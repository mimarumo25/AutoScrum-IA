"""Runtime durable de LangGraph para el pipeline SDD.

La logica de dominio sigue en orchestrator.py y taskqueue.py. Este modulo aporta
checkpoints SQLite, reanudacion, interrupt humano y un limite claro alrededor de
los efectos de cada visita. state.json se conserva como proyeccion legible para
el panel, los reportes y la compatibilidad con corridas anteriores.
"""
import asyncio
import os
import time
from pathlib import Path
from typing import Callable

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

from sdd.runtime.parallel_tasks import ParallelTasks  # noqa: E402
from sdd.runtime.human_approval import approval_record, rejected_record  # noqa: E402
from sdd.runtime.artifact_integrity import evaluation_matches  # noqa: E402
from sdd.runtime.work_unit_graph import WorkUnitGraph  # noqa: E402
from sdd.runtime.workflow_contracts import Evaluation, HumanDecision  # noqa: E402
from sdd.runtime.workflow_defects import (classify_defect, delegate_defect,
                                          escalate_defect, retry_defect)  # noqa: E402
from sdd.runtime.workflow_state import PipelineState, merge_results  # noqa: E402
from sdd.runtime.graph_state import (copy_state as _copy_state,
                                     delta as _delta,
                                     has_interrupt as _has_interrupt,
                                     normalize as _normalize,
                                     visit_id as _visit_id)  # noqa: E402
from sdd.runtime.run_state import prepare_resume  # noqa: E402


CHECKPOINT_PATH = ".agent/checkpoints.sqlite"
_merge_results = merge_results


SaveFn = Callable[[dict[str, object], Path], None]
LogFn = Callable[..., None]


def run_pipeline(state: dict[str, object], state_path: Path, args: object,
                 cfg: dict[str, object], nodes: dict[str, dict[str, object]],
                 auto_human: bool, generate_fn: Callable[..., None],
                 evaluate_fn: Callable[..., None],
                 approve_fn: Callable[..., None],
                 log_fn: LogFn, save_fn: SaveFn,
                 token_usage_fn: Callable[[str], dict[str, int]],
                 commit_fn: Callable[..., tuple[bool, str]],
                 resume_requested: bool = False) -> dict[str, object]:
    """Ejecuta o reanuda el StateGraph y devuelve su proyeccion final."""
    initial = _normalize(_copy_state(state))
    workdir = str(getattr(args, "workdir"))
    checkpoint_path = Path(workdir) / CHECKPOINT_PATH
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    def project(value: PipelineState) -> None:
        save_fn(dict(value), state_path)

    def bootstrap(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        if current.get("status") != "running":
            return _delta(value, current)
        budget = cfg["budget"]
        deadline = float(current.get("started_at", time.time())) + \
            int(budget["max_wall_minutes"]) * 60
        if time.time() >= deadline:
            current["status"] = "escalated"
            log_fn(current, "PRESUPUESTO", motivo="max_wall_minutes agotado")
        max_out = int(budget.get("max_output_tokens", 0))
        spent = token_usage_fn(workdir)["output_tokens"] if max_out else 0
        if max_out and spent >= max_out:
            current["status"] = "escalated"
            log_fn(current, "PRESUPUESTO",
                   motivo=f"max_output_tokens agotado ({spent} >= {max_out})")
        return _delta(value, current)

    agent_ids = {
        node_id for node_id, node in nodes.items()
        if node.get("type") != "human"
    }

    def choose_next(value: PipelineState) -> str:
        if value.get("status") != "running":
            return END
        cursor = str(value.get("cursor", "product"))
        return "prepare" if cursor in agent_ids else cursor

    def prepare(value: PipelineState) -> dict[str, object]:
        if value.get("active_visit"):
            return {}
        return {"active_visit": _visit_id(value)}

    def choose_agent(value: PipelineState) -> str:
        return str(value.get("cursor", "product"))

    def agent_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        setattr(args, "visit_id", current.get("active_visit"))
        try:
            generate_fn(current, args, cfg, nodes, auto_human)
        finally:
            setattr(args, "visit_id", None)
        return _delta(value, current)

    def after_generate(value: PipelineState) -> str:
        return "evaluate" if value.get("status") == "running" else "bootstrap"

    def evaluate_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        evaluate_fn(current, args, cfg, nodes, auto_human)
        current["active_visit"] = None
        evaluation = current.get("evaluation") or {}
        if evaluation.get("approved"):
            current["pending_review"] = {
                "kind": "unit", "unit_ids": [str(evaluation.get("unit_id"))],
                "node": str(evaluation.get("node")),
                "task_id": evaluation.get("task_id"),
            }
            current["cursor"] = "human_review"
        return _delta(value, current)

    def after_evaluate(value: PipelineState) -> str:
        evaluation = value.get("evaluation") or {}
        return "human_review" if evaluation.get("approved") else "classify_decision"

    parallel = ParallelTasks(
        workdir, args, cfg, nodes, auto_human, generate_fn, evaluate_fn,
        log_fn, commit_fn, _normalize, project)
    work_unit = WorkUnitGraph(
        workdir, args, cfg, nodes, auto_human, generate_fn, evaluate_fn,
        log_fn, _normalize, token_usage_fn).compile()

    def _decision_payload(current: PipelineState) -> dict[str, object]:
        action = str(getattr(args, "human_decision", None) or
                     ("accept" if auto_human else ""))
        actor = ("autonomous" if auto_human else
                 os.environ.get("SDD_APPROVAL_ACTOR", "cli"))
        feedback = str(getattr(args, "human_feedback", "") or
                       os.environ.get("SDD_APPROVAL_FEEDBACK", ""))
        return {"action": action, "actor": actor, "feedback": feedback}

    def human_review_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        if auto_human:
            decision = _decision_payload(current)
            log_fn(current, "GATE_HUMANO", accion="auto-approval explicita",
                   unidades=",".join((current.get("pending_review") or {}).get(
                       "unit_ids", [])))
        else:
            pending = current.get("pending_review") or {}
            log_fn(current, "GATE_HUMANO", accion="accept/reject con feedback",
                   unidades=",".join(pending.get("unit_ids", [])))
            decision = interrupt({
                "kind": "evaluation_approval",
                "run_id": current.get("run_id"),
                "units": pending.get("unit_ids", []),
                "evaluation": current.get("evaluation"),
                "message": "Aceptar o rechazar la evaluacion; reject requiere feedback",
            })
        if decision is True:
            decision = {"action": "accept", "actor": "cli", "feedback": ""}
        if isinstance(decision, dict) and "action" not in decision:
            decision = {
                "action": "accept" if decision.get("approved") else "reject",
                "actor": str(decision.get("actor", "cli")),
                "feedback": str(decision.get("feedback", "")),
            }
        current["status"] = "running"
        current["human_decision"] = dict(decision)
        current.setdefault("iterations", []).append({
            "unit_ids": list((current.get("pending_review") or {}).get("unit_ids", [])),
            "stage": "human_review", "decision": dict(decision),
        })
        return _delta(value, current)

    def active_unit(value: PipelineState):
        evaluation = Evaluation.model_validate(value.get("evaluation") or {})
        node = nodes[evaluation.node]
        task = next((item for item in value.get("tasks", [])
                     if str(item.get("id")) == str(evaluation.task_id)), None)
        return evaluation, node, task

    def reject_unit_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        evaluation, node, _task = active_unit(current)
        human = HumanDecision.model_validate(current.get("human_decision") or {})
        artifacts = evaluation.solution.get("artifacts") or []
        current["evaluation"] = evaluation.model_copy(update={
            "approved": False, "owner": node["id"], "gate_id": "H1",
            "feedback": human.feedback,
            "findings": [{
                "file": str(artifacts[0] if artifacts else f"agents/{node['id']}.md"),
                "line": 0, "rule": "rechazo-humano", "evidence": human.feedback,
            }],
        }).model_dump(mode="json")
        current["feedback"] = human.feedback
        return _delta(value, current)

    def classify_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        evaluation, node, task = active_unit(current)
        current["defect_decision"] = classify_defect(
            current, node, task, str(evaluation.owner or node["id"]),
            str(evaluation.gate_id or "G-EVALUATION"),
            [item.model_dump(mode="json") for item in evaluation.findings],
            cfg["budget"])
        return _delta(value, current)

    def choose_defect_route(value: PipelineState) -> str:
        return str((value.get("defect_decision") or {}).get("route", "escalate"))

    def defect_effect(value: PipelineState, operation) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        _evaluation, node, task = active_unit(current)
        operation(current, workdir, node, task, current["defect_decision"],
                  cfg["budget"], log_fn)
        current["retry_count"] = int(
            (current.get("defect_decision") or {}).get("attempt", 0))
        current["pending_review"] = None
        current["human_decision"] = None
        return _delta(value, current)

    def validate_unit_content(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        evaluation = Evaluation.model_validate(current.get("evaluation") or {})
        if evaluation_matches(evaluation.model_dump(mode="json"), workdir):
            current["content_validation"] = "valid"
            return _delta(value, current)
        feedback = "los artefactos cambiaron despues de la evaluacion; se requiere reevaluar"
        current["evaluation"] = evaluation.model_copy(update={
            "approved": False, "owner": evaluation.node,
            "gate_id": "G-CONTENT-CHANGED", "feedback": feedback,
            "findings": [{"file": str((evaluation.content_roots or ["."])[0]),
                          "line": 0, "rule": "contenido-cambio-tras-evaluacion",
                          "evidence": feedback}],
        }).model_dump(mode="json")
        current["feedback"] = feedback
        current["pending_review"] = None
        current["human_decision"] = None
        current["content_validation"] = "changed"
        return _delta(value, current)

    def choose_content_validation(value: PipelineState) -> str:
        return str(value.get("content_validation", "changed"))

    def approve_unit_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        decision = HumanDecision.model_validate(current.get("human_decision") or {})
        pending = current.get("pending_review") or {}
        approve_fn(current, args, cfg, nodes)
        record = approval_record(
            workdir, decision.actor,
            mode="automatic" if auto_human else "interactive")
        record["unit_ids"] = list(pending.get("unit_ids", []))
        current["human_approval"] = record
        current.setdefault("human_approvals", []).append(record)
        current["retry_count"] = 0
        current["feedback"] = ""
        current["pending_review"] = None
        current["human_decision"] = None
        return _delta(value, current)

    def choose_approved_route(value: PipelineState) -> str:
        return "done" if value.get("approval_next") == "done" else "continue"

    def continue_approved_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        current["cursor"] = str(current.pop("approval_next", "product"))
        return _delta(value, current)

    def complete_approved_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        current.pop("approval_next", None)
        current["status"] = "done"
        return _delta(value, current)

    def approve_parallel_node(value: PipelineState) -> dict[str, object]:
        pending = dict(value.get("pending_review") or {})
        current = parallel.approve_human(
            value, dict(value.get("human_decision") or {}))
        return _delta(value, _record_human(current, pending, approved=True))

    def reject_parallel_node(value: PipelineState) -> dict[str, object]:
        pending = dict(value.get("pending_review") or {})
        current = parallel.reject_human(
            value, dict(value.get("human_decision") or {}))
        return _delta(value, _record_human(current, pending, approved=False))

    def _record_human(current, pending, approved):
        decision = current.get("human_decision") or {}
        record = (approval_record(
            workdir, str(decision.get("actor", "human")),
            mode="automatic" if auto_human else "interactive")
            if approved else rejected_record(
                str(decision.get("actor", "human")),
                str(decision.get("feedback", ""))))
        record["unit_ids"] = list(pending.get("unit_ids", []))
        current["human_approval"] = record
        current.setdefault("human_approvals", []).append(record)
        current["human_decision"] = None
        return current

    def choose_human_route(value: PipelineState) -> str:
        pending = value.get("pending_review") or {}
        kind = str(pending.get("kind") or "unit")
        action = HumanDecision.model_validate(value.get("human_decision") or {}).action
        return f"{action}_{kind}"

    def legacy_human_node(value: PipelineState) -> dict[str, object]:
        """Migra checkpoints detenidos en el antiguo gate exclusivo del plan."""
        current = _normalize(_copy_state(value))
        current["pending_review"] = {
            "kind": "legacy", "unit_ids": ["human_gate:legacy"],
            "message": "checkpoint legado requiere decision explicita",
        }
        current["cursor"] = "human_review"
        return _delta(value, current)

    def accept_legacy_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        decision = HumanDecision.model_validate(current.get("human_decision") or {})
        current["human_approval"] = approval_record(
            workdir, decision.actor, mode="migration-interactive")
        current.setdefault("human_approvals", []).append(current["human_approval"])
        next_node = str(nodes["human_gate"]["next"])
        if next_node == "done":
            current["status"] = "done"
        else:
            current["cursor"] = next_node
        current["pending_review"] = None
        current["human_decision"] = None
        return _delta(value, current)

    def reject_legacy_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        decision = HumanDecision.model_validate(current.get("human_decision") or {})
        current["human_approval"] = rejected_record(decision.actor, decision.feedback)
        current.setdefault("human_approvals", []).append(current["human_approval"])
        current["status"] = "escalated"
        current["feedback"] = decision.feedback
        current["pending_review"] = None
        current["human_decision"] = None
        return _delta(value, current)

    builder = StateGraph(PipelineState)
    builder.add_node("bootstrap", bootstrap)
    builder.add_node("prepare", prepare)
    for node_id in sorted(agent_ids):
        builder.add_node(node_id, agent_node)
        builder.add_conditional_edges(node_id, after_generate)
    builder.add_node("evaluate", evaluate_node)
    builder.add_node("human_review", human_review_node)
    builder.add_node("approve_unit", approve_unit_node)
    builder.add_node("continue_approved", continue_approved_node)
    builder.add_node("complete_approved", complete_approved_node)
    builder.add_node("validate_unit_content", validate_unit_content)
    builder.add_node("reject_unit", reject_unit_node)
    builder.add_node("approve_parallel", approve_parallel_node)
    builder.add_node("reject_parallel", reject_parallel_node)
    builder.add_node("accept_legacy", accept_legacy_node)
    builder.add_node("reject_legacy", reject_legacy_node)
    builder.add_node("classify_decision", classify_node)
    builder.add_node("retry_unit", lambda value: defect_effect(value, retry_defect))
    builder.add_node("delegate_unit", lambda value: defect_effect(value, delegate_defect))
    builder.add_node("escalate_unit", lambda value: defect_effect(value, escalate_defect))
    builder.add_conditional_edges("evaluate", after_evaluate)
    builder.add_conditional_edges("human_review", choose_human_route, {
        "accept_unit": "validate_unit_content", "reject_unit": "reject_unit",
        "accept_parallel": "approve_parallel", "reject_parallel": "reject_parallel",
        "accept_legacy": "accept_legacy", "reject_legacy": "reject_legacy",
    })
    builder.add_conditional_edges("validate_unit_content", choose_content_validation, {
        "valid": "approve_unit", "changed": "classify_decision",
    })
    builder.add_conditional_edges("approve_unit", choose_approved_route, {
        "continue": "continue_approved", "done": "complete_approved",
    })
    builder.add_edge("reject_unit", "classify_decision")
    builder.add_conditional_edges("classify_decision", choose_defect_route, {
        "retry": "retry_unit", "delegate": "delegate_unit",
        "escalate": "escalate_unit",
    })
    for terminal_node in (
            "continue_approved", "complete_approved",
            "approve_parallel", "reject_parallel",
            "accept_legacy", "reject_legacy",
            "retry_unit", "delegate_unit", "escalate_unit"):
        builder.add_edge(terminal_node, "bootstrap")
    builder.add_node("human_gate", legacy_human_node)
    builder.add_node("task_loop", lambda value: {})
    builder.add_node("load_reconcile", lambda value: _delta(
        value, parallel.load_reconcile(value)))
    builder.add_node("select_ready", lambda value: _delta(
        value, parallel.select_ready(value)))
    builder.add_node("prepare_batch", lambda value: _delta(
        value, parallel.prepare_batch(value)))
    builder.add_node("parallel_dispatch", lambda value: {})
    builder.add_node("work_unit", work_unit)
    builder.add_node("parallel_collect", lambda value: _delta(
        value, parallel.stage_results(value)))
    builder.add_node("route_batch", lambda value: {})
    builder.add_node("defer_review", lambda value: _delta(
        value, parallel.defer_review(value)))
    builder.add_node("integrate_result", lambda value: _delta(
        value, parallel.integrate_result(value)))
    builder.add_node("delegate_result", lambda value: _delta(
        value, parallel.delegate_result(value)))
    builder.add_node("defect_result", lambda value: _delta(
        value, parallel.defect_result(value)))
    builder.add_node("escalate_result", lambda value: _delta(
        value, parallel.escalate_result(value)))
    builder.add_node("finish_batch", lambda value: _delta(
        value, parallel.finish_batch(value)))
    builder.add_edge(START, "bootstrap")
    builder.add_conditional_edges("bootstrap", choose_next)
    builder.add_conditional_edges("prepare", choose_agent)
    builder.add_edge("human_gate", "human_review")
    builder.add_edge("task_loop", "load_reconcile")
    builder.add_conditional_edges("load_reconcile", parallel.schedule_route, {
        "select": "select_ready", "terminal": "bootstrap",
    })
    builder.add_conditional_edges("select_ready", parallel.schedule_route, {
        "prepare": "prepare_batch", "terminal": "bootstrap",
    })
    builder.add_edge("prepare_batch", "bootstrap")
    builder.add_conditional_edges("parallel_dispatch", parallel.dispatch)
    builder.add_edge("work_unit", "parallel_collect")
    builder.add_edge("parallel_collect", "route_batch")
    builder.add_conditional_edges("route_batch", parallel.route_batch, {
        "review": "defer_review", "integrate": "integrate_result",
        "delegate": "delegate_result",
        "defect": "defect_result", "escalate": "escalate_result",
        "finish": "finish_batch",
    })
    for result_node in ("defer_review", "integrate_result", "delegate_result",
                        "defect_result", "escalate_result"):
        builder.add_edge(result_node, "route_batch")
    builder.add_edge("finish_batch", "bootstrap")

    async def execute_graph() -> dict[str, object]:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            config = {
                "configurable": {"thread_id": str(initial["run_id"])},
                "recursion_limit": max(
                    250, int(cfg["budget"]["max_agent_calls"]) * 8),
                "max_concurrency": max(
                    1, int(cfg["runtime"].get("max_concurrency", 3))),
            }
            before = await graph.aget_state(config)
            resume_input = None
            # El resume tecnico se reconstruye sobre SQLite, no sobre state.json.
            # Durante HITL el unico input externo es la decision humana.
            if (resume_requested and getattr(before, "values", None)
                    and not _has_interrupt(before)):
                authoritative = _normalize(_copy_state(before.values))
                previous = prepare_resume(authoritative, workdir)
                log_fn(authoritative, "REANUDADO",
                       desde=authoritative.get("cursor"), estado_previo=previous)
                recovery = authoritative.get("resume_recovery") or {}
                if recovery:
                    log_fn(authoritative, "RECUPERACION_RESTAURADA",
                           id=recovery.get("id"), para=recovery.get("owner"),
                           gate=recovery.get("gate_id"),
                           hallazgos=recovery.get("findings"))
                await graph.aupdate_state(config, authoritative)
                before = await graph.aget_state(config)
                resume_input = (None if getattr(before, "next", ())
                                else authoritative)
            if resume_requested and _has_interrupt(before):
                graph_input = Command(resume=_decision_payload(initial))
            elif resume_requested and getattr(before, "values", None):
                graph_input = resume_input
            elif getattr(before, "values", None):
                graph_input = None
            else:
                graph_input = initial
            await graph.ainvoke(graph_input, config=config, durability="sync")
            after = await graph.aget_state(config)
            if _has_interrupt(after):
                waiting = _normalize(_copy_state(after.values))
                waiting["status"] = "waiting_human"
                project(waiting)
                return dict(waiting)
            final = _normalize(_copy_state(after.values))
            project(final)
            return dict(final)

    return asyncio.run(execute_graph())

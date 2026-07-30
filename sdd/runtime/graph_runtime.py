"""Runtime durable de LangGraph para el pipeline SDD.

La logica de dominio sigue en orchestrator.py y taskqueue.py. Este modulo aporta
checkpoints SQLite, reanudacion, interrupt humano y un limite claro alrededor de
los efectos de cada visita. state.json se conserva como proyeccion legible para
el panel, los reportes y la compatibilidad con corridas anteriores.
"""
import asyncio
import copy
import hashlib
import os
import time
from pathlib import Path
from typing import Annotated, Callable, TypedDict

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

from sdd.runtime.parallel_tasks import ParallelTasks  # noqa: E402
from sdd.runtime.human_approval import (approval_record, rejected_record, spec_hash,
                                        waiting_projection)  # noqa: E402


CHECKPOINT_PATH = ".agent/checkpoints.sqlite"
_RESET_RESULTS = "__sdd_reset__"


def _merge_results(left: dict[str, object],
                   right: dict[str, object]) -> dict[str, object]:
    """Acumula ramas del superstep y permite liberar el lote ya colectado."""
    if right.get(_RESET_RESULTS) is True:
        return {key: value for key, value in right.items()
                if key != _RESET_RESULTS}
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    run_id: str
    cursor: str
    status: str
    attempts: dict[str, int]
    agent_calls: int
    started_at: float
    original_started_at: float
    resume_started_at: float
    resume_history: list[dict[str, object]]
    resume_checkpoint: dict[str, object]
    resume_recovery: dict[str, object] | None
    tasks: list[dict[str, object]]
    current_task: str | None
    defect_seq: int
    history: list[dict[str, object]]
    resume_at: str | None
    resume_stack: list[str]
    recoveries: list[dict[str, object]]
    recovery_seq: int
    active_visit: str | None
    human_approval: dict[str, object]
    engine: str
    checkpoint_db: str
    batch_seq: int
    parallel_batch: dict[str, object] | None
    parallel_results: Annotated[dict[str, object], _merge_results]
    worker_task_id: str | None


SaveFn = Callable[[dict[str, object], Path], None]
LogFn = Callable[..., None]


def _copy_state(state: dict[str, object]) -> PipelineState:
    return copy.deepcopy(dict(state))


def _delta(before: PipelineState, after: PipelineState) -> dict[str, object]:
    """Devuelve solo canales modificados para no reserializar todo el estado."""
    changed: dict[str, object] = {}
    for key, value in after.items():
        if before.get(key) == value:
            continue
        if key == "parallel_results" and not value and before.get(key):
            changed[key] = {_RESET_RESULTS: True}
        else:
            changed[key] = value
    return changed


def _normalize(state: PipelineState) -> PipelineState:
    state.setdefault("tasks", [])
    state.setdefault("history", [])
    state.setdefault("attempts", {})
    state.setdefault("current_task", None)
    state.setdefault("resume_at", None)
    state.setdefault("resume_stack", [])
    state.setdefault("recoveries", [])
    state.setdefault("recovery_seq", 0)
    state.setdefault("active_visit", None)
    state.setdefault("batch_seq", 0)
    state.setdefault("parallel_batch", None)
    state.setdefault("parallel_results", {})
    state.setdefault("worker_task_id", None)
    state["engine"] = "langgraph"
    state["checkpoint_db"] = CHECKPOINT_PATH
    return state


def _visit_id(state: PipelineState) -> str:
    identity = ":".join((
        str(state.get("run_id", "run")),
        str(state.get("cursor", "node")),
        str(state.get("current_task") or "linear"),
        str(state.get("agent_calls", 0)),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _has_interrupt(snapshot: object) -> bool:
    return bool(getattr(snapshot, "interrupts", ()))


def run_pipeline(state: dict[str, object], state_path: Path, args: object,
                 cfg: dict[str, object], nodes: dict[str, dict[str, object]],
                 auto_human: bool, step_fn: Callable[..., None],
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
            project(current)
            return _delta(value, current)
        budget = cfg["budget"]
        deadline = float(current.get("started_at", time.time())) + \
            int(budget["max_wall_minutes"]) * 60
        if time.time() > deadline:
            current["status"] = "escalated"
            log_fn(current, "PRESUPUESTO", motivo="max_wall_minutes agotado")
        max_out = int(budget.get("max_output_tokens", 0))
        spent = token_usage_fn(workdir)["output_tokens"] if max_out else 0
        if max_out and spent > max_out:
            current["status"] = "escalated"
            log_fn(current, "PRESUPUESTO",
                   motivo=f"max_output_tokens agotado ({spent} > {max_out})")
        project(current)
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
            step_fn(current, args, cfg, nodes, auto_human)
        finally:
            setattr(args, "visit_id", None)
        current["active_visit"] = None
        if "resume_at" not in current:
            current["resume_at"] = None
        project(current)
        return _delta(value, current)

    parallel = ParallelTasks(
        workdir, args, cfg, nodes, auto_human, step_fn,
        log_fn, commit_fn, _normalize, project)

    def human_node(value: PipelineState) -> dict[str, object]:
        current = _normalize(_copy_state(value))
        signed = current.get("human_approval")
        if isinstance(signed, dict) and signed.get("approved"):
            current["status"] = "running"
            current["cursor"] = str(nodes["human_gate"]["next"])
            project(current)
            return _delta(value, current)

        if auto_human:
            step_fn(current, args, cfg, nodes, auto_human)
            current["human_approval"] = approval_record(
                workdir, "autonomous", mode="automatic")
            project(current)
            return _delta(value, current)

        waiting = _copy_state(current)
        waiting["status"] = "waiting_human"
        log_fn(waiting, "GATE_HUMANO",
               accion="revisar spec/ y spec/30_plan/tasks.yaml; luego reanudar")
        project(waiting)
        decision = interrupt({
            "kind": "human_approval",
            "run_id": current.get("run_id"),
            "spec_hash": spec_hash(workdir),
            "message": "Aprobar la especificacion y el plan antes de escribir codigo",
        })
        approved = decision is True or (
            isinstance(decision, dict) and decision.get("approved") is True)
        actor = (str(decision.get("actor", "cli"))
                 if isinstance(decision, dict) else "cli")
        current = waiting
        # Al reanudar un interrupt, Command.resume debe consumir primero la
        # interrupción original. Superponemos después la proyección preparada
        # (presupuesto nuevo, historial y recuperaciones), antes del bootstrap.
        if approved and resume_requested:
            current.update(_normalize(_copy_state(initial)))
        if not approved:
            current["status"] = "escalated"
            current["human_approval"] = rejected_record(actor)
            log_fn(current, "ESCALATE_HUMAN", motivo="plan rechazado por el humano")
        else:
            current["status"] = "running"
            current["cursor"] = str(nodes["human_gate"]["next"])
            current["human_approval"] = approval_record(workdir, actor)
            log_fn(current, "APROBADO", nodo="human_gate", accion="firma",
                   detalle=current["human_approval"]["spec_hash"])
        project(current)
        return _delta(value, current)

    def schedule_node(value: PipelineState) -> dict[str, object]:
        return _delta(value, parallel.schedule(value))

    def collect_node(value: PipelineState) -> dict[str, object]:
        return _delta(value, parallel.collect(value))

    builder = StateGraph(PipelineState)
    builder.add_node("bootstrap", bootstrap)
    builder.add_node("prepare", prepare)
    for node_id in sorted(agent_ids):
        builder.add_node(node_id, agent_node)
        builder.add_edge(node_id, "bootstrap")
    builder.add_node("human_gate", human_node)
    builder.add_node("task_loop", schedule_node)
    builder.add_node("parallel_dispatch", lambda value: {})
    builder.add_node("parallel_worker", parallel.worker)
    builder.add_node("parallel_collect", collect_node)
    builder.add_edge(START, "bootstrap")
    builder.add_conditional_edges("bootstrap", choose_next)
    builder.add_conditional_edges("prepare", choose_agent)
    builder.add_edge("human_gate", "bootstrap")
    builder.add_edge("task_loop", "bootstrap")
    builder.add_conditional_edges("parallel_dispatch", parallel.dispatch)
    builder.add_edge("parallel_worker", "parallel_collect")
    builder.add_edge("parallel_collect", "bootstrap")

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
            # Fuera de interrupt(), LangGraph no mezcla por sí solo la
            # proyección preparada con el snapshot. La persistimos antes de
            # continuar. En interrupt(), hacerlo aquí recrearía la espera; el
            # human_node aplica la misma superposición tras Command.resume.
            if (resume_requested and getattr(before, "values", None)
                    and not _has_interrupt(before)):
                await graph.aupdate_state(config, initial)
                before = await graph.aget_state(config)
            if resume_requested and _has_interrupt(before):
                actor = "autonomous" if auto_human else os.environ.get(
                    "SDD_APPROVAL_ACTOR", "cli")
                graph_input = Command(resume={"approved": True, "actor": actor})
            elif resume_requested and getattr(before, "next", ()):
                graph_input = None
            else:
                graph_input = initial
            await graph.ainvoke(graph_input, config=config, durability="async")
            after = await graph.aget_state(config)
            if _has_interrupt(after):
                return dict(_normalize(waiting_projection(state_path, initial)))
            final = _normalize(_copy_state(after.values))
            project(final)
            return dict(final)

    return asyncio.run(execute_graph())

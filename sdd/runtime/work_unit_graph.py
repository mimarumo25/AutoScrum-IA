"""Subgrafo LangGraph ejecutado por cada rama creada con Send."""
import copy
import time

from langgraph.graph import END, START, StateGraph

from sdd.core import chronicle, lifecycle, metrics
from sdd.runtime import optimized_gates, task_worktrees, taskqueue
from sdd.runtime.workflow_contracts import Evaluation
from sdd.runtime.artifact_integrity import allowed_roots, content_hash
from sdd.runtime.workflow_defects import (classify_defect, delegate_defect,
                                          escalate_defect, retry_defect)
from sdd.runtime.workflow_state import PipelineState, WorkUnitOutput


class WorkUnitGraph:
    """Nodos de una unidad; el grafo, no Python, gobierna sus rondas."""

    def __init__(self, workdir, args, cfg, nodes, auto_human, generate_fn,
                 evaluate_fn, log_fn, normalize_fn, token_usage_fn):
        self.workdir = workdir
        self.args = args
        self.cfg = cfg
        self.nodes = nodes
        self.auto_human = auto_human
        self.generate_fn = generate_fn
        self.evaluate_fn = evaluate_fn
        self.log_fn = log_fn
        self.normalize = normalize_fn
        self.token_usage = token_usage_fn

    def prepare(self, value: PipelineState) -> dict[str, object]:
        current = self.normalize(copy.deepcopy(dict(value)))
        task_id = str(current.get("worker_task_id"))
        batch = current.get("parallel_batch") or {}
        batch_id = str(batch.get("id"))
        if not current.get("work_unit_started"):
            source = taskqueue.by_id(current["tasks"], task_id)
            if source is None:
                return {"work_unit_started": True,
                        "work_unit_error": f"{batch_id}: no existe la tarea {task_id}"}
            task = copy.deepcopy(source)
            workspace = task.get("workspace")
            if not isinstance(workspace, dict):
                return {"work_unit_started": True,
                        "work_unit_error": f"{task_id}: worktree no preparado"}
            ceiling = int(self.cfg["budget"]["max_agent_calls"])
            quota = max(0, int(batch.get("agent_quota", 0)))
            baseline = ceiling - quota
            retry_key = f"{task_id}:H1"
            taskqueue.publish_current(str(workspace["path"]), task)
            lifecycle.started(self.workdir, task_id, str(task["node"]),
                              workspace=str(workspace["path"]), batch_id=batch_id)
            current.update(
                tasks=[task], history=[], attempts={}, gate_refunds={},
                agent_calls=baseline,
                defect_seq=0, current_task=task_id, cursor=str(task["node"]),
                status="running" if quota else "escalated", iterations=[],
                generation=None, evaluation=None, pending_review=None,
                human_decision=None, defect_decision=None,
                feedback=str(task.get("evaluation_feedback") or ""),
                retry_count=int(value.get("attempts", {}).get(retry_key, 0)),
                work_unit_started=True,
                work_unit_error="" if quota else "presupuesto de agente agotado",
            )
        current["active_visit"] = (
            f"{batch_id}-{task_id}-{int(current.get('agent_calls', 0))}")
        return current

    def budget_check(self, value: PipelineState) -> dict[str, object]:
        """Impide entrar a generate cuando cualquier limite ya fue alcanzado."""
        if value.get("work_unit_error"):
            return {}
        current = self.normalize(copy.deepcopy(dict(value)))
        budget = self.cfg["budget"]
        batch = current.get("parallel_batch") or {}
        ceiling = int(budget["max_agent_calls"])
        quota = int(batch.get("agent_quota", 0))
        baseline = ceiling - quota
        used = max(0, int(current.get("agent_calls", baseline)) - baseline)
        reason = ""
        wall = int(budget.get("max_wall_minutes", 0))
        if wall and time.time() >= float(current.get("started_at", time.time())) + wall * 60:
            reason = "max_wall_minutes agotado"
        workspace = current["tasks"][0]["workspace"]
        max_output = int(budget.get("max_output_tokens", 0))
        output = self.token_usage(str(workspace["path"]))["output_tokens"]
        if not reason and max_output and output >= max_output:
            reason = f"max_output_tokens agotado ({output} >= {max_output})"
        if not reason and used >= quota:
            reason = "agent quota agotada"
        if reason:
            current["work_unit_error"] = reason
            self.log_fn(current, "PRESUPUESTO", motivo=reason)
        return current

    @staticmethod
    def after_budget(value: PipelineState) -> str:
        return "blocked" if value.get("work_unit_error") else "generate"

    def generate(self, value: PipelineState) -> dict[str, object]:
        if value.get("work_unit_error"):
            return {}
        current = self.normalize(copy.deepcopy(dict(value)))
        workspace = current["tasks"][0]["workspace"]
        worker_args = copy.copy(self.args)
        setattr(worker_args, "workdir", str(workspace["path"]))
        setattr(worker_args, "visit_id", current.get("active_visit"))
        try:
            self.generate_fn(current, worker_args, self.cfg, self.nodes,
                             self.auto_human)
        except Exception as error:  # noqa: BLE001 - la rama escala fail-closed
            current["work_unit_error"] = f"{type(error).__name__}: {error}"[:300]
        return current

    def evaluate(self, value: PipelineState) -> dict[str, object]:
        if value.get("work_unit_error"):
            return {}
        current = self.normalize(copy.deepcopy(dict(value)))
        workspace = current["tasks"][0]["workspace"]
        worker_args = copy.copy(self.args)
        setattr(worker_args, "workdir", str(workspace["path"]))
        setattr(worker_args, "visit_id", current.get("active_visit"))
        try:
            self.evaluate_fn(current, worker_args, self.cfg, self.nodes,
                             self.auto_human)
        except Exception as error:  # noqa: BLE001 - la rama escala fail-closed
            current["work_unit_error"] = f"{type(error).__name__}: {error}"[:300]
        current["active_visit"] = None
        return current

    def route(self, value: PipelineState) -> dict[str, object]:
        current = self.normalize(copy.deepcopy(dict(value)))
        task = current["tasks"][0]
        if current.get("work_unit_error"):
            finding = {
                "file": f"agents/{task['node']}.md", "line": 0,
                "rule": "worker-excepcion", "evidence": current["work_unit_error"],
            }
            decision = classify_defect(
                current, self.nodes[str(task["node"])], task, str(task["node"]),
                "G-WORKER", [finding], {**self.cfg["budget"],
                                          "max_retries_per_gate": -1})
            decision["route"] = "escalate"
            decision["project_escalation"] = False
            decision["reason"] = current["work_unit_error"]
            current["defect_decision"] = decision
            return current
        evaluation = Evaluation.model_validate(current.get("evaluation") or {})
        if evaluation.approved:
            current["defect_decision"] = None
            return current
        current["defect_decision"] = classify_defect(
            current, self.nodes[str(task["node"])], task,
            str(evaluation.owner or task["node"]),
            str(evaluation.gate_id or "G-EVALUATION"),
            [item.model_dump(mode="json") for item in evaluation.findings],
            self.cfg["budget"])
        return current

    @staticmethod
    def choose_route(value: PipelineState) -> str:
        evaluation = value.get("evaluation") or {}
        if evaluation.get("approved") and not value.get("work_unit_error"):
            return "approved"
        return str((value.get("defect_decision") or {}).get("route", "escalate"))

    def _effect(self, value, operation):
        current = self.normalize(copy.deepcopy(dict(value)))
        task = current["tasks"][0]
        node = self.nodes[str(task["node"])]
        operation(current, str(task["workspace"]["path"]), node, task,
                  current["defect_decision"], self.cfg["budget"], self.log_fn)
        return current

    def retry(self, value: PipelineState) -> dict[str, object]:
        current = self._effect(value, retry_defect)
        current["retry_count"] = int(current["defect_decision"]["attempt"])
        current["generation"] = None
        current["evaluation"] = None
        return current

    def delegate(self, value: PipelineState) -> dict[str, object]:
        return self._effect(value, delegate_defect)

    def escalate(self, value: PipelineState) -> dict[str, object]:
        return self._effect(value, escalate_defect)

    def finalize(self, value: PipelineState) -> dict[str, object]:
        current = self.normalize(copy.deepcopy(dict(value)))
        task_id = str(current.get("worker_task_id"))
        batch_id = str((current.get("parallel_batch") or {}).get("id"))
        task = (taskqueue.by_id(current.get("tasks", []), task_id)
                or (current.get("tasks") or [{"id": task_id, "node": "unknown"}])[0])
        workspace = task.get("workspace")
        if isinstance(workspace, dict):
            node = self.nodes.get(str(task.get("node")), {})
            roots = allowed_roots(node, task)
            task_worktrees.preserve(task, roots)
            evaluation = current.get("evaluation")
            if isinstance(evaluation, dict):
                evaluation["content_roots"] = roots
                evaluation["content_hash"] = content_hash(workspace["path"], roots)
            metrics.transfer(str(workspace["path"]), self.workdir)
            chronicle.transfer(str(workspace["path"]), self.workdir)
            # Sin esto el historial de gates de dev_*/qa muere con el worktree, y
            # G9 es precisamente el gate cuyo historial hace falta conservar.
            optimized_gates.transfer_history(str(workspace["path"]), self.workdir)
        defect = next((item for item in current.get("tasks", [])
                       if item.get("kind") == "defect"), None)
        evaluation = current.get("evaluation") or {}
        if evaluation.get("approved") and not current.get("work_unit_error"):
            outcome = "awaiting_human"
        elif defect is not None:
            outcome = "blocked"
        else:
            outcome = "escalated"
        ceiling = int(self.cfg["budget"]["max_agent_calls"])
        quota = int((current.get("parallel_batch") or {}).get("agent_quota", 0))
        baseline = ceiling - quota
        result = {
            "batch_id": batch_id, "task_id": task_id, "outcome": outcome,
            "task": task, "defect": defect, "history": current.get("history", []),
            "attempts": current.get("attempts", {}),
            "gate_refunds": current.get("gate_refunds", {}),
            "agent_calls": max(0, int(current.get("agent_calls", baseline)) - baseline),
            "status": current.get("status", "escalated"),
            "crash": str(current.get("work_unit_error") or ""),
            "evaluation": current.get("evaluation"),
            "generation": current.get("generation"),
            "iterations": current.get("iterations", []),
        }
        return {"parallel_results": {f"{batch_id}:{task_id}": result}}

    def compile(self):
        builder = StateGraph(PipelineState, output_schema=WorkUnitOutput)
        builder.add_node("prepare", self.prepare)
        builder.add_node("budget_check", self.budget_check)
        builder.add_node("generate", self.generate)
        builder.add_node("evaluate", self.evaluate)
        builder.add_node("route", self.route)
        builder.add_node("retry", self.retry)
        builder.add_node("delegate", self.delegate)
        builder.add_node("escalate", self.escalate)
        builder.add_node("finalize", self.finalize)
        builder.add_edge(START, "prepare")
        builder.add_edge("prepare", "budget_check")
        builder.add_conditional_edges("budget_check", self.after_budget, {
            "generate": "generate", "blocked": "route",
        })
        builder.add_edge("generate", "evaluate")
        builder.add_edge("evaluate", "route")
        builder.add_conditional_edges("route", self.choose_route, {
            "approved": "finalize", "retry": "retry",
            "delegate": "delegate", "escalate": "escalate",
        })
        builder.add_edge("retry", "prepare")
        builder.add_edge("delegate", "finalize")
        builder.add_edge("escalate", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()

"""Nodos LangGraph del sprint aislado y paralelo."""
import copy
import os

from langgraph.types import Send

from sdd.core import lifecycle
from sdd.runtime import plan_analysis, scrum, task_worktrees, taskqueue
from sdd.runtime.artifact_integrity import allowed_roots, evaluation_matches


def _scrum_complete(system: str, user: str, workdir: str) -> str:
    """Usa opcionalmente un modelo rapido sin contaminar llamadas posteriores."""
    from sdd.integrations import providers
    keys = ("SDD_MODEL", "SDD_METRICS_OPERATION", "SDD_METRICS_NODE",
            "SDD_METRICS_TASK", "SDD_METRICS_WORKDIR")
    previous = {key: os.environ.get(key) for key in keys}
    review_model = os.environ.get("SDD_REVIEW_MODEL")
    try:
        if review_model:
            os.environ["SDD_MODEL"] = review_model
        os.environ["SDD_METRICS_OPERATION"] = "scrum_llm"
        os.environ["SDD_METRICS_NODE"] = "scrum"
        os.environ["SDD_METRICS_TASK"] = ""
        os.environ["SDD_METRICS_WORKDIR"] = workdir
        return providers.complete(system, user)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ParallelTasks:
    """Scheduler, workers y colector; el grafo solo conecta sus nodos."""

    def __init__(self, workdir, args, cfg, nodes, auto_human, generate_fn,
                 evaluate_fn, log_fn, commit_fn, normalize_fn, project_fn):
        self.workdir = workdir
        self.args = args
        self.cfg = cfg
        self.nodes = nodes
        self.auto_human = auto_human
        self.generate_fn = generate_fn
        self.evaluate_fn = evaluate_fn
        self.log_fn = log_fn
        self.commit_fn = commit_fn
        self.normalize = normalize_fn
        self.project = project_fn

    def load_reconcile(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        reconciled = set(taskqueue.reconcile_completed_defects(current["tasks"]))
        for task in current["tasks"]:
            if str(task["id"]) in reconciled:
                task_worktrees.cleanup(self.workdir, task)
        if not current["tasks"]:
            try:
                current["tasks"] = taskqueue.load_plan(self.workdir)
            except taskqueue.PlanError as error:
                current["status"] = "escalated"
                current["schedule_route"] = "terminal"
                self.log_fn(current, "ESCALATE_HUMAN", motivo=str(error))
                return current
            _, total = taskqueue.progress(current["tasks"])
            self.log_fn(current, "PLAN", tareas=total,
                        nodos=len({task["node"] for task in current["tasks"]}))
        current["schedule_route"] = "select"
        return current

    def select_ready(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        if current.get("status") != "running":
            current["schedule_route"] = "terminal"
            return current
        ready = taskqueue.runnable(current["tasks"])
        if not ready:
            pending = taskqueue.pending(current["tasks"])
            if not pending:
                current["status"] = "done"
                taskqueue.clear_current(self.workdir)
            else:
                # Interbloqueo del sprint, no pausa: 'waiting_human' devolveria 0 y
                # reportaria como exito una corrida que no puede avanzar.
                current["status"] = "escalated"
                taskqueue.clear_current(self.workdir)
                blocked = ", ".join(
                    f"{task['id']}({task['status']})" for task in pending[:6])
                self.log_fn(current, "RAMAS_EN_ESPERA", pendientes=len(pending),
                            motivo=f"no hay tareas ejecutables; esperan correcciones: {blocked}")
                self.log_fn(current, "ESCALATE_HUMAN", pendientes=len(pending),
                            motivo="ninguna rama puede avanzar sin intervencion")
            current["schedule_route"] = "terminal"
            return current

        slots = max(1, int(self.cfg["runtime"].get("max_concurrency", 3)))
        simulated = bool(os.environ.get("SDD_SIMULATE"))
        ready = scrum.prioritize(
            ready, critical_frs=scrum.read_critical_frs(self.workdir),
            slots=slots, simulate=simulated,
            unlocks=plan_analysis.descendants(current["tasks"]),
            complete_fn=None if simulated else lambda system, user: _scrum_complete(
                system, user, str(self.workdir)),
            log_fn=lambda event, **fields: self.log_fn(current, event, **fields))
        batch = task_worktrees.safe_batch(ready, self.nodes, slots)
        current["ready_task_ids"] = [str(task["id"]) for task in batch]
        current["schedule_route"] = "prepare"
        return current

    def prepare_batch(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        selected = set(current.get("ready_task_ids", []))
        batch = [task for task in current["tasks"] if str(task["id"]) in selected]
        if not batch:
            current["status"] = "escalated"
            current["schedule_route"] = "terminal"
            self.log_fn(current, "ESCALATE_HUMAN",
                        motivo="seleccion de batch vacia o stale")
            return current
        current["batch_seq"] = int(current.get("batch_seq", 0)) + 1
        batch_id = f"B-{current['batch_seq']:04d}"
        try:
            for task in batch:
                task["workspace"] = task_worktrees.prepare(
                    self.workdir, str(current["run_id"]), task)
        except RuntimeError as error:
            current["status"] = "escalated"
            current["schedule_route"] = "terminal"
            self.log_fn(current, "ESCALATE_HUMAN", motivo=str(error))
            return current
        current["parallel_batch"] = {
            "id": batch_id,
            "task_ids": [str(task["id"]) for task in batch],
        }
        current["current_task"] = str(batch[0]["id"]) if len(batch) == 1 else None
        current["cursor"] = "parallel_dispatch"
        current["schedule_route"] = "dispatch"
        current["ready_task_ids"] = []
        done, total = taskqueue.progress(current["tasks"])
        self.log_fn(current, "BATCH", id=batch_id, tareas=len(batch),
                    avance=f"{done}/{total}",
                    ids=",".join(str(task["id"]) for task in batch))
        return current

    @staticmethod
    def schedule_route(value):
        return str(value.get("schedule_route", "terminal"))

    def dispatch(self, value):
        batch = value.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        task_ids = list(batch.get("task_ids", []))
        ceiling = int(self.cfg["budget"]["max_agent_calls"])
        remaining = max(0, ceiling - int(value.get("agent_calls", 0)))
        base, extra = divmod(remaining, max(1, len(task_ids)))
        return [Send("work_unit", {
            **copy.deepcopy(dict(value)),
            "worker_task_id": task_id,
            "parallel_batch": {"id": batch_id, "task_ids": [task_id],
                               "agent_quota": base + (index < extra)},
            "work_unit_started": False,
            "work_unit_error": "",
        }) for index, task_id in enumerate(task_ids)]

    def stage_results(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        batch = current.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        keys = [f"{batch_id}:{task_id}" for task_id in batch.get("task_ids", [])]
        results = current.get("parallel_results", {})
        for key in sorted(keys):
            result = results.get(key)
            if result is None:
                raise RuntimeError(f"resultado de worker ausente: {key}")
            if not result.get("collected"):
                self._stage_review(current, result)
        current.update(worker_task_id=None, current_task=None)
        current["collect_queue"] = sorted(keys)
        current["review_result_keys"] = []
        return current

    @staticmethod
    def route_batch(value):
        queue = value.get("collect_queue") or []
        if not queue:
            return "finish"
        result = (value.get("parallel_results") or {}).get(str(queue[0])) or {}
        return {
            "awaiting_human": "review",
            "done": "integrate",
            "blocked": "defect",
        }.get(str(result.get("outcome")), "escalate")

    def _consume_result(self, value, route):
        current = self.normalize(copy.deepcopy(dict(value)))
        key = str((current.get("collect_queue") or [""])[0])
        result = (current.get("parallel_results") or {}).get(key)
        if result is None:
            raise RuntimeError(f"resultado de worker ausente: {key}")
        if route == "review":
            current.setdefault("review_result_keys", []).append(key)
        else:
            self._collect_one(current, result)
        current["collect_queue"] = list(current.get("collect_queue") or [])[1:]
        return current

    def defer_review(self, value):
        return self._consume_result(value, "review")

    def integrate_result(self, value):
        return self._consume_result(value, "integrate")

    def defect_result(self, value):
        return self._consume_result(value, "defect")

    def escalate_result(self, value):
        return self._consume_result(value, "escalate")

    def finish_batch(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        results = current.get("parallel_results", {})
        awaiting = [results[key] for key in current.get("review_result_keys", [])]
        batch_id = str((current.get("parallel_batch") or {}).get("id", ""))
        if awaiting and current["status"] == "running":
            current["pending_review"] = {
                "kind": "parallel", "batch_id": batch_id,
                "unit_ids": [str(item["evaluation"]["unit_id"]) for item in awaiting],
                "task_ids": [str(item["task_id"]) for item in awaiting],
                "evaluations": [item["evaluation"] for item in awaiting],
            }
            current["cursor"] = "human_review"
        else:
            current["parallel_batch"] = None
            current["parallel_results"] = {}
        if current["status"] == "running" and not awaiting:
            current["cursor"] = "task_loop"
        current["collect_queue"] = []
        current["review_result_keys"] = []
        return current

    def _collect_one(self, current, result):
        task_id = str(result["task_id"])
        task = taskqueue.by_id(current["tasks"], task_id)
        if task is None:
            raise RuntimeError(f"tarea ausente al colectar: {task_id}")
        if not result.get("collected"):
            self._stage_review(current, result)

        if result["outcome"] == "done":
            node = self.nodes[str(task["node"])]
            status, detail = task_worktrees.integrate(
                self.workdir, task, allowed_roots(node, task),
                taskqueue.commit_message(str(task["node"]), task), self.commit_fn)
            if status == "error":
                taskqueue.mark_needs_input(
                    current["tasks"], task_id,
                    f"no se pudo integrar la correccion: {detail}", "integration")
                self.log_fn(current, "RAMA_EN_ESPERA", tarea=task_id,
                            nodo=task.get("node", ""), gate="integration",
                            motivo=detail)
                task_worktrees.cleanup(self.workdir, task)
                return True
            lifecycle.integrated(self.workdir, task_id, status, detail)
            done_before = {
                str(item["id"]) for item in current["tasks"]
                if item.get("status") == "done"
            }
            waiting_before = [
                str(item["id"]) for item in current["tasks"]
                if item.get("blocked_by") == task_id
            ]
            taskqueue.mark_done(current["tasks"], task_id, self.workdir)
            completed = [
                item for item in current["tasks"]
                if item.get("status") == "done"
                and str(item["id"]) not in done_before
            ]
            for item in completed:
                task_worktrees.cleanup(self.workdir, item)
            self.log_fn(current, "INTEGRADO", tarea=task_id,
                        accion="commit" if status == "committed" else "sin-commit",
                        detalle=detail)
            for waiting_id in waiting_before:
                waiting = taskqueue.by_id(current["tasks"], waiting_id)
                if waiting is not None and waiting.get("status") == "pending":
                    self.log_fn(current, "CORRECCION_RECIBIDA", de=task_id,
                                reanuda=waiting_id)
            return True
        if result["outcome"] == "blocked":
            defect = result.get("defect") or {}
            limit = int(self.cfg["budget"].get("max_defect_tasks", 12))
            if current["defect_seq"] >= limit:
                reason = f"la rama alcanzo el tope de {limit} correcciones"
                taskqueue.mark_needs_input(
                    current["tasks"], task_id, reason,
                    str(defect.get("gate_id", "")))
                self.log_fn(current, "RAMA_EN_ESPERA", tarea=task_id,
                            nodo=task.get("node", ""),
                            gate=str(defect.get("gate_id", "")), motivo=reason)
                task_worktrees.cleanup(self.workdir, task)
                return True
            current["defect_seq"] += 1
            created = taskqueue.make_defect(
                current["tasks"], str(defect.get("node")),
                str(defect.get("gate_id")), defect.get("findings", []),
                task, current["defect_seq"], self.workdir)
            provisional_id = str(defect.get("id", ""))
            if provisional_id != created["id"]:
                current["history"] = [
                    event for event in current["history"]
                    if not (event.get("event") == "DEFECTO_TAREA"
                            and event.get("bloquea") == task_id)
                ]
                self.log_fn(current, "DEFECTO_TAREA", id=created["id"],
                            para=created["node"], bloquea=task_id,
                            gate=created["gate_id"])
            lifecycle.blocked(self.workdir, task_id, created["id"],
                              str(created.get("gate_id", "")),
                              defect.get("findings", []))
            return True
        crash = str(result.get("crash") or "")
        reason = (f"la tarea {task_id} aborto con una excepcion: {crash}" if crash
                  else f"la tarea {task_id} no pudo converger de forma automatica")
        taskqueue.mark_needs_input(current["tasks"], task_id, reason)
        self.log_fn(current, "RAMA_EN_ESPERA", tarea=task_id,
                    nodo=task.get("node", ""), motivo=reason)
        task_worktrees.cleanup(self.workdir, task)
        return True

    def _stage_review(self, current, result):
        """Fusiona una rama una sola vez, sin integrar su solucion todavia."""
        task = taskqueue.by_id(current["tasks"], str(result["task_id"]))
        if task is None:
            raise RuntimeError(f"tarea ausente al preparar revision: {result['task_id']}")
        task["workspace"] = result["task"].get("workspace")
        task["evaluation"] = result.get("evaluation")
        current["agent_calls"] += int(result.get("agent_calls", 0))
        current["history"].extend(result.get("history", []))
        current.setdefault("iterations", []).extend(result.get("iterations", []))
        for attempt, count in result.get("attempts", {}).items():
            current["attempts"][attempt] = \
                current["attempts"].get(attempt, 0) + int(count)
        result["collected"] = True

    def approve_human(self, value, decision):
        """Integra todas las unidades aceptadas por la arista HITL."""
        current = self.normalize(copy.deepcopy(dict(value)))
        batch = current.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        pending = current.get("pending_review") or {}
        task_ids = [str(item) for item in pending.get("task_ids", [])]
        results = current.get("parallel_results", {})
        actor = str(decision.get("actor") or "human")
        for task_id in task_ids:
            task = taskqueue.by_id(current["tasks"], task_id)
            result = results.get(f"{batch_id}:{task_id}")
            if task is None or result is None:
                raise RuntimeError(f"revision durable incompleta para {task_id}")
            evaluation = result.get("evaluation") or {}
            workspace = (result.get("task") or {}).get("workspace") or {}
            if not evaluation_matches(evaluation, str(workspace.get("path", ""))):
                feedback = ("los artefactos cambiaron despues de la evaluacion; "
                            "la rama debe reevaluarse")
                task["status"] = "pending"
                task["evaluation_feedback"] = feedback
                task["findings"] = [{
                    "file": str((evaluation.get("content_roots") or ["."])[0]),
                    "line": 0, "rule": "contenido-cambio-tras-evaluacion",
                    "evidence": feedback,
                }]
                task_worktrees.cleanup(self.workdir, task)
                self.log_fn(current, "REEVALUACION_REQUERIDA", tarea=task_id,
                            motivo=feedback)
                continue
            result["outcome"] = "done"
            self._collect_one(current, result)
            self.log_fn(current, "APROBACION_HUMANA", unidad=task_id,
                        actor=actor, decision="accept")
        return self._finish_human_batch(current)

    def reject_human(self, value, decision):
        """Devuelve unidades rechazadas sin mezclar el efecto de aprobacion."""
        current = self.normalize(copy.deepcopy(dict(value)))
        batch = current.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        pending = current.get("pending_review") or {}
        task_ids = [str(item) for item in pending.get("task_ids", [])]
        results = current.get("parallel_results", {})
        feedback = str(decision.get("feedback") or "")
        actor = str(decision.get("actor") or "human")
        for task_id in task_ids:
            task = taskqueue.by_id(current["tasks"], task_id)
            result = results.get(f"{batch_id}:{task_id}")
            if task is None or result is None:
                raise RuntimeError(f"revision durable incompleta para {task_id}")
            key = f"{task_id}:H1"
            current["attempts"][key] = current["attempts"].get(key, 0) + 1
            current["retry_count"] = current["attempts"][key]
            task["evaluation_feedback"] = feedback
            task["findings"] = [{"file": f"agents/{task['node']}.md", "line": 0,
                                  "rule": "rechazo-humano", "evidence": feedback}]
            limit = int(self.cfg["budget"]["max_retries_per_gate"])
            if current["attempts"][key] > limit:
                taskqueue.mark_needs_input(current["tasks"], task_id,
                                           "rechazo humano agoto reintentos", "H1")
                self.log_fn(current, "RAMA_EN_ESPERA", tarea=task_id,
                            nodo=task["node"], gate="H1",
                            motivo="rechazo humano agoto reintentos")
            else:
                task["status"] = "pending"
                self.log_fn(current, "ENRUTADO", a=task["node"],
                            intento=current["attempts"][key], reanuda_en=task_id)
            task_worktrees.cleanup(self.workdir, task)
            self.log_fn(current, "APROBACION_HUMANA", unidad=task_id,
                        actor=actor, decision="reject", feedback=feedback)
        return self._finish_human_batch(current)

    def _finish_human_batch(self, current):
        current["pending_review"] = None
        current["parallel_batch"] = None
        current["parallel_results"] = {}
        current["cursor"] = "task_loop"
        return current

"""Nodos LangGraph del sprint aislado y paralelo."""
import copy
import os

from langgraph.types import Send

import scrum
import metrics
import plan_analysis
import task_worktrees
import taskqueue
import lifecycle
import chronicle


def _scrum_complete(system: str, user: str, workdir: str) -> str:
    """Usa opcionalmente un modelo rapido sin contaminar llamadas posteriores."""
    import providers
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

    def __init__(self, workdir, args, cfg, nodes, auto_human, step_fn,
                 log_fn, commit_fn, normalize_fn, project_fn):
        self.workdir = workdir
        self.args = args
        self.cfg = cfg
        self.nodes = nodes
        self.auto_human = auto_human
        self.step_fn = step_fn
        self.log_fn = log_fn
        self.commit_fn = commit_fn
        self.normalize = normalize_fn
        self.project = project_fn

    def schedule(self, value):
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
                self.log_fn(current, "ESCALATE_HUMAN", motivo=str(error))
                self.project(current)
                return current
            _, total = taskqueue.progress(current["tasks"])
            self.log_fn(current, "PLAN", tareas=total,
                        nodos=len({task["node"] for task in current["tasks"]}))

        ready = taskqueue.runnable(current["tasks"])
        if not ready:
            pending = taskqueue.pending(current["tasks"])
            if not pending:
                current["status"] = "done"
                taskqueue.clear_current(self.workdir)
            else:
                current["status"] = "escalated"
                blocked = ", ".join(
                    f"{task['id']}({task['status']})" for task in pending[:6])
                self.log_fn(current, "ESCALATE_HUMAN",
                            motivo=f"ninguna tarea ejecutable: {blocked}")
            self.project(current)
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
        current["batch_seq"] = int(current.get("batch_seq", 0)) + 1
        batch_id = f"B-{current['batch_seq']:04d}"
        try:
            for task in batch:
                task["workspace"] = task_worktrees.prepare(
                    self.workdir, str(current["run_id"]), task)
        except RuntimeError as error:
            current["status"] = "escalated"
            self.log_fn(current, "ESCALATE_HUMAN", motivo=str(error))
            self.project(current)
            return current
        current["parallel_batch"] = {
            "id": batch_id,
            "task_ids": [str(task["id"]) for task in batch],
        }
        current["current_task"] = str(batch[0]["id"]) if len(batch) == 1 else None
        current["cursor"] = "parallel_dispatch"
        done, total = taskqueue.progress(current["tasks"])
        self.log_fn(current, "BATCH", id=batch_id, tareas=len(batch),
                    avance=f"{done}/{total}",
                    ids=",".join(str(task["id"]) for task in batch))
        self.project(current)
        return current

    def dispatch(self, value):
        batch = value.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        return [Send("parallel_worker", {
            **copy.deepcopy(dict(value)),
            "worker_task_id": task_id,
            "parallel_batch": {"id": batch_id, "task_ids": [task_id]},
        }) for task_id in batch.get("task_ids", [])]

    def worker(self, value):
        parent = self.normalize(copy.deepcopy(dict(value)))
        task_id = str(parent.get("worker_task_id"))
        batch_id = str((parent.get("parallel_batch") or {}).get("id"))
        source = taskqueue.by_id(parent["tasks"], task_id)
        if source is None:
            raise RuntimeError(f"{batch_id}: no existe la tarea {task_id}")
        task = copy.deepcopy(source)
        workspace = task.get("workspace")
        if not isinstance(workspace, dict):
            raise RuntimeError(f"{task_id}: worktree no preparado")

        local = copy.deepcopy(parent)
        local.update(tasks=[task], history=[], attempts={}, agent_calls=0,
                     defect_seq=0, current_task=task_id,
                     cursor=str(task["node"]), status="running")
        worker_args = copy.copy(self.args)
        setattr(worker_args, "workdir", str(workspace["path"]))
        taskqueue.publish_current(str(workspace["path"]), task)
        lifecycle.started(self.workdir, task_id, str(task["node"]),
                          workspace=str(workspace["path"]), batch_id=batch_id)

        while local["status"] == "running":
            active = taskqueue.by_id(local["tasks"], task_id)
            if active is None or active["status"] == "done":
                break
            if any(item.get("kind") == "defect" for item in local["tasks"]
                   if item.get("id") != task_id):
                break
            if local["cursor"] == "task_loop":
                break
            setattr(worker_args, "visit_id",
                    f"{batch_id}-{task_id}-{local['agent_calls']}")
            self.step_fn(local, worker_args, self.cfg, self.nodes, self.auto_human)

        active = taskqueue.by_id(local["tasks"], task_id) or task
        node = self.nodes[str(active["node"])]
        task_worktrees.preserve(
            active, [str(path) for path in node.get("writes", [])])
        metrics.transfer(str(workspace["path"]), self.workdir)
        chronicle.transfer(str(workspace["path"]), self.workdir)
        defect = next((item for item in local["tasks"]
                       if item.get("kind") == "defect"), None)
        if active.get("status") == "done":
            outcome = "done"
        elif defect is not None:
            outcome = "blocked"
        else:
            outcome = "escalated"
        result = {
            "batch_id": batch_id, "task_id": task_id, "outcome": outcome,
            "task": active, "defect": defect, "history": local["history"],
            "attempts": local["attempts"], "agent_calls": local["agent_calls"],
            "status": local["status"],
        }
        return {"parallel_results": {f"{batch_id}:{task_id}": result}}

    def collect(self, value):
        current = self.normalize(copy.deepcopy(dict(value)))
        batch = current.get("parallel_batch") or {}
        batch_id = str(batch.get("id", ""))
        keys = [f"{batch_id}:{task_id}" for task_id in batch.get("task_ids", [])]
        results = current.get("parallel_results", {})
        for key in sorted(keys):
            result = results.get(key)
            if result is None:
                raise RuntimeError(f"resultado de worker ausente: {key}")
            if not self._collect_one(current, result):
                break
        current.update(parallel_batch=None, worker_task_id=None, current_task=None)
        current["parallel_results"] = {}
        if current["status"] == "running":
            current["cursor"] = "task_loop"
        self.project(current)
        return current

    def _collect_one(self, current, result):
        task_id = str(result["task_id"])
        task = taskqueue.by_id(current["tasks"], task_id)
        if task is None:
            raise RuntimeError(f"tarea ausente al colectar: {task_id}")
        task["workspace"] = result["task"].get("workspace")
        current["agent_calls"] += int(result.get("agent_calls", 0))
        current["history"].extend(result.get("history", []))
        for attempt, count in result.get("attempts", {}).items():
            current["attempts"][attempt] = \
                current["attempts"].get(attempt, 0) + int(count)

        if result["outcome"] == "done":
            node = self.nodes[str(task["node"])]
            status, detail = task_worktrees.integrate(
                self.workdir, task, [str(path) for path in node.get("writes", [])],
                taskqueue.commit_message(str(task["node"]), task), self.commit_fn)
            if status == "error":
                current["status"] = "escalated"
                self.log_fn(current, "ESCALATE_HUMAN", motivo=detail)
                return False
            lifecycle.integrated(self.workdir, task_id, status, detail)
            done_before = {
                str(item["id"]) for item in current["tasks"]
                if item.get("status") == "done"
            }
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
            return True
        if result["outcome"] == "blocked":
            defect = result.get("defect") or {}
            limit = int(self.cfg["budget"].get("max_defect_tasks", 12))
            if current["defect_seq"] >= limit:
                current["status"] = "escalated"
                self.log_fn(
                    current, "ESCALATE_HUMAN",
                    motivo=f"tope de tareas de defecto alcanzado ({limit})")
                task_worktrees.cleanup(self.workdir, task)
                return False
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
        current["status"] = "escalated"
        self.log_fn(current, "ESCALATE_HUMAN",
                    motivo=f"worker {task_id} no pudo converger")
        return False

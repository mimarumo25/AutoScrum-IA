"""Journal de ciclo de vida por tarea.

Cada tarea recibe un archivo append-only `.agent/tasks/<task_id>/lifecycle.jsonl`
que registra cronologicamente cada evento de su ciclo de vida con timestamp,
nodo, resultado, hallazgos y cambios de estado.

Los eventos estandarizados permiten trazar la vida completa de una tarea sin
parsear el state.json del run ni reconstruirla desde el historial global.

La interfaz publica sigue el modelo de repo-as-state: funciones puras que
escriben un archivo por evento. Nada depende del orquestador ni de LangGraph.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_APPEND_LOCK = threading.Lock()

LIFECYCLE_FILE = "lifecycle.jsonl"
TASKS_ROOT = ".agent/tasks"


def _task_dir(workdir: str | Path, task_id: str) -> Path:
    return Path(workdir) / TASKS_ROOT / str(task_id)


def _emit(workdir: str | Path, task_id: str, event: dict[str, object]) -> None:
    payload = {
        "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **event,
    }
    task_dir = _task_dir(workdir, task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / LIFECYCLE_FILE
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")
    with _APPEND_LOCK:
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    _touch_manifest(workdir, task_id)


def _touch_manifest(workdir: str | Path, task_id: str) -> None:
    path = _task_dir(workdir, task_id) / "manifest.json"
    if path.exists():
        return
    pending = path.with_suffix(".tmp")
    stamped = {"task_id": str(task_id),
               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    pending.write_text(json.dumps(stamped, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    pending.replace(path)


def created(workdir: str | Path, task: dict[str, object]) -> None:
    manifest = _task_dir(workdir, str(task["id"])) / "manifest.json"
    if manifest.exists():
        return
    agent = task.get("agent") if isinstance(task.get("agent"), dict) else {}
    _emit(workdir, str(task["id"]), {
        "event": "created",
        "node": str(task.get("node")),
        "kind": str(task.get("kind", "plan")),
        "depends_on": [str(d) for d in (task.get("depends_on") or [])],
        "deliverables": [str(d) for d in (task.get("deliverables") or [])],
        "agent_id": str(agent.get("id") or ""),
        "agent_name": str(agent.get("name") or ""),
        "parent_agent_id": str(agent.get("parent_id") or ""),
        "parent_task_id": str(task.get("parent_task_id") or ""),
        "depth": int(task.get("depth", 0)),
    })


def delegated(workdir: str | Path, task_id: str, agent_id: str,
              child_ids: list[str], reason: str) -> None:
    """Registra que un agente principal dividio su unidad en hijos validados."""
    _emit(workdir, task_id, {
        "event": "delegated",
        "agent_id": agent_id,
        "child_ids": list(child_ids),
        "reason": reason,
    })


def started(workdir: str | Path, task_id: str, node: str,
            workspace: Optional[str] = None, batch_id: str = "",
            attempt: int = 1, agent_id: str = "") -> None:
    _emit(workdir, task_id, {
        "event": "started",
        "node": node,
        "workspace": workspace,
        "batch_id": batch_id,
        "attempt": attempt,
        "agent_id": agent_id,
    })


def agent_called(workdir: str | Path, task_id: str, returncode: int,
                 status: str = "completed") -> None:
    _emit(workdir, task_id, {
        "event": "agent_called",
        "returncode": returncode,
        "status": status,
    })


def model_selected(workdir: str | Path, task_id: str, selection: dict) -> None:
    """Registra la decision efectiva del router, nunca sus credenciales."""
    safe = {key: selection.get(key) for key in (
        "provider", "model", "tier", "requested_tier",
        "selection_reason", "fallback_reason", "escalated",
    )}
    _emit(workdir, task_id, {"event": "model_selected", **safe})


def model_escalated(workdir: str | Path, task_id: str,
                    gate: str, count: int) -> None:
    _emit(workdir, task_id, {
        "event": "model_escalated", "gate": gate,
        "tier": "frontier", "count": count,
    })


def gate_result(workdir: str | Path, task_id: str, gate_id: str,
                passed: bool, findings: int = 0) -> None:
    _emit(workdir, task_id, {
        "event": "gate_result",
        "gate": gate_id,
        "status": "pass" if passed else "fail",
        "findings": findings,
    })


def blocked(workdir: str | Path, task_id: str, blocked_by: str,
            gate: str, findings: list[dict[str, object]]) -> None:
    _emit(workdir, task_id, {
        "event": "blocked",
        "blocked_by": blocked_by,
        "gate": gate,
        "findings": [
            {"file": f.get("file"), "rule": f.get("rule"),
             "evidence": str(f.get("evidence", ""))[:200]}
            for f in (findings or [])[:10]
        ],
    })


def retried(workdir: str | Path, task_id: str, gate: str,
            attempt: int, max_retries: int) -> None:
    _emit(workdir, task_id, {
        "event": "retried",
        "gate": gate,
        "attempt": attempt,
        "max_retries": max_retries,
    })


def escalated(workdir: str | Path, task_id: str, reason: str) -> None:
    _emit(workdir, task_id, {
        "event": "escalated",
        "reason": reason,
    })


def integrated(workdir: str | Path, task_id: str, result: str,
               detail: str = "") -> None:
    _emit(workdir, task_id, {
        "event": "integrated",
        "result": result,
        "detail": detail,
    })


def done(workdir: str | Path, task_id: str) -> None:
    _emit(workdir, task_id, {
        "event": "done",
        "at_epoch": time.time(),
    })
    manifest = _task_dir(workdir, task_id) / "manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            pending = manifest.with_suffix(".tmp")
            pending.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
            pending.replace(manifest)
        except (OSError, json.JSONDecodeError):
            pass


def read(workdir: str | Path, task_id: str) -> list[dict[str, object]]:
    path = _task_dir(workdir, str(task_id)) / LIFECYCLE_FILE
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def summary(workdir: str | Path, task_id: str) -> dict[str, object]:
    events = read(workdir, task_id)
    if not events:
        return {"task_id": task_id, "status": "no-journal"}

    states: dict[str, object] = {
        "task_id": task_id,
        "events": len(events),
        "created": None,
        "started": None,
        "done": None,
    }

    gate_status: dict[str, bool] = {}
    calls = 0
    last_status = "unknown"

    for ev in events:
        name = str(ev.get("event") or "")
        at = ev.get("t")
        if name == "created":
            states["node"] = ev.get("node")
            states["kind"] = ev.get("kind")
            states["agent_id"] = ev.get("agent_id")
            states["agent_name"] = ev.get("agent_name")
            states["parent_agent_id"] = ev.get("parent_agent_id")
            states["parent_task_id"] = ev.get("parent_task_id")
            states["depth"] = ev.get("depth", 0)
            states["created"] = at
        elif name == "started":
            if states["created"] is None:
                states["created"] = at
            states["started"] = states.get("started") or at
        elif name == "agent_called":
            calls += 1
        elif name == "model_selected":
            states["provider"] = ev.get("provider")
            states["model"] = ev.get("model")
            states["tier"] = ev.get("tier")
            states["selection_reason"] = ev.get("selection_reason")
            states["fallback_reason"] = ev.get("fallback_reason")
            states["model_escalated"] = bool(ev.get("escalated"))
        elif name == "model_escalated":
            states["model_escalated"] = True
            states["escalation_gate"] = ev.get("gate")
        elif name == "gate_result":
            gate_name = str(ev.get("gate") or "")
            gate_status[gate_name] = ev.get("status") == "pass"
        elif name == "blocked":
            last_status = "blocked"
            states["blocked_by"] = ev.get("blocked_by")
        elif name == "delegated":
            last_status = "delegated"
            states["child_ids"] = ev.get("child_ids") or []
            states["delegation_reason"] = ev.get("reason")
        elif name == "escalated":
            last_status = "escalated"
        elif name == "integrated":
            last_status = "integrated"
        elif name == "done":
            last_status = "done"
            states["done"] = at

    states["calls"] = calls
    states["gates"] = gate_status
    if last_status == "integrated" or last_status == "done":
        states["status"] = "done"
    else:
        states["status"] = last_status

    return states


def all_tasks(workdir: str | Path) -> list[dict[str, object]]:
    root = Path(workdir) / TASKS_ROOT
    if not root.exists():
        return []
    result: list[dict[str, object]] = []
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir():
            continue
        manifest = task_dir / "manifest.json"
        if not manifest.exists():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["task_id"] = task_dir.name
            result.append(payload)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def total_token_usage_by_task(workdir: str | Path, task_id: str) -> dict[str, int]:
    tasks_root = Path(workdir) / TASKS_ROOT / str(task_id)
    path = tasks_root / "usage.jsonl"
    if not path.exists():
        return {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
            for k in total:
                total[k] += int(rec.get(k, 0) or 0)
        except (json.JSONDecodeError, ValueError):
            continue
    return total

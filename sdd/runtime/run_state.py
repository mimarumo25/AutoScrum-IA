"""Carga, proyeccion y preparacion durable de una corrida."""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sdd.core import metrics
from sdd.runtime import taskqueue
from sdd.runtime.workflow_defects import (assign_linear_recovery,
                                          linear_recovery_context)


def load_state(workdir, start):
    path = Path(workdir) / ".agent/state.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), path
    state = {
        "run_id": (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
                   + uuid.uuid4().hex[:8]),
        "cursor": start, "status": "running", "attempts": {},
        "agent_calls": 0, "started_at": time.time(), "tasks": [],
        "current_task": None, "defect_seq": 0, "recovery_seq": 0,
        "recoveries": [], "resume_stack": [], "history": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    return state, path


def save(state, path):
    started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        pending.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        for attempt in range(6):
            try:
                pending.replace(path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        pending.unlink(missing_ok=True)
    metrics.record(path.parent.parent, "state_projection",
                   duration_ms=round((time.perf_counter() - started) * 1000, 3),
                   bytes=path.stat().st_size)


def _resume_findings(state):
    history = state.get("history") or []
    terminal_index = next((index for index in range(len(history) - 1, -1, -1)
                           if history[index].get("event") in {
                               "ESCALATE_HUMAN", "RECUPERACION_EN_ESPERA",
                               "RAMAS_EN_ESPERA"}), len(history))
    last_defect = next((history[index] for index in range(terminal_index - 1, -1, -1)
                        if history[index].get("event") == "DEFECTO"), None)
    if last_defect is None:
        return "", "", []
    gate = str(last_defect.get("gate") or "")
    owner = str(last_defect.get("owner") or state.get("cursor") or "")
    findings = []
    for event in reversed(history[:terminal_index]):
        if event.get("event") == "AGENTE_INICIO" and findings:
            break
        if event.get("event") != "DEFECTO":
            continue
        if (str(event.get("gate") or "") != gate
                or str(event.get("owner") or owner) != owner):
            if findings:
                break
            continue
        location = str(event.get("ubicacion") or "")
        file_name, line = location, 0
        if ":" in location:
            candidate, raw_line = location.rsplit(":", 1)
            try:
                file_name, line = candidate, int(raw_line)
            except ValueError:
                pass
        findings.append({
            "file": file_name, "line": line,
            "rule": str(event.get("regla") or "fallo-anterior"),
            "evidence": str(event.get("evidencia") or "correccion pendiente"),
        })
    findings.reverse()
    return owner, gate, findings


def _restore_resume_recovery(state, workdir):
    if not workdir:
        return None
    recoveries = state.setdefault("recoveries", [])
    for recovery in recoveries:
        if recovery.get("status") == "needs_input":
            recovery["status"] = "assigned"
    assigned = [item for item in recoveries if item.get("status") == "assigned"]
    if not assigned and not state.get("tasks"):
        owner, gate, findings = _resume_findings(state)
        failed_node = str(state.get("cursor") or owner)
        if owner and gate and findings:
            recovery = assign_linear_recovery(
                state, workdir, failed_node, owner, gate, findings, 0)
            recovery["model_escalated"] = True
            recovery["model_escalation_count"] = max(
                1, int(recovery.get("model_escalation_count", 0)))
            assigned = [recovery]
    owners = {str(item.get("owner")) for item in assigned if item.get("owner")}
    for owner in owners:
        taskqueue.publish_current(workdir, linear_recovery_context(state, owner))
    return assigned[-1] if assigned else None


def prepare_resume(state, workdir=None):
    previous = state.get("status")
    resume_cursor = state.get("cursor")
    resumed_at = time.time()
    previous_started_at = state.get("started_at")
    state.setdefault("resume_history", []).append({
        "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": previous, "cursor": resume_cursor,
        "current_task": state.get("current_task"),
        "attempts": dict(state.get("attempts") or {}),
        "started_at": previous_started_at, "resumed_at": resumed_at,
    })
    state["resume_checkpoint"] = {
        "from_node": resume_cursor, "from_task": state.get("current_task"),
        "previous_status": previous,
    }
    state["status"] = "running"
    if previous != "waiting_human":
        state["attempts"] = {}
    state["resume_at"] = None
    if previous_started_at is not None:
        state.setdefault("original_started_at", previous_started_at)
    state["started_at"] = resumed_at
    state["resume_started_at"] = resumed_at
    taskqueue.reactivate_attention_tasks(state.get("tasks") or [])
    if state.get("cursor") in {"parallel_dispatch", "parallel_collect"}:
        state.update(cursor="task_loop", parallel_batch=None,
                     parallel_results={}, worker_task_id=None, current_task=None)
    recovery = (None if previous == "waiting_human"
                else _restore_resume_recovery(state, workdir))
    state["resume_recovery"] = ({
        "id": recovery.get("id"), "owner": recovery.get("owner"),
        "failed_node": recovery.get("failed_node"),
        "gate_id": recovery.get("gate_id"),
        "findings": len(recovery.get("findings") or []),
    } if recovery else None)
    return previous


def token_usage(workdir):
    path = Path(workdir) / ".agent/usage.jsonl"
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        for key in total:
            total[key] += int(record.get(key, 0) or 0)
    return total

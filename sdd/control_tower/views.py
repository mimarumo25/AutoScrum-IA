"""Lectura de artefactos y construcción de payloads para el panel."""

import json
import tomllib
from pathlib import Path

from sdd.core import config, lifecycle
from sdd.integrations import model_router
from sdd.presentation import report
from sdd.control_tower import state
from sdd.control_tower.runtime import ROOT


def sprint_from(state_path: Path):
    try:
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [
        {
            "id": task.get("id"),
            "node": task.get("node"),
            "title": task.get("title", ""),
            "status": task.get("status"),
            "kind": task.get("kind"),
            "blocked_by": task.get("blocked_by"),
            "attention_reason": task.get("attention_reason", ""),
            "attention_gate": task.get("attention_gate", ""),
            "model_escalated": bool(task.get("model_escalated")),
            "model_selection": task.get("model_selection") or {},
        }
        for task in (persisted.get("tasks") or [])
    ]


def artifact_list(workdir: Path):
    """Artefactos recientes, sin exponer archivos internos ni secretos."""
    if not workdir.exists():
        return []
    result = []
    for root in (workdir / "spec", workdir / "src", workdir / "tests"):
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path.stat().st_size <= 512_000:
                    result.append(
                        {
                            "path": path.relative_to(workdir).as_posix(),
                            "size": path.stat().st_size,
                            "updated": path.stat().st_mtime,
                        }
                    )
    return sorted(result, key=lambda item: item["updated"], reverse=True)[:40]


def runtime_agents(
    steps,
    sprint,
    run_status,
    task_summaries=None,
    activity=None,
    failure=None,
    cursor=None,
    recoveries=None,
):
    """Proyecta pasos y journals concurrentes en microestados visuales."""
    current, summaries, activity, failure = (
        (steps[-1] if steps else None),
        task_summaries or [],
        activity or {},
        failure or {},
    )
    completed = {step.get("node") for step in steps if step.get("commit")}
    open_recoveries = [
        item for item in (recoveries or []) if item.get("status") == "assigned"
    ]
    waiting_nodes = {
        str(item.get("failed_node"))
        for item in open_recoveries
        if item.get("failed_node") != item.get("owner")
    }
    correcting_nodes = {str(item.get("owner")) for item in open_recoveries}
    active_node, active_phase = (
        (activity.get("node") if run_status == "running" else None),
        activity.get("phase", "thinking"),
    )
    phase_state = {
        "starting": "thinking",
        "thinking": "thinking",
        "streaming": "streaming",
        "tool_call": "tool_call",
        "validating": "validating",
        "retrying": "retrying",
        "waiting": "waiting",
        "blocked": "waiting",
        "error": "error",
    }
    try:
        projected = model_router.preview().get("roles", {})
    except Exception:  # noqa: BLE001 - routing preview must not prevent rendering.
        projected = {}
    output = []
    for agent in config.agent_catalog():
        node_id = agent["id"]
        tasks = [task for task in sprint if task.get("node") == node_id]
        node_runs = [item for item in summaries if item.get("node") == node_id]
        live_runs = [
            item
            for item in node_runs
            if item.get("status") not in ("done", "blocked", "escalated")
        ]
        blocked_runs = [item for item in node_runs if item.get("status") == "blocked"]
        failed_runs = [item for item in node_runs if item.get("status") == "escalated"]
        if not agent.get("enabled", True):
            state_name = "disabled"
        elif active_node == node_id and run_status == "running":
            state_name = phase_state.get(active_phase, "thinking")
        elif node_id in waiting_nodes:
            state_name = "waiting"
        elif node_id in correcting_nodes and run_status == "running":
            state_name = "retrying"
        elif live_runs and run_status == "running":
            state_name = (
                "tool_call"
                if any(item.get("calls", 0) for item in live_runs)
                else "thinking"
            )
        elif run_status in {"escalated", "error"} and (
            failure.get("node") == node_id or cursor == node_id
        ):
            state_name = "error"
        elif (
            tasks
            and any(task.get("status") in ("blocked", "needs_input") for task in tasks)
            or blocked_runs
        ):
            state_name = "waiting"
        elif (
            failed_runs
            or tasks
            and any(task.get("status") == "escalated" for task in tasks)
        ):
            state_name = "error"
        elif (
            not active_node
            and current
            and current.get("node") == node_id
            and run_status == "running"
        ):
            state_name = (
                "error"
                if any(not gate[1] for gate in current.get("gates", []))
                else "thinking"
            )
        elif (
            node_id in completed
            or node_runs
            and all(item.get("status") == "done" for item in node_runs)
            or tasks
            and all(task.get("status") == "done" for task in tasks)
        ):
            state_name = "completed"
        elif run_status == "running" and (tasks or node_runs):
            state_name = "queued"
        else:
            state_name = "idle"
        waiting = next(
            (
                item
                for item in reversed(open_recoveries)
                if str(item.get("failed_node")) == node_id
                and item.get("owner") != node_id
            ),
            None,
        )
        owned = next(
            (
                item
                for item in reversed(open_recoveries)
                if str(item.get("owner")) == node_id
            ),
            None,
        )
        active_task = live_runs[-1].get("task_id", "") if live_runs else ""
        if active_node == node_id:
            active_task = activity.get("task") or active_task
        if not active_task and current and current.get("node") == node_id:
            active_task = current.get("task", "")
        message = activity.get("message", "") if active_node == node_id else ""
        if not message and waiting:
            message = f"Esperando corrección de {waiting.get('owner')} para {waiting.get('gate_id')}"
        elif not message and owned:
            message = (
                f"Corrigiendo {owned.get('gate_id')} para {owned.get('failed_node')}"
            )
        observed = (
            live_runs[-1]
            if live_runs
            else node_runs[-1]
            if node_runs
            else projected.get(node_id, {})
        )
        output.append(
            {
                "id": node_id,
                "name": agent.get("name", node_id),
                "role": agent.get("role", ""),
                "state": state_name,
                "enabled": agent.get("enabled", True),
                "tools": agent.get("tools", []),
                "provider": observed.get("provider", agent.get("provider", "")),
                "model": observed.get("model", agent.get("model", "")),
                "tier": observed.get("tier", ""),
                "selection_reason": observed.get("selection_reason", ""),
                "fallback_reason": observed.get("fallback_reason", ""),
                "escalated": bool(
                    observed.get("model_escalated", observed.get("escalated", False))
                ),
                "tasks": max(len(tasks), len(node_runs)),
                "tasks_done": max(
                    sum(task.get("status") == "done" for task in tasks),
                    sum(item.get("status") == "done" for item in node_runs),
                ),
                "current_task": active_task,
                "active_runs": len(live_runs),
                "activity_message": message,
                "waiting_for": (waiting or {}).get("owner", ""),
                "recovery_id": ((waiting or owned) or {}).get("id", ""),
                "activity_updated_at": activity.get("updated_at")
                if active_node == node_id
                else None,
                "attempt": activity.get("attempt") if active_node == node_id else None,
                "gate": activity.get("gate", "") if active_node == node_id else "",
            }
        )
    return output


def failure_from_history(raw_state, status):
    if status not in {"escalated", "error", "waiting_human"}:
        return None
    history = raw_state.get("history") or []
    terminal = next(
        (
            item
            for item in reversed(history)
            if item.get("event")
            in {
                "ESCALATE_HUMAN",
                "PRESUPUESTO",
                "GATE_HUMANO",
                "RAMAS_EN_ESPERA",
                "RECUPERACION_EN_ESPERA",
            }
        ),
        None,
    )
    defect = next(
        (item for item in reversed(history) if item.get("event") == "DEFECTO"), None
    )
    source = terminal or defect or {}
    node = (
        source.get("owner")
        or source.get("nodo")
        or (defect or {}).get("owner")
        or raw_state.get("cursor", "")
    )
    gate = source.get("gate") or (defect or {}).get("gate", "")
    attempt = (
        (raw_state.get("attempts") or {}).get(f"{node}:{gate}")
        if node and gate
        else None
    )
    reason = (
        source.get("motivo")
        or source.get("evidencia")
        or f"La ejecución terminó en estado {status}"
    )
    findings = [
        item.get("evidencia")
        for item in history[-30:]
        if item.get("event") == "DEFECTO"
        and (not gate or item.get("gate") == gate)
        and item.get("evidencia")
    ]
    return {
        "id": f"{raw_state.get('run_id', '')}:{source.get('t', '')}:{node}:{gate}",
        "severity": "warning" if status == "waiting_human" else "error",
        "node": node,
        "gate": gate,
        "reason": reason,
        "rule": (defect or {}).get("regla", ""),
        "location": (defect or {}).get("ubicacion", ""),
        "attempt": attempt,
        "findings": findings[-5:],
        "can_resume": True,
        "resume_node": raw_state.get("cursor", node),
        "technical": {
            "reason": reason,
            "node": node,
            "gate": gate,
            "rule": (defect or {}).get("regla", ""),
            "location": (defect or {}).get("ubicacion", ""),
            "findings": findings[-5:],
        },
        **state.humanize_failure(
            reason, findings[-5:], (defect or {}).get("regla", ""), node, attempt
        ),
    }


def view_payload(workdir, status, provider, project, task, runtime=None):
    workdir = Path(workdir) if workdir else None
    steps, nodes, final, sprint, engine, raw_state = [], [], None, [], None, {}
    if workdir and (path := workdir / ".agent/state.json").exists():
        try:
            raw_state = json.loads(path.read_text(encoding="utf-8"))
            cfg = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
            steps, nodes, final, engine, sprint = (
                report.build_steps(raw_state.get("history", [])),
                [node["id"] for node in cfg["node"]] + ["done"],
                raw_state.get("status"),
                raw_state.get("engine"),
                sprint_from(path),
            )
        except (ValueError, OSError, tomllib.TOMLDecodeError):
            pass
    runtime = runtime or {}
    effective_status = (
        final if status not in {"starting", "running"} and final else status
    )
    failure = runtime.get("failure") or failure_from_history(
        raw_state, effective_status
    )
    activity = runtime.get("activity") or {}
    if (
        effective_status in state.TERMINAL_STATUSES
        and activity.get("phase") in state.ACTIVE_PHASES
    ):
        activity = state.activity(
            "error"
            if effective_status in {"escalated", "error"}
            else "waiting"
            if effective_status == "waiting_human"
            else "completed",
            (failure or {}).get("node") or raw_state.get("cursor", ""),
            "",
            (failure or {}).get("reason") or f"Estado final: {effective_status}",
            gate=(failure or {}).get("gate", ""),
        )
    iterations = [
        {
            **step,
            "id": f"iter-{index + 1:02d}",
            "index": index + 1,
            "status": "error"
            if any(not gate[1] for gate in step.get("gates", []))
            else "completed"
            if step.get("commit")
            else "active",
        }
        for index, step in enumerate(steps)
    ]
    idea = (
        (workdir / "spec/00_intake.yaml").read_text(encoding="utf-8", errors="replace")
        if workdir and (workdir / "spec/00_intake.yaml").exists()
        else ""
    )
    try:
        tokens = (
            report._token_usage(workdir)
            if workdir
            else {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        )
    except (OSError, ValueError):
        tokens = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    try:
        summaries = (
            [
                lifecycle.summary(workdir, item["task_id"])
                for item in lifecycle.all_tasks(workdir)
            ]
            if workdir
            else []
        )
    except (OSError, ValueError, KeyError):
        summaries = []
    return {
        "status": effective_status,
        "final": final,
        "provider": provider,
        "project": project,
        "task": task,
        "engine": engine,
        "steps": steps,
        "iterations": iterations,
        "nodes": nodes,
        "sprint": sprint,
        "agents": runtime_agents(
            steps,
            sprint,
            effective_status,
            summaries,
            activity,
            failure,
            raw_state.get("cursor"),
            raw_state.get("recoveries", []),
        ),
        "live_tasks": summaries,
        "tokens": tokens,
        "input": idea,
        "activity": activity,
        "failure": failure,
        "recoveries": raw_state.get("recoveries", []),
        "revision": runtime.get("revision", 0),
        "updated_at": runtime.get("updated_at", 0),
        "artifacts": artifact_list(workdir) if workdir else [],
        "raw": {
            "run_id": raw_state.get("run_id"),
            "agent_calls": raw_state.get("agent_calls", 0),
            "attempts": raw_state.get("attempts", {}),
            "started_at": raw_state.get("started_at"),
            "resume_checkpoint": raw_state.get("resume_checkpoint") or {},
            "recoveries": raw_state.get("recoveries", []),
        },
    }


def repair_mojibake(value):
    if not isinstance(value, str) or not any(
        mark in value for mark in ("Ã", "Â", "â€")
    ):
        return value
    try:
        repaired = value.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return (
        repaired
        if sum(repaired.count(mark) for mark in ("Ã", "Â", "â€"))
        < sum(value.count(mark) for mark in ("Ã", "Â", "â€"))
        else value
    )


def current_state():
    with state.LOCK:
        snapshot = dict(state.RUN)
        snapshot["log"] = [repair_mojibake(line) for line in state.RUN["log"]]
    payload = view_payload(
        snapshot["workdir"],
        snapshot["status"],
        snapshot["provider"],
        snapshot["project"],
        snapshot["task"],
        snapshot,
    )
    payload["log"] = snapshot["log"]
    return payload


def task_view(project, task):
    workdir = config.resolve_output(project, task)
    state_path = workdir / ".agent/state.json"
    payload = view_payload(workdir, "idle", None, project, task)
    if not state_path.exists():
        payload["final"], payload["log"] = "sin correr", []
        return payload
    log_path = workdir / ".agent/run.log"
    payload["log"] = (
        [
            repair_mojibake(line)
            for line in log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        ]
        if log_path.exists()
        else []
    )
    return payload

"""Arranque, reanudación y ejecución del proceso orquestador."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from sdd.core import config, process_control
from sdd.integrations import providers
from sdd.control_tower import state

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "SDD_API_KEY"}
KEY_ENV.update(
    {
        provider: values["key_env"]
        for provider, values in providers.OPENAI_PRESETS.items()
    }
)


def _git(workdir, *args):
    process_control.run_git(workdir, *args, text=True)


def claim_run():
    """Reserva atomica: dos POST concurrentes no pueden iniciar dos corridas."""
    with state.LOCK:
        if state.RUN["status"] in ("starting", "running"):
            return False
        state.RUN["status"] = "starting"
        state.RUN["revision"] = int(state.RUN.get("revision", 0)) + 1
        state.RUN["updated_at"] = time.time()
        return True


def release_claim():
    with state.LOCK:
        if state.RUN["status"] == "starting":
            state.RUN["status"] = "idle"
            state.RUN["revision"] = int(state.RUN.get("revision", 0)) + 1
            state.RUN["updated_at"] = time.time()


def seed(workdir: Path, idea: str):
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / ".git").exists():
        _git(workdir, "init", "-q")
        _git(workdir, "config", "user.email", "sdd@local")
        _git(workdir, "config", "user.name", "sdd-pipeline")
        (workdir / ".gitignore").write_text(config.GITIGNORE, encoding="utf-8")
    intake = workdir / "spec/00_intake.yaml"
    intake.parent.mkdir(parents=True, exist_ok=True)
    intake.write_text(idea, encoding="utf-8")
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-qm", "chore: intake desde la interfaz web")


def run_pipeline(workdir: Path, env: dict, extra=()):
    logfile = workdir / ".agent/run.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        with logfile.open(
            "a" if "--resume" in extra else "w", encoding="utf-8"
        ) as file:
            process = subprocess.Popen(
                [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(workdir), *extra],
                cwd=str(ROOT.parent),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            for line in process.stdout or ():
                line = line.rstrip("\n")
                with state.LOCK:
                    state.RUN["log"].append(line)
                state.observe_pipeline_line(line)
                file.write(line + "\n")
                file.flush()
            returncode = process.wait()
    except Exception as error:  # noqa: BLE001
        reason = f"Error del runtime: {error}"
        with state.LOCK:
            state.RUN["log"].append(f"ERROR: {error}")
            current = dict(state.RUN.get("activity") or {})
        state.run_update(
            status="error",
            activity=state.activity(
                "error", current.get("node"), current.get("task"), reason
            ),
            failure={
                "id": f"runtime:{time.time_ns()}",
                "severity": "error",
                "node": current.get("node", ""),
                "gate": "runtime",
                "reason": reason,
                "findings": [],
                "can_resume": True,
            },
        )
    finally:
        try:
            persisted = json.loads(
                (workdir / ".agent/state.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            persisted = {}
        terminal = persisted.get("status") or (
            "done" if locals().get("returncode") == 0 else "error"
        )
        cursor = persisted.get("cursor", "")
        with state.LOCK:
            current, failure = (
                dict(state.RUN.get("activity") or {}),
                dict(state.RUN.get("failure") or {}),
            )
        if terminal in {"escalated", "error", "waiting_human"}:
            reason = (
                failure.get("reason") or f"La ejecución terminó en estado {terminal}"
            )
            failure = {
                **failure,
                "id": failure.get("id") or f"{cursor}:{terminal}",
                "severity": "error" if terminal != "waiting_human" else "warning",
                "node": failure.get("node") or cursor,
                "reason": reason,
                "can_resume": True,
            }
            final_activity = state.activity(
                "blocked" if terminal == "waiting_human" else "error",
                failure.get("node") or cursor,
                current.get("task"),
                reason,
                gate=failure.get("gate", ""),
            )
        else:
            failure = None
            final_activity = state.activity(
                "completed",
                cursor,
                current.get("task"),
                "La ejecución completó todos los pasos",
            )
        state.run_update(status=terminal, activity=final_activity, failure=failure)


def env_for(provider: str, model: str, key: str):
    env = dict(os.environ)
    env["SDD_PROVIDER"] = provider
    if model:
        env["SDD_MODEL"] = model
    if key:
        env[KEY_ENV.get(provider, "ANTHROPIC_API_KEY")] = key
    return env


def _autonomous(body: dict) -> bool:
    value = body.get("autonomous", False)
    if not isinstance(value, bool):
        raise ValueError("autonomous debe ser boolean")
    return value


def resume(body):
    project, task = (
        body.get("project", "").strip(),
        body.get("task", "").strip() or "tarea-1",
    )
    workdir = config.resolve_output(project, task)
    if not (workdir / ".agent/state.json").exists():
        return None
    cfg = config.load()
    provider = cfg.get("provider") or "anthropic"
    env = env_for(provider, cfg.get("model") or "", cfg["keys"].get(provider, ""))
    env["SDD_APPROVAL_ACTOR"] = "web"
    decision = str(body.get("decision") or "")
    feedback = str(body.get("feedback") or "").strip()
    autonomous = _autonomous(body)
    checkpoint = json.loads((workdir / ".agent/state.json").read_text(encoding="utf-8"))
    if checkpoint.get("status") == "waiting_human" and decision not in {
            "accept", "reject"} and not autonomous:
        raise ValueError("decision accept o reject es obligatoria para revision humana")
    if decision and decision not in {"accept", "reject"}:
        raise ValueError("decision debe ser accept o reject")
    if decision == "reject" and not feedback:
        raise ValueError("feedback es obligatorio al rechazar")
    node = checkpoint.get("cursor", "product")
    state.run_update(
        status="running",
        workdir=str(workdir),
        log=[],
        provider=provider,
        project=project,
        task=task,
        autonomous=autonomous,
        failure=None,
        activity=state.activity(
            "starting",
            node,
            checkpoint.get("current_task") or "",
            f"Reanudando exactamente desde {node}",
        ),
    )
    extra = ["--resume"]
    if autonomous:
        extra.append("--autonomous")
    if decision:
        extra.extend(("--human-decision", decision))
    if feedback:
        extra.extend(("--human-feedback", feedback))
    threading.Thread(
        target=run_pipeline, args=(workdir, env, tuple(extra)), daemon=True
    ).start()
    return workdir


def start(body):
    provider, key = body.get("provider", "anthropic"), body.get("key", "")
    model, project = body.get("model", "").strip(), body.get("project", "").strip()
    task = body.get("task", "").strip() or "tarea-1"
    autonomous = _autonomous(body)
    config.save(
        {"provider": provider, "model": model, "keys": {provider: key} if key else {}}
    )
    key = key or config.load()["keys"].get(provider, "")
    workdir = config.resolve_output(project, task)
    seed(workdir, body.get("idea", ""))
    state.run_update(
        status="running",
        workdir=str(workdir),
        log=[],
        provider=provider,
        project=project,
        task=task,
        autonomous=autonomous,
        failure=None,
        activity=state.activity(
            "starting",
            "product",
            "",
            "Inicializando Product Strategist y el contexto del proyecto",
        ),
    )
    extra = ("--autonomous",) if autonomous else ()
    threading.Thread(
        target=run_pipeline, args=(workdir, env_for(provider, model, key), extra),
        daemon=True,
    ).start()
    return workdir

"""Chronicle de agente: archivo completo de cada visita de agente.

Por cada visita de agente (llamada LLM), archiva bajo `.agent/chronicle/`:

  {visit_id}/
    manifest.json       — metadata (node, task, returncode, timing, token_usage)
    system_prompt.txt   — system prompt enviado al modelo
    user_prompt.txt     — contexto + tarea enviada al modelo
    response.txt        — respuesta cruda del LLM (antes del parseo de archivos)
    agent_stdout.txt    — salida stdout del agente
    agent_stderr.txt    — salida stderr del agente
    files_written.json  — lista de archivos escritos + omitidos
    gates/
      {gate_id}.json    — resultado de cada gate para esta visita

El chronicle NO depende del state.json ni de LangGraph: es un journal append-only
paralelo que sobrevive cortes y puede consultarse independientemente del run.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_APPEND_LOCK = threading.Lock()

CHRONICLE_ROOT = ".agent/chronicle"


def _chronicle_dir(workdir: str | Path, visit_id: str) -> Path:
    return Path(workdir) / CHRONICLE_ROOT / str(visit_id)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(content, encoding="utf-8")
    pending.replace(path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    pending.replace(path)


def archive_agent_call(workdir: str | Path, visit_id: str,
                       node: str, task_id: Optional[str],
                       system_prompt: str, user_prompt: str,
                       response_text: str,
                       stdout_text: str, stderr_text: str,
                       returncode: int,
                       files_written: list[str],
                       files_skipped: list[tuple[str, str]],
                       token_usage: Optional[dict[str, int]] = None,
                       ) -> None:
    base = _chronicle_dir(workdir, visit_id)

    _write_atomic(base / "system_prompt.txt", system_prompt)
    _write_atomic(base / "user_prompt.txt", user_prompt)
    _write_atomic(base / "response.txt", response_text)

    if stdout_text:
        _write_atomic(base / "agent_stdout.txt", stdout_text)
    if stderr_text:
        _write_atomic(base / "agent_stderr.txt", stderr_text)

    _write_json(base / "files_written.json", {
        "written": files_written,
        "skipped": [{"path": p, "reason": r} for p, r in files_skipped],
    })

    manifest = {
        "visit_id": visit_id,
        "node": node,
        "task_id": task_id,
        "returncode": returncode,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "at_epoch": time.time(),
        "prompt_chars": len(system_prompt) + len(user_prompt),
        "response_chars": len(response_text),
    }
    if token_usage is not None and token_usage.get("calls", 0) > 0:
        manifest["token_usage"] = token_usage

    _write_json(base / "manifest.json", manifest)


def archive_gate_result(workdir: str | Path, visit_id: str,
                        gate_id: str, status: str,
                        findings: list[dict[str, object]]) -> None:
    base = _chronicle_dir(workdir, visit_id)
    path = base / "gates" / f"{gate_id}.json"
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    attempts = int(existing.get("attempts", 0)) + 1
    _write_json(path, {
        "gate_id": gate_id,
        "status": status,
        "attempts": attempts,
        "findings": findings,
        "final": status == "pass",
    })


def archive_review_result(workdir: str | Path, visit_id: str,
                          label: str, findings: list[dict[str, object]],
                          mejoras: list[dict[str, object]],
                          nota: str = "") -> None:
    base = _chronicle_dir(workdir, visit_id)
    _write_json(base / "review" / f"{label}.json", {
        "label": label,
        "findings": findings,
        "mejoras": mejoras,
        "nota": nota,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def all_visits(workdir: str | Path) -> list[dict[str, object]]:
    root = Path(workdir) / CHRONICLE_ROOT
    if not root.exists():
        return []
    result = []
    for visit_dir in sorted(root.iterdir(), reverse=True):
        if not visit_dir.is_dir():
            continue
        manifest = visit_dir / "manifest.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["visit_id"] = visit_dir.name
            result.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def read_visit(workdir: str | Path, visit_id: str) -> dict[str, object]:
    base = _chronicle_dir(workdir, visit_id)
    if not base.exists():
        return {}
    manifest = base / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        result = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result["system_prompt"] = _read_if_exists(base / "system_prompt.txt")
    result["user_prompt"] = _read_if_exists(base / "user_prompt.txt")
    result["response"] = _read_if_exists(base / "response.txt")
    result["agent_stdout"] = _read_if_exists(base / "agent_stdout.txt")
    result["agent_stderr"] = _read_if_exists(base / "agent_stderr.txt")
    result["files_written"] = _read_json_if_exists(base / "files_written.json")
    return result


def _read_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _read_json_if_exists(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def cleanup_visits(workdir: str | Path, keep: int = 50) -> int:
    root = Path(workdir) / CHRONICLE_ROOT
    if not root.exists():
        return 0
    visits = all_visits(workdir)
    removed = 0
    for visit in visits[keep:]:
        visit_dir = root / str(visit["visit_id"])
        if visit_dir.exists():
            import shutil
            shutil.rmtree(visit_dir)
            removed += 1
    return removed


def transfer(source_workdir: str | Path, destination_workdir: str | Path) -> int:
    source = Path(source_workdir) / CHRONICLE_ROOT
    if not source.exists():
        return 0
    dest = Path(destination_workdir) / CHRONICLE_ROOT
    count = 0
    for visit_dir in source.iterdir():
        if not visit_dir.is_dir():
            continue
        target = dest / visit_dir.name
        if target.exists():
            continue
        import shutil
        shutil.copytree(str(visit_dir), str(target))
        count += 1
    return count

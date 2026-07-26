"""Telemetria local y barata del plano de control.

Cada proceso escribe una linea JSON mediante O_APPEND. El archivo queda bajo
`.agent/`, fuera de Git, y permite separar latencia de proveedor, agentes,
gates, checkpoints y Git sin convertir la observabilidad en otro servicio.
"""
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

METRICS_PATH = ".agent/metrics.jsonl"
USAGE_PATH = ".agent/usage.jsonl"
_APPEND_LOCK = threading.Lock()


def record(workdir: str | Path | None, operation: str, **fields: object) -> None:
    """Anade una medicion completa en una sola escritura append-only."""
    if not workdir:
        return
    path = Path(workdir) / METRICS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": time.time(),
        "operation": operation,
        **fields,
    }
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")
    _append(path, data)


def _append(path: Path, data: bytes) -> None:
    with _APPEND_LOCK:
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def transfer(source: str | Path, destination: str | Path) -> None:
    """Agrega telemetria de un worktree antes de retirarlo.

    Copia linea por linea bajo el mismo lock que las escrituras normales para
    que varios workers no intercalen JSON. Los archivos fuente son efimeros y
    desaparecen al limpiar el worktree.
    """
    source_root = Path(source)
    destination_root = Path(destination)
    for relative in (METRICS_PATH, USAGE_PATH):
        origin = source_root / relative
        if not origin.exists():
            continue
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        for line in origin.read_bytes().splitlines(keepends=True):
            if line.strip():
                _append(target, line if line.endswith(b"\n") else line + b"\n")


def record_usage(workdir: str | Path | None, **fields: object) -> None:
    """Registra consumo de cualquier llamada LLM, incluidos R1/R2 y scrum."""
    if not workdir:
        return
    path = Path(workdir) / USAGE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")
    _append(path, data)


@contextmanager
def timed(workdir: str | Path | None, operation: str,
          **fields: object) -> Iterator[None]:
    """Registra duracion y resultado de una operacion sin ocultar excepciones."""
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        record(workdir, operation,
               duration_ms=round((time.perf_counter() - started) * 1000, 3),
               outcome=outcome, **fields)


def summarize(workdir: str | Path) -> dict[str, dict[str, float | int]]:
    """Agrupa cantidad y tiempo por operacion; tolera lineas truncadas."""
    path = Path(workdir) / METRICS_PATH
    result: dict[str, dict[str, float | int]] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        key = str(item.get("operation", "unknown"))
        bucket = result.setdefault(key, {"count": 0, "duration_ms": 0.0})
        bucket["count"] = int(bucket["count"]) + 1
        bucket["duration_ms"] = round(
            float(bucket["duration_ms"]) + float(item.get("duration_ms", 0)), 3)
    return result

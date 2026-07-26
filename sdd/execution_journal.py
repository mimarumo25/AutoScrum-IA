"""Journal idempotente para efectos externos de una visita de agente.

LangGraph puede reejecutar un nodo si el proceso cae antes de guardar su
checkpoint. Las escrituras del agente son reemplazos y los commits ya eran
idempotentes, pero una llamada al modelo no lo es. Este journal permite reutilizar
el resultado de una visita que alcanzo a terminar antes del corte.
"""
import json
from pathlib import Path
from typing import Callable


AgentResult = tuple[int, str]


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def invoke_once(workdir: str, visit_id: str | None,
                operation: Callable[[], AgentResult]) -> AgentResult:
    """Ejecuta una visita una vez y reutiliza su resultado si quedo journalizado.

    La ventana entre la respuesta externa y la escritura del journal no puede
    cerrarse sin soporte de idempotency keys del proveedor, pero si cubre el caso
    comun: el agente termino y el orquestador cayo antes del siguiente checkpoint.
    """
    if not visit_id:
        return operation()

    path = Path(workdir) / ".agent/visits" / f"{visit_id}.json"
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("status") == "completed":
                return int(saved.get("returncode", 1)), str(saved.get("detail", ""))
        except (OSError, ValueError, TypeError):
            pass

    _write_atomic(path, {"visit_id": visit_id, "status": "started"})
    returncode, detail = operation()
    _write_atomic(path, {
        "visit_id": visit_id,
        "status": "completed",
        "returncode": returncode,
        "detail": detail,
    })
    return returncode, detail

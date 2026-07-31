"""Firma y proyeccion durable del gate humano."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def spec_hash(workdir: str) -> str:
    root = Path(workdir) / "spec"
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def approval_record(workdir: str, actor: str,
                    mode: str = "interactive") -> dict[str, object]:
    return {
        "approved": True,
        "actor": actor,
        "mode": mode,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec_hash": spec_hash(workdir),
    }


def rejected_record(actor: str, feedback: str = "") -> dict[str, object]:
    return {
        "approved": False,
        "actor": actor,
        "feedback": feedback,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def waiting_projection(path: Path,
                       fallback: dict[str, object]) -> dict[str, object]:
    if not path.exists():
        return fallback
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError, TypeError):
        return fallback

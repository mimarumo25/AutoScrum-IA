"""Huella determinista y alcance unico de artefactos de una unidad."""
import hashlib
import os
from pathlib import Path

from sdd.core import process_control


def allowed_roots(node: dict[str, object],
                  task: dict[str, object] | None) -> list[str]:
    """Une el contrato del nodo y los entregables sin reducir permisos."""
    values = [*node.get("writes", []),
              *((task or {}).get("deliverables", []))]
    return sorted({str(value).replace("\\", "/").rstrip("/")
                   for value in values if str(value).strip()})


def content_hash(workdir: str | Path, roots: list[str],
                 include_head: bool = True) -> str:
    """Incluye declaraciones, paths, bytes, faltantes y HEAD en una huella."""
    base = Path(workdir).resolve()
    digest = hashlib.sha256()
    digest.update(b"sdd-evaluation-v1\0")
    for root in sorted(set(roots)):
        normalized = root.replace("\\", "/").rstrip("/")
        digest.update(b"root\0" + normalized.encode("utf-8") + b"\0")
        target = (base / normalized).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            digest.update(b"outside\0")
            continue
        if not target.exists():
            digest.update(b"missing\0")
            continue
        paths = ([target] if target.is_file() else
                 sorted(item for item in target.rglob("*") if item.is_file()))
        if not paths:
            digest.update(b"empty\0")
        for path in paths:
            relative = path.relative_to(base).as_posix()
            digest.update(b"file\0" + relative.encode("utf-8") + b"\0")
            if path.is_symlink():
                digest.update(b"symlink\0" + os.readlink(path).encode("utf-8") + b"\0")
            else:
                digest.update(path.read_bytes())
            digest.update(b"\0")
    if include_head:
        proc = process_control.run_git(base, "rev-parse", "HEAD", text=True)
        head = proc.stdout.strip() if proc.returncode == 0 else "missing"
        digest.update(b"head\0" + head.encode("utf-8") + b"\0")
    return digest.hexdigest()


def evaluation_matches(evaluation: dict[str, object], workdir: str | Path) -> bool:
    expected = str(evaluation.get("content_hash") or "")
    roots = [str(item) for item in evaluation.get("content_roots", [])]
    return bool(expected and expected == content_hash(workdir, roots))

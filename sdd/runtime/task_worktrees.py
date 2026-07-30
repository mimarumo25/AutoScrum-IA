"""Aislamiento Git y seleccion segura de tareas concurrentes."""
import json
import re
from pathlib import Path

from sdd.core import process_control


def _git(repo: str | Path, *args: str, data: bytes | None = None):
    return process_control.run_git(repo, *args, data=data)


def _text(proc) -> str:
    raw = proc.stderr or proc.stdout or b""
    return raw.decode("utf-8", errors="replace").strip()


def _safe(value: object) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    return clean or "task"


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    pending.replace(path)


def _footprint(task: dict[str, object],
               nodes: dict[str, dict[str, object]]) -> list[str]:
    declared = [str(path).rstrip("/") for path in task.get("deliverables", [])
                if str(path).strip()]
    if declared:
        return declared
    node = nodes.get(str(task.get("node")), {})
    return [str(path).rstrip("/") for path in node.get("writes", [])]


def _overlaps(left: list[str], right: list[str]) -> bool:
    for a in left:
        for b in right:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                return True
    return False


def safe_batch(ready: list[dict[str, object]],
               nodes: dict[str, dict[str, object]], limit: int) -> list[dict[str, object]]:
    """Conjunto independiente maximal, estable segun el orden de prioridad.

    Los defectos no se aislan por tipo: solo una huella compartida serializa.
    """
    selected: list[dict[str, object]] = []
    footprints: list[list[str]] = []
    for task in ready:
        current = _footprint(task, nodes)
        if all(not _overlaps(current, existing) for existing in footprints):
            selected.append(task)
            footprints.append(current)
        if len(selected) >= max(1, limit):
            break
    return selected or ready[:1]


def prepare(workdir: str, run_id: str, task: dict[str, object]) -> dict[str, object]:
    """Crea o actualiza el worktree persistente de una tarea."""
    main = Path(workdir).resolve()
    main_head = _git(main, "rev-parse", "HEAD").stdout.decode().strip()
    workspace = task.get("workspace")
    if isinstance(workspace, dict):
        path = Path(str(workspace.get("path", "")))
        if path.exists() and (path / ".git").exists():
            merge = _git(path, "merge", "--no-edit", main_head)
            if merge.returncode != 0:
                raise RuntimeError(
                    f"no se pudo actualizar worktree de {task['id']}: {_text(merge)}")
            workspace["base_commit"] = main_head
            workspace["head"] = _git(path, "rev-parse", "HEAD").stdout.decode().strip()
            return workspace

    task_id = _safe(task.get("id"))
    branch = f"sdd/{_safe(run_id)}/{task_id}"
    path = main / ".agent" / "worktrees" / task_id
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path / ".git").exists():
        head = _git(path, "rev-parse", "HEAD").stdout.decode().strip()
        base = _git(main, "merge-base", main_head, head)
        if base.returncode != 0:
            raise RuntimeError(
                f"no se pudo reconstruir worktree de {task['id']}: {_text(base)}")
        return {
            "path": str(path), "branch": branch,
            "base_commit": base.stdout.decode().strip(), "head": head,
        }
    branch_exists = _git(main, "show-ref", "--verify", "--quiet",
                         f"refs/heads/{branch}").returncode == 0
    args = ["worktree", "add"]
    if not branch_exists:
        args.extend(["-b", branch])
    args.extend([str(path), branch if branch_exists else main_head])
    added = _git(main, *args)
    if added.returncode != 0:
        raise RuntimeError(f"no se pudo crear worktree de {task['id']}: {_text(added)}")
    return {
        "path": str(path),
        "branch": branch,
        "base_commit": main_head,
        "head": main_head,
    }


def preserve(task: dict[str, object], allowed: list[str]) -> None:
    """Guarda trabajo rojo en su rama para reanudarlo tras corregir el defecto."""
    workspace = task.get("workspace")
    if not isinstance(workspace, dict):
        return
    path = Path(str(workspace["path"]))
    for owned in allowed:
        _git(path, "add", "-A", "--", owned)
    staged = _git(path, "diff", "--cached", "--quiet")
    if staged.returncode != 0:
        _git(path, "commit", "-qm", f"chore(sdd): preservar {task['id']} bloqueada")
    workspace["head"] = _git(path, "rev-parse", "HEAD").stdout.decode().strip()


def _allowed(path: str, allowed: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for raw in allowed:
        prefix = raw.replace("\\", "/")
        if prefix.endswith("/") and normalized.startswith(prefix):
            return True
        if normalized == prefix or normalized.startswith(prefix + "."):
            return True
    return False


def integrate(workdir: str, task: dict[str, object], allowed: list[str],
              message: str, commit_fn) -> tuple[str, str]:
    """Integra una sola vez, incluso si el proceso cae antes del checkpoint."""
    workspace = task.get("workspace")
    if not isinstance(workspace, dict):
        return "error", "la tarea no tiene worktree"
    path = Path(str(workspace["path"]))
    base = str(workspace["base_commit"])
    journal_path = (Path(workdir) / ".agent" / "integrations" /
                    f"{_safe(task.get('id'))}-{_safe(base[:12])}.json")
    journal: dict[str, object] = {}
    if journal_path.exists():
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "error", f"journal de integracion invalido: {journal_path}"
    if journal.get("status") == "completed":
        return str(journal["result"]), str(journal.get("detail", message))

    main_head = _git(workdir, "rev-parse", "HEAD").stdout.decode().strip()
    if journal.get("status") == "started":
        before = str(journal.get("main_head", ""))
        parent = _git(workdir, "rev-parse", "HEAD^")
        subject = _git(workdir, "log", "-1", "--format=%s")
        if (main_head != before and parent.returncode == 0
                and parent.stdout.decode().strip() == before
                and subject.stdout.decode().strip() == message):
            completed = {**journal, "status": "completed",
                         "result": "committed", "detail": message}
            _write_json(journal_path, completed)
            return "committed", message
        if main_head != before:
            return "error", "la rama principal cambio durante la integracion"
        staged = _git(workdir, "diff", "--cached", "--name-only")
        staged_names = [line for line in staged.stdout.decode().splitlines() if line]
        if staged_names:
            foreign = [name for name in staged_names if not _allowed(name, allowed)]
            if foreign:
                return "error", "el indice contiene paths ajenos a la tarea"
            committed, detail = commit_fn(workdir, message, allowed)
            if not committed:
                return "error", detail
            completed = {**journal, "status": "completed",
                         "result": "committed", "detail": detail}
            _write_json(journal_path, completed)
            return "committed", detail

    if not path.exists():
        return "error", f"worktree ausente: {path}"
    head = _git(path, "rev-parse", "HEAD").stdout.decode().strip()
    names = _git(path, "diff", "--name-only", base, head)
    changed = [line for line in names.stdout.decode().splitlines() if line]
    foreign = [name for name in changed if not _allowed(name, allowed)]
    if foreign:
        return "error", f"worktree contiene paths ajenos: {', '.join(foreign[:5])}"
    patch = _git(path, "diff", "--binary", base, head)
    if patch.returncode != 0:
        return "error", _text(patch)
    if not patch.stdout:
        _write_json(journal_path, {
            "status": "completed", "result": "empty",
            "detail": "sin cambios propios en el worktree", "main_head": main_head,
        })
        return "empty", "sin cambios propios en el worktree"
    journal = {"status": "started", "main_head": main_head,
               "task_id": str(task.get("id")), "message": message}
    _write_json(journal_path, journal)
    applied = _git(workdir, "apply", "--index", "-", data=patch.stdout)
    if applied.returncode != 0:
        return "error", f"conflicto al integrar {task['id']}: {_text(applied)}"
    committed, detail = commit_fn(workdir, message, allowed)
    if not committed:
        return "error", detail
    _write_json(journal_path, {**journal, "status": "completed",
                               "result": "committed", "detail": detail})
    return "committed", detail


def cleanup(workdir: str, task: dict[str, object]) -> None:
    """Elimina solo el worktree y la rama temporal creados para esta tarea."""
    workspace = task.get("workspace")
    if not isinstance(workspace, dict):
        return
    path = str(workspace.get("path", ""))
    branch = str(workspace.get("branch", ""))
    root = (Path(workdir).resolve() / ".agent" / "worktrees").resolve()
    resolved = Path(path).resolve() if path else None
    if resolved is not None and resolved.parent != root:
        raise RuntimeError(f"worktree fuera del area administrada: {resolved}")
    if path and Path(path).exists():
        removed = _git(workdir, "worktree", "remove", "--force", path)
        if removed.returncode != 0:
            raise RuntimeError(f"no se pudo retirar worktree: {_text(removed)}")
    else:
        _git(workdir, "worktree", "prune")
    if branch and _git(workdir, "show-ref", "--verify", "--quiet",
                       f"refs/heads/{branch}").returncode == 0:
        deleted = _git(workdir, "branch", "-D", branch)
        if deleted.returncode != 0:
            raise RuntimeError(f"no se pudo borrar rama temporal: {_text(deleted)}")
    task.pop("workspace", None)

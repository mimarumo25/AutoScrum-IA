"""Identidad y delegacion jerarquica de unidades de trabajo.

Los agentes proponen; el orquestador decide. Este modulo mantiene esa frontera:
parsea una propuesta acotada, valida que no amplie alcance y materializa hijos
con identidad y linaje persistentes. No invoca modelos ni ejecuta procesos.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from sdd.core import lifecycle


RETURN_CODE = 4
PROPOSAL_PATH = ".agent/delegation.json"
DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_CHILDREN = 6
DEFAULT_MAX_TOTAL = 24
MAX_DELIVERABLES = 4
_BLOCK = re.compile(
    r"<<<DELEGATE>>>\s*(?P<body>.*?)\s*<<<END_DELEGATE>>>", re.S)
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class DelegationError(ValueError):
    """La propuesta no es segura o no conserva el contrato del padre."""


def identity(task_id: str, node: str, parent: dict | None = None,
             depth: int = 0) -> dict[str, object]:
    """Crea una identidad determinista que sobrevive reintentos y reanudaciones."""
    parent = parent or {}
    parent_id = str(parent.get("id") or "")
    lineage = [str(item) for item in (parent.get("lineage") or [])]
    if parent_id:
        lineage.append(parent_id)
    return {
        "id": f"agent:{node}:{task_id}".lower(),
        "name": f"{node}/{task_id}",
        "role": str(node),
        "task_id": str(task_id),
        "parent_id": parent_id or None,
        "depth": int(depth),
        "lineage": lineage,
    }


def ensure_identity(task: dict, parent: dict | None = None) -> dict[str, object]:
    """Adjunta identidad una sola vez; nunca la regenera durante un retry."""
    existing = task.get("agent")
    if isinstance(existing, dict) and existing.get("id"):
        return existing
    depth = int(task.get("depth", 0))
    created = identity(str(task["id"]), str(task["node"]), parent, depth)
    task["agent"] = created
    return created


def allow_delegation(task: dict, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
    depth = int(task.get("depth", 0))
    task["delegation"] = {
        "allowed": task.get("kind") in {"plan", "subtask"} and depth < max_depth,
        "depth": depth,
        "max_depth": max_depth,
        "max_children": DEFAULT_MAX_CHILDREN,
    }


def parse_proposal(text: str) -> dict[str, object] | None:
    """Extrae JSON estricto del bloque de delegacion, si existe."""
    match = _BLOCK.search(text)
    if not match:
        return None
    if len(_BLOCK.findall(text)) != 1:
        raise DelegationError("la respuesta contiene mas de un bloque DELEGATE")
    try:
        proposal = json.loads(match.group("body"))
    except json.JSONDecodeError as error:
        raise DelegationError(f"JSON de delegacion invalido: {error.msg}") from error
    if not isinstance(proposal, dict):
        raise DelegationError("DELEGATE debe contener un objeto JSON")
    return proposal


def write_proposal(workdir: str | Path, proposal: dict[str, object]) -> Path:
    path = Path(workdir) / PROPOSAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def read_proposal(workdir: str | Path) -> dict[str, object]:
    path = Path(workdir) / PROPOSAL_PATH
    if not path.exists():
        raise DelegationError("el agente devolvio delegacion sin manifiesto persistido")
    try:
        proposal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DelegationError("el manifiesto de delegacion no es JSON legible") from error
    if not isinstance(proposal, dict):
        raise DelegationError("el manifiesto de delegacion debe ser un objeto")
    return proposal


def _safe_path(value: object) -> str:
    rel = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(rel)
    if (not rel or path.is_absolute() or ".." in path.parts
            or ":" in path.parts[0] or rel.startswith(".agent/")):
        raise DelegationError(f"ruta de entregable insegura: {rel or '(vacia)'}")
    return rel


def _under(path: str, root: str) -> bool:
    root = str(root).replace("\\", "/")
    return path == root.rstrip("/") or (root.endswith("/") and path.startswith(root))


def _owned(path: str, node: dict) -> bool:
    return any(_under(path, str(root)) for root in (node.get("writes") or []))


def _covered(path: str, parent_paths: set[str]) -> bool:
    return any(_under(path, root) for root in parent_paths)


def _parent_covered(parent_path: str, children: set[str]) -> bool:
    return any(_under(child, parent_path) for child in children)


def _has_path(source: str, target: str,
              dependencies: dict[str, list[str]]) -> bool:
    stack, seen = [source], set()
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(dependencies.get(current, []))
    return False


def _digest(proposal: dict[str, object]) -> str:
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _limits(cfg: dict) -> tuple[int, int, int]:
    budget = cfg.get("budget", cfg)
    return (
        int(budget.get("max_delegation_depth", DEFAULT_MAX_DEPTH)),
        int(budget.get("max_subtasks_per_task", DEFAULT_MAX_CHILDREN)),
        int(budget.get("max_delegated_tasks", DEFAULT_MAX_TOTAL)),
    )


def apply_proposal(tasks: list[dict], parent: dict,
                   proposal: dict[str, object], nodes: dict[str, dict],
                   cfg: dict, workdir: str | Path | None = None) -> list[dict]:
    """Valida y agrega hijos de forma atomica e idempotente."""
    max_depth, max_children, max_total = _limits(cfg)
    depth = int(parent.get("depth", 0))
    if parent.get("kind") not in {"plan", "subtask"} or depth >= max_depth:
        raise DelegationError("esta unidad no tiene autoridad para crear subtareas")
    parent_agent = ensure_identity(parent)
    digest = _digest(proposal)
    if parent.get("status") == "delegated":
        if parent.get("delegation_digest") != digest:
            raise DelegationError("la tarea ya fue delegada con un manifiesto diferente")
        existing = [task for task in tasks
                    if task.get("parent_task_id") == parent.get("id")]
        if len(existing) != len(parent.get("child_ids") or []):
            raise DelegationError("delegacion previa incompleta en el checkpoint")
        return existing

    raw = proposal.get("subtasks")
    if not isinstance(raw, list) or not 2 <= len(raw) <= max_children:
        raise DelegationError(
            f"la delegacion requiere entre 2 y {max_children} subtareas")
    if sum(task.get("kind") == "subtask" for task in tasks) + len(raw) > max_total:
        raise DelegationError(f"se superaria el limite global de {max_total} subtareas")

    parent_paths = {_safe_path(item) for item in (parent.get("deliverables") or [])}
    if not parent_paths:
        raise DelegationError("la tarea padre no declara entregables divisibles")
    parent_frs = {str(item) for item in (parent.get("fr_refs") or [])}
    normalized: list[dict[str, object]] = []
    keys: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise DelegationError("cada subtarea debe ser un objeto JSON")
        key = str(item.get("key") or f"S{index}")
        if not _KEY.fullmatch(key) or key in keys:
            raise DelegationError(f"key de subtarea invalida o repetida: {key}")
        keys.add(key)
        node_id = str(item.get("node") or parent.get("node"))
        node = nodes.get(node_id)
        if not node or not node.get("task_node", False):
            raise DelegationError(f"nodo hijo no ejecutable: {node_id}")
        deliverables = [_safe_path(value) for value in (item.get("deliverables") or [])]
        if not 1 <= len(deliverables) <= MAX_DELIVERABLES:
            raise DelegationError(
                f"{key} debe declarar entre 1 y {MAX_DELIVERABLES} entregables")
        for path in deliverables:
            if not _covered(path, parent_paths):
                raise DelegationError(f"{key} amplia el alcance del padre con {path}")
            if not _owned(path, node):
                raise DelegationError(f"{key} asigna {path} fuera de propiedad de {node_id}")
        fr_refs = [str(value) for value in (item.get("fr_refs") or parent_frs)]
        if not fr_refs or not set(fr_refs) <= parent_frs:
            raise DelegationError(f"{key} contiene fr_refs vacios o ajenos al padre")
        title = str(item.get("title") or "").strip()
        acceptance = str(item.get("acceptance") or "").strip()
        if not title or not acceptance:
            raise DelegationError(f"{key} requiere title y acceptance verificables")
        dependencies = [str(value) for value in (item.get("depends_on") or [])]
        normalized.append({
            "key": key, "index": index, "node": node_id, "title": title,
            "fr_refs": fr_refs, "deliverables": deliverables,
            "depends_on_keys": dependencies,
            "acceptance": acceptance,
            "scope": str(item.get("scope") or parent.get("scope") or node_id),
        })

    dependencies = {str(item["key"]): list(item["depends_on_keys"])
                    for item in normalized}
    for key, required in dependencies.items():
        if key in required or any(dep not in keys for dep in required):
            raise DelegationError(f"dependencias invalidas en {key}")
        if _has_path(key, key, {key: required}) and key in required:
            raise DelegationError(f"ciclo de subtareas en {key}")
    for key in keys:
        for dependency in dependencies[key]:
            if _has_path(dependency, key, dependencies):
                raise DelegationError(f"ciclo de subtareas entre {key} y {dependency}")

    child_paths = {path for item in normalized for path in item["deliverables"]}
    missing = sorted(path for path in parent_paths
                     if not _parent_covered(path, child_paths))
    if missing:
        raise DelegationError("las subtareas no cubren: " + ", ".join(missing))
    owners: dict[str, list[str]] = {}
    for item in normalized:
        for path in item["deliverables"]:
            owners.setdefault(path, []).append(str(item["key"]))
    for path, path_owners in owners.items():
        for left in path_owners:
            for right in path_owners:
                if left >= right:
                    continue
                if not (_has_path(left, right, dependencies)
                        or _has_path(right, left, dependencies)):
                    raise DelegationError(
                        f"{left} y {right} escriben {path} sin serializacion")

    ids = {str(item["key"]): f"{parent['id']}.{item['index']}"
           for item in normalized}
    if any(any(task.get("id") == child_id for task in tasks)
           for child_id in ids.values()):
        raise DelegationError("los ids calculados para las subtareas ya existen")
    children = []
    for item in normalized:
        child_id = ids[str(item["key"])]
        child = {
            "id": child_id,
            "title": item["title"],
            "node": item["node"],
            "fr_refs": item["fr_refs"],
            "deliverables": item["deliverables"],
            "depends_on": [ids[key] for key in item["depends_on_keys"]],
            "acceptance": item["acceptance"],
            "scope": item["scope"],
            "kind": "subtask",
            "status": "pending",
            "parent_task_id": str(parent["id"]),
            "parent_agent_id": str(parent_agent["id"]),
            "depth": depth + 1,
        }
        ensure_identity(child, parent_agent)
        allow_delegation(child, max_depth)
        children.append(child)

    parent.update(
        status="delegated",
        child_ids=[str(child["id"]) for child in children],
        delegation_reason=str(proposal.get("reason") or "tarea dividida por alcance"),
        delegation_digest=digest,
        delegated_by=str(parent_agent["id"]),
    )
    tasks.extend(children)
    if workdir is not None:
        for child in children:
            lifecycle.created(workdir, child)
        lifecycle.delegated(
            workdir, str(parent["id"]), str(parent_agent["id"]),
            [str(child["id"]) for child in children],
            str(parent["delegation_reason"]),
        )
    return children


def rollup(tasks: list[dict], workdir: str | Path | None = None) -> list[str]:
    """Cierra padres delegados cuando todos sus hijos terminaron; es idempotente."""
    completed: list[str] = []
    changed = True
    while changed:
        changed = False
        for parent in tasks:
            if parent.get("status") != "delegated":
                continue
            child_ids = [str(value) for value in (parent.get("child_ids") or [])]
            children = [task for task in tasks if str(task.get("id")) in child_ids]
            if not child_ids or len(children) != len(child_ids):
                continue
            if all(child.get("status") == "done" for child in children):
                parent["status"] = "done"
                completed.append(str(parent["id"]))
                if workdir is not None:
                    lifecycle.done(workdir, str(parent["id"]))
                changed = True
    return completed

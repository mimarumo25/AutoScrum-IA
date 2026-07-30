"""Supervisor scrum: prioriza tareas listas sin decidir su seguridad.

La salida siempre es una permutacion de `ready`. Dependencias, propiedad y
solapamiento siguen siendo decisiones deterministas de taskqueue/G7/worktrees.
"""
import json
import re
from pathlib import Path
from typing import Callable

_FR = re.compile(r"(FR-\d+)")
_ORDER = re.compile(r"<<<ORDER>>>\s*(.*?)\s*<<<END>>>", re.S)

CompleteFn = Callable[[str, str], str]
LogFn = Callable[..., None]


def read_critical_frs(workdir: str | Path) -> set[str]:
    """Obtiene FR presentes en una linea de tags que tambien sea @critical."""
    root = Path(workdir) / "spec/10_product/features"
    critical: set[str] = set()
    if not root.exists():
        return critical
    for path in root.rglob("*.feature"):
        for line in path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            if "@critical" in line:
                critical.update(_FR.findall(line))
    return critical


def _refs(task: dict[str, object]) -> set[str]:
    value = task.get("fr_refs") or []
    return {str(item) for item in value} if isinstance(value, list) else set()


def _deterministic(ready: list[dict[str, object]], critical: set[str],
                   unlocks: dict[str, int]) -> list[dict[str, object]]:
    return sorted(ready, key=lambda task: (
        0 if task.get("kind") == "defect" else 1,
        0 if _refs(task) & critical else 1,
        -unlocks.get(str(task.get("id", "")), 0),
        str(task.get("id", "")),
    ))


def _model_order(base: list[dict[str, object]], critical: set[str],
                 unlocks: dict[str, int],
                 complete_fn: CompleteFn) -> list[str] | None:
    rows = []
    for task in base:
        refs = sorted(_refs(task))
        rows.append(
            f"- {task.get('id')} node={task.get('node')} kind={task.get('kind')} "
            f"fr={','.join(refs) or '-'} critical={'yes' if set(refs) & critical else 'no'} "
            f"unlocks={unlocks.get(str(task.get('id')), 0)}")
    system = (
        "Eres scrum master. Solo ordenas tareas LISTAS; no cambias dependencias, "
        "archivos, estado ni seguridad. Prioriza defectos que desbloquean, FR "
        "criticos y camino critico. Responde exclusivamente "
        "<<<ORDER>>>[\"id\", ...]<<<END>>> con todos los ids una vez.")
    match = _ORDER.search(complete_fn(system, "TAREAS LISTAS:\n" + "\n".join(rows)))
    if not match:
        return None
    parsed = json.loads(match.group(1))
    return [str(item) for item in parsed] if isinstance(parsed, list) else None


def prioritize(ready: list[dict[str, object]], *, critical_frs: set[str],
               slots: int, simulate: bool = True,
               unlocks: dict[str, int] | None = None,
               complete_fn: CompleteFn | None = None,
               log_fn: LogFn | None = None) -> list[dict[str, object]]:
    """Ordena; usa modelo solo si hay mas candidatas que slots disponibles."""
    unlock_counts = unlocks or {}
    base = _deterministic(ready, critical_frs, unlock_counts)
    if len(base) <= max(1, slots) or simulate or complete_fn is None:
        return base
    try:
        ordered_ids = _model_order(base, critical_frs, unlock_counts, complete_fn)
    except Exception:
        ordered_ids = None
    expected = sorted(str(task.get("id")) for task in base)
    if ordered_ids is None or sorted(ordered_ids) != expected:
        if log_fn is not None:
            log_fn("SCRUM", modo="fallback", motivo="orden LLM invalido")
        return base
    rank = {task_id: index for index, task_id in enumerate(ordered_ids)}
    return sorted(base, key=lambda task: rank[str(task.get("id"))])

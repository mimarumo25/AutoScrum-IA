"""Analisis no bloqueante del DAG antes de su firma humana.

No cambia el plan: calcula camino critico, ancho de olas y dependencias que no
estan respaldadas por `context`, para que planner/humano puedan corregirlas.
"""


def _list(task: dict[str, object], key: str) -> list[str]:
    value = task.get(key) or []
    return [str(item) for item in value] if isinstance(value, list) else []


def _related(left: str, right: str) -> bool:
    a, b = left.rstrip("/"), right.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def descendants(tasks: list[dict[str, object]]) -> dict[str, int]:
    """Cantidad transitiva de tareas que cada id desbloquea."""
    children: dict[str, set[str]] = {str(task["id"]): set() for task in tasks}
    for task in tasks:
        for dependency in _list(task, "depends_on"):
            children.setdefault(dependency, set()).add(str(task["id"]))

    def visit(task_id: str, seen: set[str]) -> set[str]:
        found: set[str] = set()
        for child in children.get(task_id, set()):
            if child in seen:
                continue
            found.add(child)
            found.update(visit(child, seen | {child}))
        return found

    return {task_id: len(visit(task_id, {task_id})) for task_id in children}


def analyze(tasks: list[dict[str, object]]) -> dict[str, object]:
    """Resumen de rendimiento y advisories, sin alterar ninguna tarea."""
    by_id = {str(task["id"]): task for task in tasks}
    depths: dict[str, int] = {}

    def depth(task_id: str) -> int:
        if task_id in depths:
            return depths[task_id]
        dependencies = [item for item in _list(by_id[task_id], "depends_on")
                        if item in by_id]
        depths[task_id] = 1 + max((depth(item) for item in dependencies), default=0)
        return depths[task_id]

    for task_id in by_id:
        depth(task_id)

    pending = set(by_id)
    done: set[str] = set()
    widths: list[int] = []
    while pending:
        ready = sorted(task_id for task_id in pending
                       if set(_list(by_id[task_id], "depends_on")) <= done)
        if not ready:
            break
        widths.append(len(ready))
        done.update(ready)
        pending.difference_update(ready)

    advisories = []
    for task in tasks:
        contexts = _list(task, "context")
        for dependency in _list(task, "depends_on"):
            producer = by_id.get(dependency)
            if producer is None:
                continue
            outputs = _list(producer, "deliverables")
            if outputs and not any(_related(context, output)
                                   for context in contexts for output in outputs):
                advisories.append({
                    "task": str(task["id"]), "dependency": dependency,
                    "reason": "ningun deliverable de la dependencia aparece en context",
                })
    return {
        "critical_path": max(depths.values(), default=0),
        "max_ready_wave": max(widths, default=0),
        "waves": len(widths),
        "descendants": descendants(tasks),
        "advisories": advisories,
    }


def log_plan(workdir: str, state: dict[str, object], log_fn) -> None:
    """Publica el analisis antes del gate humano; nunca cambia el plan."""
    from sdd.runtime import taskqueue
    try:
        result = analyze(taskqueue.load_plan(workdir))
    except taskqueue.PlanError:
        return
    log_fn(state, "PLAN_PERFORMANCE",
           camino_critico=result["critical_path"],
           ola_maxima=result["max_ready_wave"], olas=result["waves"])
    for advisory in result["advisories"]:
        log_fn(state, "PLAN_ADVISORY", **advisory)

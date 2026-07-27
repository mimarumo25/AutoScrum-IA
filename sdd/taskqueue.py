#!/usr/bin/env python3
"""Cola de tareas del pipeline: el sprint, en datos.

Antes el orquestador recorria una cadena lineal de nodos y cada nodo resolvia
"todo lo suyo" en una sola llamada al modelo. Eso no escala y no es un equipo:
es una cadena de montaje de una sola pieza. Aqui vive la pieza que faltaba —
el plan se descompone en tareas con dependencias, y un defecto que un nodo no
puede arreglar se convierte en una tarea de otro nodo, no en un reintento ciego.

Estados de una tarea:
  pending  — lista para ejecutarse cuando sus dependencias esten done
  done     — su nodo la ejecuto y todos sus gates dieron verde
  blocked  — un gate la mando a otro dueno; espera a que cierre la tarea D-###

El orquestador solo transporta punteros; el estado vive en .agent/state.json y
la tarea activa se publica en .agent/current_task.json para que el agente la lea.
"""
import json
from pathlib import Path

import yaml

import lifecycle

PLAN_PATH = "spec/30_plan/tasks.yaml"
CURRENT_PATH = ".agent/current_task.json"


class PlanError(RuntimeError):
    """El plan no se puede cargar. G10 deberia haberlo impedido antes."""


def load_plan(workdir) -> list:
    """Lee spec/30_plan/tasks.yaml y lo normaliza al estado interno de la cola."""
    path = Path(workdir) / PLAN_PATH
    if not path.exists():
        raise PlanError(f"no existe {PLAN_PATH}; el planner no dejo plan que ejecutar")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PlanError(f"{PLAN_PATH} ilegible: {' '.join(str(e).split())[:200]}") from e
    raw = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not raw:
        raise PlanError(f"{PLAN_PATH} no contiene una lista 'tasks' no vacia")
    result = [{
        "id": str(t.get("id")),
        "title": str(t.get("title") or t.get("id")),
        "node": str(t.get("node")),
        "fr_refs": [str(x) for x in (t.get("fr_refs") or [])],
        "deliverables": [str(x) for x in (t.get("deliverables") or [])],
        "depends_on": [str(x) for x in (t.get("depends_on") or [])],
        "acceptance": str(t.get("acceptance") or ""),
        "scope": str(t.get("scope") or ""),
        "kind": "plan",
        "status": "pending",
    } for t in raw if isinstance(t, dict)]
    for task in result:
        lifecycle.created(workdir, task)
    return result


def by_id(tasks, tid):
    return next((t for t in tasks if t["id"] == tid), None)


def runnable(tasks):
    """Tareas pending cuyas dependencias ya estan completas.

    Las tareas de defecto van primero: cierran el camino de una tarea bloqueada,
    y dejarlas para el final alarga el bucle sin ganar nada.
    """
    done = {t["id"] for t in tasks if t["status"] == "done"}
    ready = [t for t in tasks if t["status"] == "pending"
             and all(d in done for d in t["depends_on"])]
    ready.sort(key=lambda t: 0 if t["kind"] == "defect" else 1)
    return ready


def next_runnable(tasks):
    """Primera tarea ejecutable; compatibilidad con el supervisor unitario."""
    ready = runnable(tasks)
    return ready[0] if ready else None


def pending(tasks):
    return [t for t in tasks if t["status"] != "done"]


def progress(tasks):
    return sum(1 for t in tasks if t["status"] == "done"), len(tasks)


def mark_done(tasks, tid, workdir=None):
    """Cierra una tarea y libera a quien esperaba por ella.

    Un defecto puede quedar bloqueado por otro defecto mas especifico. Cuando
    el hijo termina, volver a ejecutar toda la cadena usa criterios obsoletos y
    worktrees basados en commits antiguos. Los defectos padres se cierran en
    cascada; una tarea normal (por ejemplo QA) vuelve a ``pending`` para
    revalidar el flujo completo sobre el codigo integrado.
    """
    task = by_id(tasks, tid)
    if task is None:
        return
    task["status"] = "done"
    if workdir is not None:
        lifecycle.done(workdir, tid)
    for other in tasks:
        if other.get("blocked_by") == tid:
            other.pop("blocked_by", None)
            if other.get("kind") == "defect":
                mark_done(tasks, other["id"], workdir)
            else:
                other["status"] = "pending"


def reconcile_completed_defects(tasks):
    """Repara checkpoints antiguos con cadenas de defectos ya resueltas.

    Una corrida puede caer despues de integrar el defecto hijo y antes de que
    ``mark_done`` persista el cierre del padre. ``raised_by`` conserva esa
    relacion incluso cuando ``blocked_by`` ya fue retirado. La reconciliacion
    hace idempotente la reanudacion y devuelve los ids cerrados para poder
    limpiar sus worktrees obsoletos.
    """
    before = {str(task["id"]): task.get("status") for task in tasks}
    for child in list(tasks):
        if child.get("kind") != "defect" or child.get("status") != "done":
            continue
        parent = by_id(tasks, child.get("raised_by"))
        if parent is not None and parent.get("kind") == "defect":
            mark_done(tasks, parent["id"])
    return [str(task["id"]) for task in tasks
            if before.get(str(task["id"])) != "done"
            and task.get("status") == "done"]


def make_defect(tasks, owner, gate_id, findings, blocked_task, seq, workdir=None):
    """Convierte un defecto ajeno en trabajo asignado a su dueno.

    Si el gate manda el defecto a un nodo distinto del que corrio, reintentar el
    nodo actual no arregla nada: el problema esta en el artefacto de otro. Se crea
    una tarea D-### para el dueno y la tarea actual queda bloqueada tras ella.
    """
    tid = f"D-{seq:03d}"
    files = sorted({f["file"] for f in findings})[:6]
    # Entregables derivados de los archivos senalados. Sin esto G0 no tenia nada
    # que verificar en una tarea de defecto: al menos comprueba que los archivos a
    # corregir siguen presentes y no vacios (caza el "fix" que borra o vacia).
    # Se excluyen globs y rutas de gate (agents/*.md, toolchain sin archivo real).
    deliverables = [f for f in files
                    if f and "*" not in f and not f.startswith("agents/")]
    defect = {
        "id": tid,
        "title": f"corregir {gate_id}: {findings[0]['rule']}",
        "node": owner,
        "fr_refs": list(blocked_task.get("fr_refs") or []) if blocked_task else [],
        "deliverables": deliverables,
        "depends_on": [],
        "acceptance": f"{gate_id} en verde sobre {', '.join(files) or 'el artefacto senalado'}",
        "scope": owner,
        "kind": "defect",
        "status": "pending",
        "gate_id": gate_id,
        "findings": findings,
        "raised_by": blocked_task["id"] if blocked_task else None,
    }
    tasks.append(defect)
    if workdir is not None:
        lifecycle.created(workdir, defect)
    if blocked_task is not None:
        blocked_task["status"] = "blocked"
        blocked_task["blocked_by"] = tid
    return defect


def publish_current(workdir, task):
    """Escribe .agent/current_task.json: el unico canal por el que el agente
    sabe que tarea le toca. Sigue siendo repo-as-state, no contexto por chat."""
    path = Path(workdir) / CURRENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_current(workdir):
    path = Path(workdir) / CURRENT_PATH
    if path.exists():
        path.unlink()


def commit_message(node, task):
    """Conventional Commits referenciando FR-### y task_id, como exige CLAUDE.md."""
    if task is None:
        return f"docs({node}): artefactos de {node} con gates en verde"
    ctype = "fix" if task["kind"] == "defect" else ("test" if node == "qa" else "feat")
    scope = task.get("scope") or {"dev_backend": "api", "dev_frontend": "web",
                                  "qa": "tests"}.get(node, node)
    refs = ", ".join(task.get("fr_refs") or [])
    trailer = f"({refs}, {task['id']})" if refs else f"({task['id']})"
    return f"{ctype}({scope}): {task['title']} {trailer}"

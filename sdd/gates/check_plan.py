#!/usr/bin/env python3
"""G10: el plan de tareas es ejecutable por el bucle del orquestador.

Por que existe: los prompts de dev ya decian 'implementas las tareas de
spec/30_plan/tasks.yaml asignadas a tu task_id', pero ningun nodo producia ese
archivo y el orquestador no tenia bucle de tareas. Se ejecutaba todo el sistema
en una sola llamada por rol. Este gate valida el contrato que hace posible el
bucle: ids unicos, dependencias sin ciclos, entregables dentro de los paths del
nodo dueno y cobertura completa de los FR del PRD.

Formato exigido (subconjunto estricto de YAML, a proposito):

    tasks:
      - id: T-001
        title: parser de expresiones
        node: dev_backend
        fr_refs: [FR-001]
        deliverables: [src/domain/parser.py]
        depends_on: []
        acceptance: parse() devuelve AST o error tipado
"""
import argparse
import re
import tomllib
from pathlib import Path

import yaml

from _lib import finding, emit

PLAN = "spec/30_plan/tasks.yaml"
ID_RX = re.compile(r"^T-\d{3}$")
FR_RX = re.compile(r"\bFR-\d{3}\b")
REQUIRED = ("id", "title", "node", "fr_refs", "deliverables", "acceptance")

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--pipeline", required=True)
a = p.parse_args()
wd = Path(a.workdir)

cfg = tomllib.loads(Path(a.pipeline).read_text(encoding="utf-8"))
writes = {n["id"]: n.get("writes", []) for n in cfg["node"]}
task_nodes = {n["id"] for n in cfg["node"] if n.get("task_node")}

plan_path = wd / PLAN
if not plan_path.exists():
    emit([finding(PLAN, 0, "plan-ausente", "el planner no produjo tasks.yaml")])
try:
    doc = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
except yaml.YAMLError as e:
    emit([finding(PLAN, 0, "plan-invalido", " ".join(str(e).split())[:300])])

tasks = doc.get("tasks") if isinstance(doc, dict) else None
if not isinstance(tasks, list) or not tasks:
    emit([finding(PLAN, 0, "plan-vacio", "tasks.yaml debe tener una lista 'tasks' no vacia")])

out = []
seen, by_id = set(), {}

for idx, t in enumerate(tasks, 1):
    where = f"{PLAN}#{idx}"
    if not isinstance(t, dict):
        out.append(finding(PLAN, idx, "tarea-invalida", "cada tarea debe ser un mapa"))
        continue
    tid = str(t.get("id") or "")
    for key in REQUIRED:
        if not t.get(key):
            out.append(finding(PLAN, idx, "campo-faltante", f"{tid or where} sin '{key}'"))
    if tid and not ID_RX.match(tid):
        out.append(finding(PLAN, idx, "id-invalido", f"'{tid}' no cumple T-###"))
    if tid in seen:
        out.append(finding(PLAN, idx, "id-duplicado", f"{tid} aparece mas de una vez"))
    seen.add(tid)
    by_id[tid] = t

    node = t.get("node")
    if node and node not in task_nodes:
        out.append(finding(PLAN, idx, "nodo-invalido",
                           f"{tid} asignada a '{node}'; nodos de tarea: {sorted(task_nodes)}"))
    else:
        allowed = writes.get(node, [])
        for d in (t.get("deliverables") or []):
            d = str(d).replace("\\", "/")
            if not any(d.startswith(w) for w in allowed):
                out.append(finding(PLAN, idx, "entregable-fuera-de-propiedad",
                                   f"{tid}: '{d}' no esta bajo {allowed} de {node}"))
    for fr in (t.get("fr_refs") or []):
        if not FR_RX.fullmatch(str(fr)):
            out.append(finding(PLAN, idx, "fr-invalido", f"{tid}: '{fr}' no cumple FR-###"))

# --- dependencias: existen y no forman ciclo -------------------------------
for tid, t in by_id.items():
    for dep in (t.get("depends_on") or []):
        if str(dep) not in by_id:
            out.append(finding(PLAN, 0, "dependencia-inexistente", f"{tid} depende de '{dep}'"))

WHITE, GREY, BLACK = 0, 1, 2
color = dict.fromkeys(by_id, WHITE)


def cycle_from(tid, stack):
    """DFS con marcado tricolor: un GREY alcanzable de nuevo cierra un ciclo."""
    color[tid] = GREY
    stack.append(tid)
    for dep in (by_id[tid].get("depends_on") or []):
        dep = str(dep)
        if dep not in by_id:
            continue
        if color[dep] == GREY:
            return stack[stack.index(dep):] + [dep]
        if color[dep] == WHITE:
            found = cycle_from(dep, stack)
            if found:
                return found
    color[tid] = BLACK
    stack.pop()
    return None


for tid in list(by_id):
    if color[tid] == WHITE:
        cyc = cycle_from(tid, [])
        if cyc:
            out.append(finding(PLAN, 0, "ciclo-de-dependencias", " -> ".join(cyc)))
            break

# --- cobertura: ningun FR del PRD se queda sin tarea -----------------------
prd = wd / "spec/10_product/prd.md"
if prd.exists():
    planned = {str(fr) for t in tasks if isinstance(t, dict) for fr in (t.get("fr_refs") or [])}
    for fr in sorted(set(FR_RX.findall(prd.read_text(encoding="utf-8", errors="replace")))):
        if fr not in planned:
            out.append(finding(PLAN, 0, "fr-sin-tarea", f"{fr} no lo implementa ninguna tarea"))

qa_tasks = [t for t in tasks if isinstance(t, dict) and t.get("node") == "qa"]
if not qa_tasks:
    out.append(finding(PLAN, 0, "plan-sin-qa", "ninguna tarea asignada a qa"))
elif len(qa_tasks) > 1:
    # G8 (cobertura de @critical) y G9 (suite en verde) verifican el proyecto
    # ENTERO en cada tarea de qa. Si QA se parte en varias tareas, la primera no
    # puede cubrir escenarios que pertenecen a la ultima, y G8 la bloquea sin que
    # el agente pueda arreglarlo. Con los gates actuales, QA es una sola tarea.
    ids = ", ".join(t.get("id", "?") for t in qa_tasks)
    out.append(finding(PLAN, 0, "qa-dividida",
                       f"hay {len(qa_tasks)} tareas de qa ({ids}); G8/G9 verifican el "
                       f"proyecto completo en cada una, asi que QA debe ser UNA sola "
                       f"tarea que logre cobertura total de @critical y suite en verde"))

emit(out)

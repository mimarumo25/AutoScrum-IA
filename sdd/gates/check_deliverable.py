#!/usr/bin/env python3
"""G0: el nodo produjo lo que declaro producir.

Por que existe: los demas gates INSPECCIONAN artefactos. Si el agente no escribio
nada, no hay nada que inspeccionar y todos dan verde sobre el vacio. Ese fue el
modo de fallo real: dev_backend murio con IncompleteRead, no escribio un solo
archivo, y G7/G4/G5 pasaron. Verde vacio no es verde.

Fuentes de la declaracion (ambas se exigen):
  1. must_produce del nodo en pipeline.toml — el contrato fijo del rol.
  2. deliverables de la tarea activa en .agent/current_task.json — el contrato
     variable de la tarea que se esta ejecutando ahora.

Un patron sin coincidencias, o cuyas coincidencias estan todas vacias, es fallo.
"""
import argparse
import json
import tomllib
from pathlib import Path

from _lib import finding, emit

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--node", required=True)
p.add_argument("--pipeline", required=True)
p.add_argument("--min-bytes", type=int, default=1,
               help="contenido util minimo para no considerar el archivo vacio")
a = p.parse_args()

wd = Path(a.workdir)
cfg = tomllib.loads(Path(a.pipeline).read_text(encoding="utf-8"))
node = next((n for n in cfg["node"] if n["id"] == a.node), {})

patterns = [(pat, "must_produce del nodo") for pat in node.get("must_produce", [])]

task_path = wd / ".agent/current_task.json"
task_id = None
if task_path.exists():
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
    except ValueError:
        task = {}
    task_id = task.get("id")
    patterns += [(pat, f"deliverables de {task_id}")
                 for pat in (task.get("deliverables") or [])]


def matches(pattern: str):
    """Coincidencias no vacias de un glob relativo al repo."""
    hits, empty = [], []
    for path in sorted(wd.glob(pattern)):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            body = ""
        (hits if len(body.encode("utf-8")) >= a.min_bytes else empty).append(path)
    return hits, empty


out = []
for pattern, origin in patterns:
    hits, empty = matches(pattern)
    if hits:
        continue
    if empty:
        for path in empty:
            out.append(finding(path.relative_to(wd).as_posix(), 0, "entregable-vacio",
                               f"{pattern} existe pero no tiene contenido ({origin})"))
    else:
        out.append(finding(pattern, 0, "entregable-ausente",
                           f"{a.node} no produjo '{pattern}' ({origin})"))
emit(out)

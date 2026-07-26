#!/usr/bin/env python3
"""G7: el nodo solo puede escribir dentro de sus paths declarados.

Solo juzga lo que ESTE nodo ensucio en ESTA visita. El orquestador deja en
.agent/baseline.txt lo que ya estaba sucio antes de invocar al agente: cuando una
tarea se pone roja no se commitea, y sus archivos siguen en el arbol mientras otro
nodo trabaja. Sin la linea base, G7 le atribuia ese trabajo ajeno al nodo actual
y lo revertia — borrando la tarea que precisamente habia destapado el defecto.
"""
import argparse
import subprocess
import tomllib
from pathlib import Path
from _lib import finding, emit

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--node", required=True)
p.add_argument("--pipeline", required=True)
a = p.parse_args()

cfg = tomllib.loads(Path(a.pipeline).read_text())
allowed = next((n["writes"] for n in cfg["node"] if n["id"] == a.node), [])

baseline_file = Path(a.workdir) / ".agent/baseline.txt"
baseline = set(baseline_file.read_text(encoding="utf-8").splitlines()) \
    if baseline_file.exists() else set()

r = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=a.workdir,
                   capture_output=True, text=True)
out = []
for line in r.stdout.splitlines():
    path = line[3:].strip().strip('"')
    if not path or path in baseline or any(path.startswith(w) for w in allowed):
        continue
    out.append(finding(path, 0, "violacion-de-propiedad",
                       f"{a.node} escribio fuera de {allowed}"))
emit(out)

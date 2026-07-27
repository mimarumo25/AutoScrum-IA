#!/usr/bin/env python3
"""G9: la suite del proyecto se instala, tipa y pasa. Ejecutando, no leyendo.

Por que existe: G8 comprobaba que la cadena '@SCN-003' apareciera en algun archivo
de tests/. Eso es correlacion de texto, no verificacion. En la corrida real 544
lineas de pruebas que ni siquiera podian importar sus modulos pasaron el gate.
Mientras ningun gate EJECUTE, el verde del pipeline no significa nada.

El comando no lo inventa este gate: lo declara el arquitecto en
spec/20_arch/toolchain.yaml, y este gate lo corre.

    language: node
    dir: .              # opcional, relativo al repo
    install: npm ci     # opcional
    typecheck: npm run typecheck   # opcional
    test: npm test      # obligatorio

Clasificacion de fallos (determina a quien se enruta):
  - toolchain-no-declarado  -> spec/20_arch/ -> arquitecto
  - toolchain-no-disponible -> el binario no esta en PATH: ningun agente puede
    arreglarlo escribiendo codigo. El orquestador escala a humano.
  - entorno-sin-red         -> idem, escala a humano.
  - suite-roja / typecheck-rojo / instalacion-fallida -> se atribuyen al archivo
    del repo que aparezca en la salida; el router hace el resto.
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml

from _lib import finding, emit

TOOLCHAIN = "spec/20_arch/toolchain.yaml"
NET_MARKERS = ("ENOTFOUND", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN",
               "getaddrinfo", "Temporary failure in name resolution",
               "Could not resolve host", "network is unreachable")
# Rutas tipo src/x/y.ts o tests\a\b.py que aparecen en la salida de un runner.
PATH_IN_OUTPUT = re.compile(r"[\w./\\-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|go|java|kt|rb|php|cs)\b")

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--timeout", type=int, default=900, help="segundos por comando")
p.add_argument("--steps", default="install,lint,typecheck,security,test,coverage",
               help="pasos a ejecutar, en orden, separados por coma. Cada uno es "
                    "opcional: solo corre si toolchain.yaml lo declara.")
a = p.parse_args()
wd = Path(a.workdir).resolve()


def tail(text: str, n: int = 400) -> str:
    return " ".join((text or "").strip().split())[-n:]


def blame(output: str, fallback: str) -> str:
    """Archivo del repo al que atribuir el fallo. Prefiere codigo sobre prueba:
    una prueba roja suele delatar un defecto de produccion, y el router debe
    llevarlo a su dueno, no dejarlo siempre en QA."""
    hits = []
    for line in (output or "").splitlines():
        # pytest-cov lista cada archivo de produccion aunque el fallo sea una
        # asercion defectuosa de QA. Esas filas no son un traceback y no deben
        # desviar el defecto al backend.
        if re.search(r"\s+\d+\s+\d+\s+\d+%\s*$", line):
            continue
        for raw in PATH_IN_OUTPUT.findall(line):
            parts = raw.replace("\\", "/").lstrip("./").split("/")
            # Los runners imprimen rutas absolutas; se prueba cada sufijo hasta dar
            # con una que exista en el repo. Asi funciona igual en win y en *nix.
            for i in range(len(parts)):
                cand = "/".join(parts[i:])
                if cand and (wd / cand).is_file():
                    hits.append(cand)
                    break
    if not hits:
        return fallback
    non_test = [h for h in hits if not h.startswith(("tests/", "test/"))]
    return (non_test or hits)[0]


def run(step: str, command: str, cwd: Path, out: list) -> bool:
    """Ejecuta un paso. Devuelve False si el pipeline no debe seguir evaluando."""
    argv = shlex.split(command, posix=(os.name != "nt"))
    if not argv:
        return True
    exe = shutil.which(argv[0], path=os.environ.get("PATH"))
    if exe is None:
        out.append(finding(TOOLCHAIN, 0, "toolchain-no-disponible",
                           f"'{argv[0]}' no esta en PATH; el paso '{step}' no se pudo "
                           f"ejecutar. Instalalo en la maquina que corre el pipeline."))
        return False
    argv[0] = exe
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=a.timeout, errors="replace")
    except subprocess.TimeoutExpired:
        out.append(finding(TOOLCHAIN, 0, "suite-colgada",
                           f"'{command}' excedio {a.timeout}s sin terminar"))
        return False
    if proc.returncode == 0:
        return True
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if any(m in output for m in NET_MARKERS):
        out.append(finding(TOOLCHAIN, 0, "entorno-sin-red",
                           f"'{command}' fallo por red: {tail(output, 200)}"))
        return False
    rule = {"install": "instalacion-fallida", "typecheck": "typecheck-rojo",
            "lint": "lint-rojo", "security": "seguridad-rojo",
            "coverage": "cobertura-insuficiente", "test": "suite-roja"}.get(step, "comando-fallido")
    default = {"install": "package.json", "typecheck": TOOLCHAIN}.get(step, TOOLCHAIN)
    out.append(finding(blame(output, default), 0, rule,
                       f"`{command}` salio {proc.returncode}: {tail(output)}"))
    return False


out = []
spec = wd / TOOLCHAIN
if not spec.exists():
    emit([finding(TOOLCHAIN, 0, "toolchain-no-declarado",
                  "sin toolchain.yaml no hay forma de ejecutar la suite; el "
                  "arquitecto debe declarar install/typecheck/test")])

try:
    tc = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
except yaml.YAMLError as e:
    emit([finding(TOOLCHAIN, 0, "toolchain-invalido", tail(str(e)))])
if not isinstance(tc, dict):
    emit([finding(TOOLCHAIN, 0, "toolchain-invalido", "la raiz debe ser un mapa")])

cwd = (wd / str(tc.get("dir") or ".")).resolve()
if not cwd.is_dir():
    emit([finding(TOOLCHAIN, 0, "toolchain-invalido", f"dir '{tc.get('dir')}' no existe")])
if not str(tc.get("test") or "").strip():
    emit([finding(TOOLCHAIN, 0, "toolchain-sin-test",
                  "toolchain.yaml debe declarar el comando 'test'")])


def tree_hash() -> str:
    """Huella de src/ + tests/ + toolchain.yaml. Ejecutar la suite completa una
    vez por cada tarea de QA es caro; si el arbol no cambio desde el ultimo verde,
    el resultado seria identico (la propia regla de QA exige suites deterministas).
    """
    import hashlib
    h = hashlib.sha256()
    h.update(spec.read_bytes())
    for root in ("src", "tests"):
        for f in sorted((wd / root).rglob("*")) if (wd / root).exists() else []:
            if not f.is_file() or {"node_modules", "__pycache__", ".git"} & set(f.parts):
                continue
            h.update(f.relative_to(wd).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


cache = wd / ".agent/g9_last_pass.txt"
huella = tree_hash()
if os.environ.get("SDD_G9_CACHE", "1") != "0" and cache.exists() \
        and cache.read_text(encoding="utf-8").strip() == huella:
    print("  [G9] arbol identico al ultimo verde; suite no reejecutada", file=sys.stderr)
    emit([])

for step in [s.strip() for s in a.steps.split(",") if s.strip()]:
    command = str(tc.get(step) or "").strip()
    if not command:
        continue          # paso opcional no declarado
    if not run(step, command, cwd, out):
        break             # sin instalar no tiene sentido tipar, sin tipar no tiene sentido probar

if not out:               # suite en verde: memoriza la huella para no repetirla
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(huella, encoding="utf-8")
emit(out)

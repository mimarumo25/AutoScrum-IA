#!/usr/bin/env python3
"""G6: todo import local resuelve a un archivo que existe.

Por que existe: QA escribio seis pruebas que importaban src/calculator.js,
src/parser.js y src/evaluator.js — ninguno de los tres existia — y el pipeline
dio verde. Este gate es deterministico, no necesita toolchain instalado ni red,
y habria cazado ese fallo completo antes de tocar npm.

Alcance deliberado: SOLO especificadores locales.
  - JS/TS: los que empiezan por './', '../' o '/'. Los desnudos ('react') son
    paquetes del gestor y no son asunto de este gate.
  - Python: los relativos ('from .x') y los absolutos cuyo primer segmento es un
    directorio de primer nivel del repo ('from src.api.x'). 'import os' se ignora.

Un falso positivo aqui bloquea el pipeline, asi que ante la duda no se reporta.
"""
import argparse
import re
from pathlib import Path

from _lib import source_files, finding, emit

JS_EXT = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".vue", ".svelte"]
PY_EXT = [".py"]

# from './x' | import './x' | require('./x') | import('./x') | export ... from './x'
JS_SPEC = re.compile(
    r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*|\bimport\s+)['"](?P<spec>[^'"]+)['"]""")
PY_FROM = re.compile(r"^\s*from\s+(?P<mod>\.*[A-Za-z0-9_.]*)\s+import\s+")
PY_IMPORT = re.compile(r"^\s*import\s+(?P<mod>[A-Za-z_][A-Za-z0-9_.]*)")

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--roots", default="src,tests",
               help="directorios de primer nivel a analizar, separados por coma")
a = p.parse_args()
wd = Path(a.workdir).resolve()
roots = [r.strip() for r in a.roots.split(",") if r.strip()]

# Directorios de primer nivel del repo: definen que import absoluto es "local".
top_level = {d.name for d in wd.iterdir() if d.is_dir() and not d.name.startswith(".")}


def resolves(base: Path, exts) -> bool:
    """base sin extension: ¿existe base, base+ext o base/index+ext?"""
    if base.is_file():
        return True
    for ext in exts:
        if base.with_suffix(base.suffix + ext).is_file() or (base / f"index{ext}").is_file():
            return True
    if base.is_dir() and any((base / f"__init__{e}").is_file() for e in exts):
        return True
    return False


def check_js(path: Path, out):
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.lstrip().startswith(("//", "*", "/*")) or "gate-ignore" in line:
            continue
        for m in JS_SPEC.finditer(line):
            spec = m.group("spec")
            if not spec.startswith((".", "/")):
                continue  # paquete del gestor, fuera de alcance
            base = (wd / spec.lstrip("/")) if spec.startswith("/") else (path.parent / spec)
            if not resolves(base.resolve(), JS_EXT):
                out.append(finding(path.relative_to(wd).as_posix(), i, "import-no-resuelve",
                                   f"'{spec}' no existe en el repo"))


def check_py(path: Path, out):
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.lstrip().startswith("#") or "gate-ignore" in line:
            continue
        m = PY_FROM.match(line) or PY_IMPORT.match(line)
        if not m:
            continue
        mod = m.group("mod")
        if mod.startswith("."):                       # relativo: sube un nivel por punto
            dots = len(mod) - len(mod.lstrip("."))
            base = path.parent
            for _ in range(dots - 1):
                base = base.parent
            tail = mod.lstrip(".")
            base = base / Path(*tail.split(".")) if tail else base
        else:
            head = mod.split(".")[0]
            if head not in top_level:                 # stdlib o paquete instalado
                continue
            base = wd / Path(*mod.split("."))
        if not resolves(base, PY_EXT):
            out.append(finding(path.relative_to(wd).as_posix(), i, "import-no-resuelve",
                               f"'{mod}' no existe en el repo"))


out = []
for root in roots:
    for f in source_files(wd / root):
        (check_py if f.suffix in PY_EXT else check_js)(f, out)
emit(out)

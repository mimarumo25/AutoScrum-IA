#!/usr/bin/env python3
"""G1: todo FR tiene escenario. G8: todo escenario critico tiene prueba."""
import argparse
import re
from pathlib import Path
from _lib import finding, emit

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--mode", choices=["product", "qa"], required=True)
a = p.parse_args()
wd = Path(a.workdir)
features = list((wd / "spec/10_product/features").rglob("*.feature"))
out = []

if a.mode == "product":
    prd = wd / "spec/10_product/prd.md"
    frs = set(re.findall(r"\bFR-\d{3}\b", prd.read_text())) if prd.exists() else set()
    if not features:
        out.append(finding("spec/10_product/features", 0, "sin-escenarios", "no hay archivos .feature"))
    body = "\n".join(f.read_text() for f in features)
    for fr in sorted(frs):
        if fr not in body:
            out.append(finding("spec/10_product", 0, "fr-sin-escenario", f"{fr} sin escenario Gherkin"))
    scn = re.findall(r"@(SCN-\d{3})", body)
    for s in sorted({x for x in scn if scn.count(x) > 1}):
        out.append(finding("spec/10_product", 0, "id-duplicado", f"{s} repetido"))
else:
    test_files = [f for f in (wd / "tests").rglob("*") if f.is_file()]
    tests = "\n".join(f.read_text(errors="replace") for f in test_files)
    for f in features:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            tags = re.findall(r"@(SCN-\d{3}|critical)", line)
            ids = [t for t in tags if t.startswith("SCN")]
            norm = re.sub(r"[-_]", "", tests)
            if ids and "critical" in tags and re.sub(r"[-_]", "", ids[0]) not in norm:
                out.append(finding(f, i, "escenario-critico-sin-prueba", ids[0]))
    disabled = re.compile(
        r"(?:pytest\.mark\.(?:xfail|skip|skipif)|unittest\.(?:skip|skipIf|skipUnless))"
    )
    for test_file in test_files:
        for line_no, line in enumerate(
                test_file.read_text(errors="replace").splitlines(), 1):
            if disabled.search(line):
                out.append(finding(
                    test_file, line_no, "prueba-desactivada",
                    "QA no puede ocultar fallos con skip/skipif/xfail; debe corregir "
                    "el producto o mantener la suite roja"))
emit(out)

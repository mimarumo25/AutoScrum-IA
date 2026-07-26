#!/usr/bin/env python3
"""G4: limite de lineas por archivo. Sustituible por eslint max-lines / ruff."""
import argparse
from pathlib import Path
from _lib import source_files, finding, emit

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
p.add_argument("--hard", type=int, default=500)
p.add_argument("--warn", type=int, default=300)
a = p.parse_args()

out = []
for f in source_files(Path(a.workdir) / "src"):
    n = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
    if n > a.hard:
        out.append(finding(f, n, "max-lines", f"{n} lineas, limite duro {a.hard}"))
emit(out)

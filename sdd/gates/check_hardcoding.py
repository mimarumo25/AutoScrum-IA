#!/usr/bin/env python3
"""G5: valores dependientes del entorno en codigo + contrato .env incompleto."""
import argparse
import re
from pathlib import Path
from _lib import source_files, finding, emit

RULES = [
    ("hardcoded-url", re.compile(r"[\"'`]https?://(?!localhost|example\.|schemas?\.|www\.w3\.org)[^\"'`\s]+")),
    ("hardcoded-secret", re.compile(r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*[\"'][^\"']{8,}[\"']")),
    ("hardcoded-port", re.compile(r"(?i)\b(port|puerto)\s*[:=]\s*\d{2,5}\b")),
    ("hardcoded-dsn", re.compile(r"[\"'](postgres|postgresql|mysql|mongodb|redis|amqp)://[^\"']+[\"']")),
]
ENV_USE = re.compile(r"(?:process\.env\.([A-Z][A-Z0-9_]+)|os\.environ(?:\.get)?[\[(]\s*[\"']([A-Z][A-Z0-9_]+)[\"'])")

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
a = p.parse_args()
wd = Path(a.workdir)

declared = set()
contract = wd / "spec/20_arch/env-contract.yaml"
if contract.exists():
    declared = set(re.findall(r"^\s*-?\s*name:\s*([A-Z][A-Z0-9_]+)", contract.read_text(), re.M))
example = set()
if (wd / ".env.example").exists():
    example = set(re.findall(r"^([A-Z][A-Z0-9_]+)\s*=", (wd / ".env.example").read_text(), re.M))

out = []
for f in source_files(wd / "src"):
    for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if "gate-ignore" in line:
            continue
        for rule, rx in RULES:
            m = rx.search(line)
            if m:
                out.append(finding(f, i, rule, m.group(0)[:80]))
        for m in ENV_USE.finditer(line):
            var = m.group(1) or m.group(2)
            if var not in declared:
                out.append(finding(f, i, "env-no-declarada", f"{var} ausente de env-contract.yaml"))
            elif var not in example:
                # Este es tambien el guardian del doble-dueno de .env.example: G5
                # escanea TODO src/, asi que si dev_frontend sobreescribe el archivo
                # y borra una variable que dev_backend usa en src/api, aqui salta
                # env-sin-ejemplo en la corrida de frontend. La sobreescritura no es
                # silenciosa: falla el gate. (Residuo: una variable declarada pero
                # aun no usada por codigo no queda cubierta hasta que ese codigo se
                # escribe; es una ventana estrecha y aceptada.)
                out.append(finding(f, i, "env-sin-ejemplo", f"{var} ausente de .env.example"))
emit(out)

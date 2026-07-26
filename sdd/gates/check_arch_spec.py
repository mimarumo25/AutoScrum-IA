#!/usr/bin/env python3
"""G2: NFR medibles, ADR con alternativas y coste, contrato de entorno presente."""
import argparse
import re
import tomllib
from pathlib import Path
from _lib import finding, emit

p = argparse.ArgumentParser()
p.add_argument("--workdir", required=True)
a = p.parse_args()
ROOT = Path(__file__).resolve().parent.parent
arch = Path(a.workdir) / "spec/20_arch"
out = []

# gate_id de un NFR debe apuntar a algo que EXISTA. Antes se aceptaba cualquier
# cadena (el demo declaraba 'G11', un gate fantasma), asi que un NFR podia decir
# "me verifica G11" sin que nada lo verificara jamas. 'manual' es la unica salida
# honesta cuando el umbral no lo comprueba ninguna maquina.
try:
    registry = tomllib.loads((ROOT / "gates/registry.toml").read_text(encoding="utf-8"))
    known_gates = {g["id"] for g in registry["gate"]} | {"manual"}
except (OSError, tomllib.TOMLDecodeError):
    known_gates = {"manual"}

for req in ["nfr.yaml", "api/openapi.yaml", "env-contract.yaml", "threat-model.md"]:
    if not (arch / req).exists():
        out.append(finding(f"spec/20_arch/{req}", 0, "artefacto-faltante", req))

nfr = arch / "nfr.yaml"
if nfr.exists():
    for block in re.split(r"\n(?=\s*-\s)", nfr.read_text()):
        if "id:" not in block:
            continue
        nid = re.search(r"id:\s*(\S+)", block).group(1)
        for key in ["umbral", "metrica", "gate_id"]:
            if key not in block:
                out.append(finding("spec/20_arch/nfr.yaml", 0, "nfr-no-medible",
                                   f"{nid} sin campo {key}"))
        gid = re.search(r"gate_id:\s*(\S+)", block)
        if gid and gid.group(1) not in known_gates:
            out.append(finding("spec/20_arch/nfr.yaml", 0, "nfr-gate-inexistente",
                               f"{nid} dice verificarse con '{gid.group(1)}', que no existe; "
                               f"usa un gate real o 'manual'"))

for adr in sorted((arch / "adr").glob("*.md")) if (arch / "adr").exists() else []:
    t = adr.read_text().lower()
    if t.count("alternativa") < 2:
        out.append(finding(adr, 0, "adr-sin-alternativas", "menos de 2 alternativas descartadas"))
    if not re.search(r"(usd|coste|costo)", t):
        out.append(finding(adr, 0, "adr-sin-coste", "sin coste mensual estimado"))
emit(out)

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

# Los artefactos van en INGLES (CLAUDE.md); este gate exigia los substrings
# españoles 'umbral' y 'metrica', asi que cada entrada NFR conforme generaba dos
# hallazgos falsos. Y era substring: la palabra 'metrica' dentro del texto de un
# valor contaba como campo presente. Ahora se exige la CLAVE, anclada a la linea.
NFR_FIELDS = {
    "threshold": ("threshold", "umbral"),
    "metric": ("metric", "metrica", "métrica"),
    "gate_id": ("gate_id",),
}
# 'id:' anclado: sin el ancla, un bloque que solo tiene 'gate_id:' pasaba el
# guard (contiene la subcadena 'id:') y luego se tomaba el valor del gate como
# si fuera el id del NFR.
NFR_ID = re.compile(r"^\s*(?:-\s*)?id:\s*(\S+)", re.M)
# El identificador puede venir entrecomillado: 'gate_id: "G9"' es YAML valido, y
# con (\S+) las comillas entraban en el identificador y no casaban con el registro.
NFR_GATE = re.compile(r"gate_id:\s*[\"']?([A-Za-z0-9_.\-]+)[\"']?")

# Tres formas REALES de declarar alternativas en las corridas de este pipeline,
# las tres conformes con agents/architect.md:
#   '## Alternativas consideradas' + items numerados   -> encabezado markdown
#   '**Alternativas descartadas**:' + items numerados  -> etiqueta en negrita
#   'Alternativa descartada: A.' una por linea         -> prosa enumerada
#   '| Opcion | Justificacion de descarte |' + filas    -> tabla markdown
# El gate solo entendia la tercera, y a medias: contaba la palabra en todo el
# archivo, asi que las otras tres (una etiqueta + N alternativas) daban 1 y se
# reprobaban.
ALT_HEADING = re.compile(r"^(#{1,6})\s*[^\n]*alternativ", re.M | re.I)
ALT_BOLD_LABEL = re.compile(r"^\*\*[^\n]*alternativ", re.M | re.I)
TOP_ITEM = re.compile(r"^(?:\d+[.)]|[-*+])\s+\S", re.M)
SUB_HEADING = re.compile(r"^#{3,6}\s+\S", re.M)
ANY_ITEM = re.compile(r"^[ \t]+(?:\d+[.)]|[-*+])\s+\S", re.M)
NAMED_ALT = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*\**alternativ\w*\b", re.M | re.I)
PIPE_LINE = re.compile(r"^\|.*$", re.M)
TABLE_SEP = re.compile(r"^\|[\s:|-]+\|\s*$", re.M)


def _table_rows(section: str) -> int:
    """Filas de datos de las tablas de la seccion.

    Cada tabla aporta una cabecera y un separador que no son alternativas; se
    descuentan dos lineas por separador encontrado, asi el conteo funciona
    tambien si hay mas de una tabla.
    """
    return max(0, len(PIPE_LINE.findall(section))
               - 2 * len(TABLE_SEP.findall(section)))


def _alternatives_section(text: str) -> str | None:
    """Cuerpo de la seccion de alternativas, o None si no hay tal seccion."""
    heading = ALT_HEADING.search(text)
    bold = ALT_BOLD_LABEL.search(text)
    candidates = [match for match in (heading, bold) if match is not None]
    if not candidates:
        return None
    label = min(candidates, key=lambda match: match.start())
    # Un encabezado solo lo cierra otro del mismo nivel o superior: si las
    # alternativas SON sub-encabezados, cortar en el primero las ocultaria. Una
    # etiqueta en negrita la cierra cualquier encabezado o la siguiente etiqueta.
    level = len(label.group(1)) if label is heading else 6
    rest = text[label.end():]
    end = re.search(rf"^(?:#{{1,{level}}}\s+|\*\*)", rest, re.M)
    return rest[:end.start()] if end else rest


def discarded_alternatives(text: str) -> int:
    """Cuenta alternativas descartadas por ESTRUCTURA, no por vocabulario.

    Se toma el maximo entre "items dentro de la seccion" y "lineas que nombran
    una alternativa" para cubrir las tres formas sin cambiar un falso positivo
    por otro: una regla solo estructural reprobaria los ADR en prosa, y una solo
    de vocabulario es la que reprobaba los ADR con seccion.
    """
    enumerated = 0
    section = _alternatives_section(text)
    if section is not None:
        enumerated = (max(len(TOP_ITEM.findall(section)),
                          len(SUB_HEADING.findall(section)),
                          _table_rows(section))
                      or len(ANY_ITEM.findall(section)))
    return max(enumerated, len(NAMED_ALT.findall(text)))


nfr = arch / "nfr.yaml"
if nfr.exists():
    for block in re.split(r"\n(?=\s*-\s)", nfr.read_text(encoding="utf-8")):
        nid_match = NFR_ID.search(block)
        if nid_match is None:
            continue
        nid = nid_match.group(1)
        for canonical, aliases in NFR_FIELDS.items():
            if not any(re.search(rf"^\s*{alias}\s*:", block, re.M) for alias in aliases):
                out.append(finding("spec/20_arch/nfr.yaml", 0, "nfr-no-medible",
                                   f"{nid} sin campo {canonical}"))
        gid = NFR_GATE.search(block)
        if gid and gid.group(1) not in known_gates:
            out.append(finding("spec/20_arch/nfr.yaml", 0, "nfr-gate-inexistente",
                               f"{nid} dice verificarse con '{gid.group(1)}', que no existe; "
                               f"usa un gate real o 'manual'"))

for adr in sorted((arch / "adr").glob("*.md")) if (arch / "adr").exists() else []:
    raw = adr.read_text(encoding="utf-8", errors="replace")
    if discarded_alternatives(raw) < 2:
        out.append(finding(adr, 0, "adr-sin-alternativas", "menos de 2 alternativas descartadas"))
    # Mismo defecto que las claves NFR: los ADR reales escriben '## Cost
    # estimate' y '$0/month' en INGLES, como exige CLAUDE.md, y la regex solo
    # aceptaba vocabulario español. 'cost' cubre tambien 'coste' y 'costo' por
    # subcadena; el importe se exige como cifra ('$0'), no como '$' suelto, para
    # que un '$VAR' de un fragmento de shell no cuente como coste declarado.
    if not re.search(r"(usd|cost|\$\s*\d)", raw.lower()):
        out.append(finding(adr, 0, "adr-sin-coste", "sin coste mensual estimado"))
emit(out)

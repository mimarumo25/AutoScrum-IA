#!/usr/bin/env python3
"""R1: revision critica del artefacto de un nodo de especificacion.

CATEGORIA DISTINTA A LOS GATES G*. Un gate G* es codigo determinista sin juicio.
Este tiene juicio: es un modelo leyendo el trabajo de otro modelo. Por eso lleva
prefijo R y por eso esta acotado por diseno:

  - solo puede ANADIR defectos, jamas relajar un gate determinista;
  - corre DESPUES de los G* del nodo y solo si estan verdes (skip_if_prior_failed
    en registry.toml): no se gasta una llamada al modelo criticando un artefacto
    que ya se sabe rojo;
  - solo los hallazgos `blocking` frenan el pipeline. Los `mejora` se registran y
    salen en el reporte final como backlog;
  - tope de rondas por nodo. Agotado el tope se sigue adelante DEJANDO CONSTANCIA,
    porque sobre un artefacto subjetivo un revisor exigente no converge nunca.

Si el revisor falla o responde algo ilegible, este gate PASA y lo registra en
.agent/review/<node>.json. Es deliberado: los G* deterministas siguen sosteniendo
la correccion, y tumbar la corrida porque el critico se cayo cuesta mas de lo que
protege. Lo que no hace es callarselo — el reporte final lo dice.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # para importar providers

from _lib import finding, emit  # noqa: E402

REVIEW_BLOCK = re.compile(r"<<<REVIEW>>>\s*(?P<body>.*?)\s*<<<END>>>", re.S)
SEVERITIES = ("blocking", "mejora")

# Que lee el revisor de cada nodo: su propio artefacto mas los insumos con los que
# debe ser coherente. Sin los insumos no puede juzgar alcance ni trazabilidad.
CONTEXT = {
    "product": ["spec/00_intake.yaml", "spec/10_product/**/*"],
    "architect": ["spec/00_intake.yaml", "spec/10_product/**/*", "spec/20_arch/**/*"],
    "planner": ["spec/00_intake.yaml", "spec/10_product/**/*",
                "spec/20_arch/nfr.yaml", "spec/20_arch/api/openapi.yaml",
                "spec/20_arch/toolchain.yaml", "spec/30_plan/**/*"],
    # Nodos de codigo (R2): el contexto real son los archivos de la tarea activa,
    # que se anaden dinamicamente desde current_task.json (ver gather).
    "dev_backend": ["spec/20_arch/api/openapi.yaml"],
    "dev_frontend": ["spec/20_arch/api/openapi.yaml"],
    "qa": ["spec/10_product/features/**/*"],
}
READABLE = {".yaml", ".yml", ".md", ".sql", ".json", ".feature", ".mmd",
            ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
MAX_CHARS = int(os.environ.get("SDD_REVIEW_CTX_CHARS", "120000"))


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"rounds": 0, "invocations": 0, "historial": [], "mejoras": [], "nota": ""}


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def active_task(workdir: Path) -> dict:
    tp = workdir / ".agent/current_task.json"
    if tp.exists():
        try:
            return json.loads(tp.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def gather(workdir: Path, node: str) -> str:
    patterns = list(CONTEXT.get(node, ["spec/**/*"]))
    # Para nodos de codigo, el objeto de la revision son los entregables de la
    # tarea activa (no todo el repo): mismo criterio de contexto acotado del agente.
    task = active_task(workdir)
    patterns += [d for d in (task.get("deliverables") or []) if "*" not in d]
    patterns += list(task.get("context") or [])
    chunks, total, seen = [], 0, set()
    for pattern in patterns:
        for f in sorted(workdir.glob(pattern)):
            rel = f.relative_to(workdir).as_posix()
            if not f.is_file() or f.suffix not in READABLE or rel in seen:
                continue
            seen.add(rel)
            body = f.read_text(encoding="utf-8", errors="replace")
            if total + len(body) > MAX_CHARS:
                chunks.append("### (artefactos restantes omitidos por presupuesto)")
                return "\n\n".join(chunks)
            total += len(body)
            chunks.append(f"### {rel}\n{body}")
    return "\n\n".join(chunks)


def ask_model(system: str, user: str) -> str:
    import providers
    # El revisor puede correr en otro modelo que el autor: un critico que es
    # literalmente el mismo modelo tiende a validar su propio criterio.
    override = os.environ.get("SDD_REVIEW_MODEL")
    previo = os.environ.get("SDD_MODEL")
    if override:
        os.environ["SDD_MODEL"] = override
    try:
        return providers.complete(system, user)
    finally:
        if override:
            os.environ.pop("SDD_MODEL", None)
            if previo is not None:
                os.environ["SDD_MODEL"] = previo


def ask_simulated(node: str, invocacion: int) -> str:
    """Revisor de guion para `--simulate`: ejercita el ciclo sin gastar tokens.

    product recibe un blocking la primera vez y queda limpio a la segunda, asi el
    demo demuestra el reintento. architect y planner reciben solo mejoras, asi
    demuestra que lo no bloqueante se registra sin frenar nada.
    """
    guion = {
        "product": [
            [{"severity": "blocking", "file": "spec/10_product/prd.md", "line": 2,
              "rule": "requisito-sin-caso-negativo",
              "evidence": "FR-001 solo describe la renovacion exitosa; no dice que "
                          "pasa si el estudiante no tiene cupo, y el backend tendra "
                          "que inventarse el comportamiento"},
             {"severity": "mejora", "file": "spec/10_product/prd.md", "line": 3,
              "rule": "prd-sin-glosario",
              "evidence": "'sede' y 'acudiente' se usan sin definir"}],
            [],
        ],
        "architect": [[{"severity": "mejora", "file": "spec/20_arch/nfr.yaml", "line": 0,
                        "rule": "nfr-unico",
                        "evidence": "solo hay un NFR de rendimiento; no hay ninguno de "
                                    "disponibilidad ni de tamano de datos"}]],
        "planner": [[{"severity": "mejora", "file": "spec/30_plan/tasks.yaml", "line": 0,
                      "rule": "tareas-serializadas",
                      "evidence": "T-003 depende de T-002 pero el frontend trabaja "
                                  "contra el contrato, no contra el handler"}]],
    }
    rondas = guion.get(node, [[]])
    hallazgos = rondas[min(invocacion, len(rondas) - 1)]
    return "<<<REVIEW>>>\n" + json.dumps({"findings": hallazgos}) + "\n<<<END>>>"


def _extract_doc(text: str):
    """Encuentra el objeto de revision en la respuesta, tolerando formato.

    Los modelos no siempre respetan el envoltorio <<<REVIEW>>> (DeepSeek en
    particular). Se intenta, en orden: (1) el bloque exacto; (2) un bloque de
    codigo ```json … ```; (3) el primer objeto {...} con clave "findings" que
    parsee. Endurecer aqui evita perder revisiones reales por un envoltorio omitido.
    """
    text = text or ""
    m = REVIEW_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group("body")), None
        except ValueError as e:
            return None, f"JSON invalido en el bloque REVIEW: {e}"
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1)), None
        except ValueError:
            pass
    # Ultimo recurso: cada objeto {...} de nivel superior que contenga "findings".
    for mm in re.finditer(r"\{.*?\"findings\".*?\}", text, re.S):
        frag = mm.group(0)
        for end in range(len(frag), 0, -1):      # recorta hasta que parsee
            try:
                doc = json.loads(frag[:end])
            except ValueError:
                continue
            if isinstance(doc, dict) and "findings" in doc:
                return doc, None
            break
    return None, "el revisor no emitio findings en un formato reconocible"


def parse(text: str):
    """Extrae y valida los hallazgos. Devuelve (hallazgos, error_o_None)."""
    doc, err = _extract_doc(text)
    if err:
        return [], err
    crudos = doc.get("findings") if isinstance(doc, dict) else None
    if not isinstance(crudos, list):
        return [], "el objeto de revision no contiene una lista 'findings'"
    limpios = []
    for f in crudos:
        if not isinstance(f, dict) or not f.get("evidence"):
            continue
        sev = str(f.get("severity", "mejora")).lower()
        limpios.append({
            "severity": sev if sev in SEVERITIES else "mejora",
            "file": str(f.get("file") or "spec/").replace("\\", "/"),
            "line": int(f["line"]) if str(f.get("line", "")).isdigit() else 0,
            "rule": str(f.get("rule") or "hallazgo-de-revision"),
            "evidence": " ".join(str(f["evidence"]).split())[:400],
        })
    return limpios, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--max-rounds", type=int, default=2)
    p.add_argument("--prompt", required=True, help="ruta a agents/reviewer.md")
    p.add_argument("--label", default="R1", help="etiqueta para el log (R1 spec, R2 codigo)")
    a = p.parse_args()

    wd = Path(a.workdir)
    # El estado se lleva por nodo Y por tarea activa: un nodo de codigo se revisa
    # una vez por tarea, y las rondas de T-001 no deben gastar el presupuesto de
    # T-002. Los nodos de spec no tienen tarea activa, asi que la clave es el nodo.
    task = active_task(wd)
    key = f"{a.node}.{task['id']}" if task.get("id") else a.node
    spath = wd / f".agent/review/{key}.json"
    state = load_state(spath)

    if state["rounds"] >= a.max_rounds:
        state["nota"] = (f"tope de {a.max_rounds} ronda(s) de revision alcanzado; "
                         f"se continuo sin bloquear")
        save_state(spath, state)
        print(f"  [{a.label}] {key}: {state['nota']}", file=sys.stderr)
        emit([])

    system = Path(a.prompt).read_text(encoding="utf-8")
    user = (f"NODO BAJO REVISION: {a.node}"
            + (f" · tarea {task['id']}" if task.get("id") else "") + "\n"
            f"Aplica la rubrica de '{a.node}' de tu system prompt.\n\n"
            f"ARTEFACTOS:\n{gather(wd, a.node)}")

    try:
        if os.environ.get("SDD_SIMULATE"):
            raw = ask_simulated(a.node, state["invocations"])
        else:
            raw = ask_model(system, user)
        hallazgos, error = parse(raw)
    except Exception as e:  # noqa: BLE001 — un critico caido no tumba la corrida
        hallazgos, error = [], f"{type(e).__name__}: {e}"

    state["invocations"] += 1
    if error:
        # Se pasa, pero queda escrito. El reporte final lo saca a la superficie.
        state["nota"] = f"revision no disponible: {error}"
        save_state(spath, state)
        print(f"  [{a.label}] {key}: {state['nota']}", file=sys.stderr)
        emit([])

    blocking = [f for f in hallazgos if f["severity"] == "blocking"]
    mejoras = [f for f in hallazgos if f["severity"] == "mejora"]
    state["mejoras"] = [m for m in state["mejoras"]
                        if m["rule"] not in {x["rule"] for x in mejoras}] + mejoras
    state["historial"].append({"invocacion": state["invocations"],
                               "blocking": len(blocking), "mejoras": len(mejoras)})
    if blocking:
        state["rounds"] += 1          # solo consume presupuesto la ronda que bloquea
        state["nota"] = ""
    save_state(spath, state)
    print(f"  [{a.label}] {key}: {len(blocking)} blocking, {len(mejoras)} mejora(s)",
          file=sys.stderr)

    emit([finding(f["file"], f["line"], f["rule"], f["evidence"]) for f in blocking])


if __name__ == "__main__":
    main()

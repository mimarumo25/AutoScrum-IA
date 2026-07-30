#!/usr/bin/env python3
"""Agente real de un nodo. Reemplaza a examples/fake_agent.py en modo real.

Mismo contrato de invocacion:  agent.py <node> <workdir>

Flujo (repo-as-state):
  1. Lee el system prompt del nodo (agents/<node>.md).
  2. Lee la idea (spec/00_intake.yaml) y SOLO los artefactos relevantes para su rol
     y su tarea. Antes concatenaba spec/ entero en cada llamada: con un spec grande
     eso revienta la ventana de contexto mucho antes que el proyecto termine.
  3. Lee su tarea de .agent/current_task.json (id, entregables, criterio de
     aceptacion y, si es tarea de defecto, los hallazgos del gate).
  4. Llama al proveedor LLM (providers.complete, con reintento y continuacion).
  5. Parsea bloques de archivo y ESCRIBE solo dentro de los paths permitidos del
     nodo (declarados en pipeline.toml). Lo que quede fuera se omite y se avisa;
     G7 es el respaldo que revierte cualquier fuga que se cuele.

Codigos de salida:
  0  escribio al menos un archivo
  1  fallo (proveedor caido, sin bloques de archivo, nada escrito)
  2  invocacion incorrecta
  3  el agente se declara BLOQUEADO: le falta un insumo que no puede fabricar.
     El orquestador escala a humano en vez de reintentar a ciegas. Esto existe
     porque el frontend, al no encontrar backend, se invento un mock y siguio.
"""
import json
import os
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdd.core import chronicle, config, lifecycle  # noqa: E402
from sdd.integrations import model_router, providers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

FILE_BLOCK = re.compile(r"<<<FILE:\s*(?P<path>[^\n>]+?)\s*>>>\n(?P<body>.*?)\n?<<<END>>>", re.S)
BLOCKED_BLOCK = re.compile(r"<<<BLOCKED:\s*(?P<why>[^\n>]+?)\s*>>>")

# Presupuesto de contexto. No es un limite del modelo: es lo que impide que la
# llamada crezca con el repo hasta hacerse imposible en el proyecto numero 30.
MAX_CHARS_PER_FILE = int(os.environ.get("SDD_CTX_FILE_CHARS", "12000"))
MAX_CHARS_TOTAL = int(os.environ.get("SDD_CTX_TOTAL_CHARS", "160000"))

# Que parte de spec/ necesita cada rol. Todo lo demas es ruido que paga tokens.
NODE_CONTEXT = {
    "product":      ["spec/10_product/**/*"],
    "architect":    ["spec/10_product/**/*"],
    "planner":      ["spec/10_product/**/*", "spec/20_arch/nfr.yaml",
                     "spec/20_arch/api/openapi.yaml", "spec/20_arch/toolchain.yaml",
                     "spec/20_arch/env-contract.yaml", "spec/30_plan/**/*"],
    "dev_backend":  ["spec/20_arch/**/*", "spec/30_plan/tasks.yaml",
                     "spec/10_product/features/**/*"],
    "dev_frontend": ["spec/20_arch/api/openapi.yaml", "spec/20_arch/env-contract.yaml",
                     "spec/20_arch/toolchain.yaml", "spec/30_plan/tasks.yaml",
                     "spec/10_product/features/**/*"],
    # QA prueba TODO el codigo: debe ver src/ entero para importar los simbolos
    # reales (clases, funciones) en vez de adivinarlos. Sin esto, una prueba que
    # importa un nombre inexistente hace que la suite ni arranque.
    "qa":           ["spec/10_product/features/**/*", "spec/20_arch/api/openapi.yaml",
                     "spec/20_arch/nfr.yaml", "spec/20_arch/toolchain.yaml",
                     "spec/30_plan/tasks.yaml", "spec/40_qa/**/*", "src/**/*"],
}

PROTOCOL = """
FORMATO DE SALIDA OBLIGATORIO. Responde EXCLUSIVAMENTE con uno o mas bloques de
archivo, sin prosa antes ni despues, sin ``` de markdown. Cada bloque:

<<<FILE: ruta/relativa/desde/la/raiz>>>
(contenido completo del archivo)
<<<END>>>

Escribe SOLO dentro de estos paths permitidos para tu nodo: {allowed}
No escribas en /tests ni en /spec salvo que tus paths lo incluyan.
Cada archivo debe ser contenido completo y valido, no un diff ni un fragmento.

Si te falta un insumo que NO puedes fabricar dentro de tus paths (por ejemplo, el
modulo que debes consumir no existe todavia), NO lo simules ni lo mockees para
salir del paso: responde con una sola linea

<<<BLOCKED: que falta y quien deberia producirlo>>>

Un mock que tapa un entregable ausente convierte un fallo visible en uno invisible.
"""


def compose_system_prompt(node_cfg: dict, profile: dict) -> str:
    """Compone el prompt efectivo: override portable, addon y capacidades."""
    default_prompt = (ROOT / node_cfg["prompt"]).read_text(encoding="utf-8")
    system_prompt = str(profile.get("system_prompt") or "").strip() or default_prompt
    addon = str(profile.get("prompt_addon") or "").strip()
    if addon:
        system_prompt += ("\n\n## Complemento del operador (aplícalo además de lo anterior)\n"
                          + addon)
    tools = [str(tool) for tool in profile.get("tools", []) if str(tool).strip()]
    if tools:
        system_prompt += ("\n\n## Capacidades habilitadas por el operador\n" +
                          "Utiliza solo estas capacidades: " + ", ".join(tools))
    return system_prompt


def parse_files(text: str):
    """Extrae [(path, body)] de la respuesta del modelo. Tolera fences de markdown."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return [(m.group("path").strip().replace("\\", "/"), m.group("body"))
            for m in FILE_BLOCK.finditer(text)]


def _safe_target(workdir: Path, rel: str):
    """Resuelve rel dentro de workdir. Devuelve None si intenta escapar."""
    if rel.startswith("/") or ":" in rel.split("/")[0]:
        return None
    target = (workdir / rel).resolve()
    try:
        target.relative_to(workdir.resolve())
    except ValueError:
        return None
    return target


def write_files(workdir: Path, allowed, files):
    written, skipped = [], []
    for rel, body in files:
        target = _safe_target(workdir, rel)
        if target is None:
            skipped.append((rel, "ruta insegura"))
            continue
        if allowed and not any(rel.startswith(a) for a in allowed):
            skipped.append((rel, f"fuera de {allowed}"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        written.append(rel)
    return written, skipped


# --- Construccion del contexto ---------------------------------------------

def _read_capped(path: Path) -> str:
    body = path.read_text(encoding="utf-8", errors="replace")
    if len(body) > MAX_CHARS_PER_FILE:
        body = body[:MAX_CHARS_PER_FILE] + "\n… (truncado por presupuesto de contexto)"
    return body


# Extensiones que el agente puede LEER como contexto. Antes solo incluia spec, y
# .py/.ts se descartaban: un agente nunca veia el codigo con el que debia integrarse
# (QA importaba nombres de clase inventados y la suite ni arrancaba). Ahora tambien
# lee fuente, para que las pruebas y los modulos que consumen otros modulos usen los
# simbolos REALES, no adivinados.
CONTEXT_EXT = {".yaml", ".yml", ".md", ".sql", ".json", ".feature", ".mmd",
               ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go",
               ".java", ".kt", ".rb", ".php", ".cs"}


def gather_specs(workdir: Path, node: str, extra_globs) -> str:
    """Artefactos (spec y codigo) relevantes para este nodo y esta tarea, con tope."""
    globs = list(NODE_CONTEXT.get(node, ["spec/**/*"])) + list(extra_globs or [])
    seen, chunks, total = set(), [], 0
    for pattern in globs:
        for f in sorted(workdir.glob(pattern)):
            rel = f.relative_to(workdir).as_posix()
            if {"node_modules", "__pycache__", "dist", "build", ".git"} & set(f.parts):
                continue
            if not f.is_file() or rel in seen or f.suffix not in CONTEXT_EXT:
                continue
            seen.add(rel)
            body = _read_capped(f)
            if total + len(body) > MAX_CHARS_TOTAL:
                chunks.append(f"### (…{len(list(seen))} archivos mas omitidos por presupuesto)")
                return "\n\n".join(chunks)
            total += len(body)
            chunks.append(f"### {rel}\n{body}")
    return "\n\n".join(chunks) if chunks else "(el repo aun no tiene artefactos en spec/)"


def gather_inventory(workdir: Path) -> str:
    """Inventario de src/ y tests/: rutas y tamano, sin contenido.

    Barato y decisivo: un agente que ve que src/domain/parser.py NO existe no
    escribe una prueba que lo importe ni un cliente que lo llame.
    """
    rows = []
    for root in ("src", "tests", "migrations"):
        base = workdir / root
        if not base.exists():
            rows.append(f"  {root}/ — (no existe todavia)")
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or {"node_modules", "__pycache__", "dist", "build"} & set(f.parts):
                continue
            n = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            rows.append(f"  {f.relative_to(workdir).as_posix()} ({n} lineas)")
    return "\n".join(rows) if rows else "  (arbol de codigo vacio)"


def gather_task(workdir: Path):
    """La tarea activa. Devuelve (texto_para_el_prompt, globs_de_contexto_extra)."""
    path = workdir / ".agent/current_task.json"
    if not path.exists():
        return "", []
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "", []
    lines = [
        "TU TAREA ASIGNADA (no implementes nada fuera de ella):",
        f"  task_id:     {t.get('id')}",
        f"  titulo:      {t.get('title')}",
        f"  FR cubiertos: {', '.join(t.get('fr_refs') or []) or '(ninguno)'}",
        f"  entregables: {', '.join(t.get('deliverables') or []) or '(los que exija el criterio)'}",
        f"  aceptacion:  {t.get('acceptance')}",
    ]
    if t.get("kind") == "defect":
        lines.append(f"\n  Esta es una TAREA DE DEFECTO abierta por {t.get('gate_id')}. "
                     f"Corrige exactamente esto y nada mas:")
        for f in (t.get("findings") or [])[:10]:
            lines.append(f"    - {f['file']}:{f['line']} {f['rule']} — {f['evidence']}")
    return "\n".join(lines), list(t.get("context") or []) + list(t.get("deliverables") or [])


def active_task(workdir: Path) -> dict:
    path = workdir / ".agent/current_task.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def gather_defects(workdir: Path, node: str) -> str:
    """Defectos del ciclo anterior para este nodo, para que el modelo los corrija."""
    reports = workdir / ".agent/reports"
    if not reports.exists():
        return ""
    lines = []
    for rf in sorted(reports.glob(f"{node}.*.json")):
        try:
            rep = json.loads(rf.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for f in rep.get("findings", []):
            lines.append(f"- [{rep['gate_id']}] {f['file']}:{f['line']} "
                         f"{f['rule']} — {f['evidence']}")
    if not lines:
        return ""
    return ("\nDEFECTOS DEL CICLO ANTERIOR QUE DEBES CORREGIR (emitidos por gates "
            "deterministas):\n" + "\n".join(lines))


def main():
    if len(sys.argv) != 3:
        print("uso: agent.py <node> <workdir>", file=sys.stderr)
        return 2
    node, workdir = sys.argv[1], Path(sys.argv[2])

    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
    node_cfg = next((n for n in cfg["node"] if n["id"] == node), None)
    if node_cfg is None:
        print(f"nodo desconocido: {node}", file=sys.stderr)
        return 2
    if node_cfg.get("type") == "human":
        return 0  # el gate humano no invoca modelo

    profile = config.agent_profile(node)
    if not profile.get("enabled", True):
        print(f"  [agent] BLOQUEADO: {node} esta desactivado por el operador", file=sys.stderr)
        return 3
    runtime_cfg = config.load()
    task_data = active_task(workdir)
    try:
        selection = model_router.resolve_agent(node, task_data, runtime_cfg)
    except model_router.ModelRoutingError as error:
        print(f"  [agent] error de routing: {error}", file=sys.stderr)
        return 1
    selection_path = workdir / ".agent/model-selections" / f"{node}.json"
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps(selection, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    if task_data.get("id"):
        task_data["model_selection"] = selection
        (workdir / ".agent/current_task.json").write_text(
            json.dumps(task_data, indent=2, ensure_ascii=False), encoding="utf-8")
        lifecycle.model_selected(workdir, str(task_data["id"]), selection)
    os.environ["SDD_TEMPERATURE"] = str(profile.get("temperature", 0.2))
    if int(profile.get("max_tokens") or 0) > 0:
        os.environ["SDD_MAX_TOKENS"] = str(profile["max_tokens"])

    allowed = node_cfg.get("writes", [])
    system_prompt = compose_system_prompt(node_cfg, profile)

    intake_path = workdir / "spec/00_intake.yaml"
    intake = intake_path.read_text(encoding="utf-8") if intake_path.exists() else \
        "(no hay spec/00_intake.yaml; pide que se defina la idea)"
    task_text, extra_globs = gather_task(workdir)

    user = "\n".join(filter(None, [
        f"IDEA (spec/00_intake.yaml, unica fuente de verdad):\n{intake}",
        task_text,
        f"ESPECIFICACION RELEVANTE PARA TU ROL:\n{gather_specs(workdir, node, extra_globs)}",
        f"ARBOL DE CODIGO ACTUAL (lo que existe de verdad hoy):\n{gather_inventory(workdir)}",
        gather_defects(workdir, node),
        PROTOCOL.format(allowed=allowed),
    ]))

    try:
        with model_router.selection_environment(selection, runtime_cfg):
            prov = providers.describe()
            print(f"  [agent] nodo={node} proveedor={prov.get('provider')} "
                  f"modelo={prov.get('model')} tier={selection['tier']} "
                  f"contexto={len(user)} chars", flush=True)
            text = providers.complete(system_prompt, user)
    except providers.ProviderError as e:
        print(f"  [agent] error de proveedor: {e}", file=sys.stderr)
        _archive_call(workdir, node, system_prompt, user, text="",
                      written=[], skipped=[], returncode=1,
                      detail=f"ProviderError: {e}", selection=selection)
        return 1
    except Exception as e:  # noqa: BLE001 — reportar limpio, no traceback crudo
        print(f"  [agent] fallo la llamada al modelo: {type(e).__name__}: {e}",
              file=sys.stderr)
        _archive_call(workdir, node, system_prompt, user, text="",
                      written=[], skipped=[], returncode=1,
                      detail=f"{type(e).__name__}: {e}", selection=selection)
        return 1

    blocked = BLOCKED_BLOCK.search(text)
    if blocked and not FILE_BLOCK.search(text):
        print(f"  [agent] BLOQUEADO: {blocked.group('why')}", file=sys.stderr)
        return 3
    files = parse_files(text)
    if not files:
        print("  [agent] el modelo no emitio bloques de archivo; salida cruda:",
              file=sys.stderr)
        print(text[:500], file=sys.stderr)
        return 1
    written, skipped = write_files(workdir, allowed, files)
    _archive_call(workdir, node, system_prompt, user, text,
                  written=written, skipped=skipped, returncode=0,
                  selection=selection)
    for w in written:
        print(f"  [agent] escrito {w}", flush=True)
    for rel, why in skipped:
        print(f"  [agent] OMITIDO {rel} ({why})", flush=True)
    if not written:
        print("  [agent] ningun archivo cayo dentro de los paths permitidos",
              file=sys.stderr)
        _archive_call(workdir, node, system_prompt, user, text,
                      written=written, skipped=skipped, returncode=1,
                      selection=selection)
        return 1
    return 0


def _archive_call(workdir: Path, node: str, system_prompt: str, user: str,
                  text: str, *, written: list, skipped: list,
                  returncode: int = 0, detail: str = "",
                  selection: dict | None = None) -> None:
    visit_id = os.environ.get("SDD_VISIT_ID")
    if not visit_id:
        return
    task_id = os.environ.get("SDD_METRICS_TASK") or None
    usage = providers.last_usage() if returncode == 0 and text else None
    chronicle.archive_agent_call(
        str(workdir), visit_id, node, task_id,
        system_prompt=system_prompt,
        user_prompt=user,
        response_text=text,
        stdout_text="",
        stderr_text=detail,
        returncode=returncode,
        files_written=written,
        files_skipped=skipped,
        token_usage=usage,
        model_selection=selection,
    )


if __name__ == "__main__":
    sys.exit(main())

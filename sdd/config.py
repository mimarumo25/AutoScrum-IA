#!/usr/bin/env python3
"""Configuracion persistente del pipeline (config.json en la raiz del repo).

Guarda: proveedor, modelo, tema, ruta base de salida, API keys por proveedor y
complementos de prompt por subagente. Ademas resuelve las rutas de proyecto/tarea
y lista las tareas guardadas de un proyecto (para la vista de Tareas).

Estructura de salida por defecto:  <raiz>/project/<proyecto>/<tarea>/

SEGURIDAD: config.json puede contener API keys en claro. Esta en .gitignore.
"""
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent      # el paquete sdd/
ROOT = PKG.parent                          # la raiz del repo
CONFIG_PATH = ROOT / "config.json"

AGENT_NODES = ["product", "architect", "planner", "dev_backend", "dev_frontend", "qa"]

# .gitignore que se siembra en todo repo objetivo. Desde que el gate G9 EJECUTA la
# suite, el arbol se llena de artefactos de ejecucion (__pycache__, coverage,
# node_modules). Nadie los escribio a proposito, asi que G7 no debe imputarselos a
# ningun nodo ni deben acabar en un commit.
GITIGNORE = """.agent/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
node_modules/
dist/
build/
coverage/
.coverage
.venv/
"""

DEFAULTS = {
    "output_base": "project",
    "theme": "auto",
    "provider": "anthropic",
    "model": "",
    "keys": {},           # {proveedor: api_key}
    "agent_addons": {},   # {nodo: texto extra para su system prompt}
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except (ValueError, OSError):
            pass
    if not isinstance(cfg.get("keys"), dict):
        cfg["keys"] = {}
    if not isinstance(cfg.get("agent_addons"), dict):
        cfg["agent_addons"] = {}
    return cfg


def save(patch: dict) -> dict:
    cfg = load()
    for k in ("output_base", "theme", "provider", "model"):
        if patch.get(k) is not None and str(patch.get(k)) != "":
            cfg[k] = patch[k]
    for prov, key in (patch.get("keys") or {}).items():
        if key:
            cfg["keys"][prov] = key
    if isinstance(patch.get("agent_addons"), dict):
        for node, txt in patch["agent_addons"].items():
            cfg["agent_addons"][node] = txt   # cadena vacia SÍ borra (es intencional)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # Las API keys van en claro (cifrarlas exigiria un llavero del SO y una
    # dependencia nueva; queda fuera de v0). Mitigacion: el archivo esta en
    # .gitignore y aqui se restringen los permisos a solo-el-dueno. En Windows
    # os.chmod es limitado, asi que es best-effort, no una garantia.
    if cfg.get("keys"):
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
    return cfg


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", (name or "").strip())
    s = re.sub(r"\.{2,}", ".", s).strip("-.")
    return s or "sin-nombre"


def _base(output_base=None) -> Path:
    base = output_base or load().get("output_base") or "project"
    bp = Path(base)
    return bp if bp.is_absolute() else (ROOT / bp)


def resolve_output(project: str, task: str = None, output_base=None) -> Path:
    """Ruta absoluta del proyecto (y tarea) donde se genera el trabajo."""
    p = _base(output_base) / slug(project)
    if task:
        p = p / slug(task)
    return p.resolve()


NODE_COUNT = len(AGENT_NODES)   # nodos que invocan modelo (sin el gate humano)


def _task_status(state_path: Path) -> dict:
    """Avance de una corrida. Si ya hay plan, se mide en tareas cerradas: es la
    unidad de trabajo real. Antes de que exista el plan, en nodos aprobados."""
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"final": "?", "done": 0, "total": NODE_COUNT, "calls": 0, "unit": "nodos"}
    tasks = st.get("tasks") or []
    if tasks:
        done = sum(1 for t in tasks if t.get("status") == "done")
        return {"final": st.get("status", "?"), "done": done, "total": len(tasks),
                "calls": st.get("agent_calls", 0), "unit": "tareas"}
    committed = {e.get("nodo") for e in st.get("history", [])
                 if e.get("event") == "APROBADO" and e.get("accion") == "commit"}
    committed.discard("human_gate")
    return {"final": st.get("status", "?"), "done": len(committed),
            "total": NODE_COUNT, "calls": st.get("agent_calls", 0), "unit": "nodos"}


def list_projects(output_base=None):
    base = _base(output_base)
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def list_tasks(project: str, output_base=None):
    """Tareas guardadas de un proyecto, con estado y avance."""
    proj = _base(output_base) / slug(project)
    if not proj.exists():
        return []
    out = []
    for t in sorted(proj.iterdir()):
        if not t.is_dir():
            continue
        sp = t / ".agent/state.json"
        info = {"task": t.name, "has_run": sp.exists()}
        info.update(_task_status(sp) if sp.exists()
                    else {"final": "sin correr", "done": 0, "total": NODE_COUNT,
                          "calls": 0, "unit": "nodos"})
        out.append(info)
    return out


def masked() -> dict:
    cfg = load()
    cfg["keys"] = {p: (k[:6] + "…" + k[-4:] if len(k) > 12 else "•••")
                   for p, k in cfg["keys"].items()}
    return cfg

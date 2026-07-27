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
import tomllib
from pathlib import Path

PKG = Path(__file__).resolve().parent      # el paquete sdd/
ROOT = PKG.parent                          # la raiz del repo
CONFIG_PATH = ROOT / "config.json"
PIPELINE_PATH = PKG / "pipeline.toml"

AGENT_NODES = ["product", "architect", "planner", "dev_backend", "dev_frontend", "qa"]
AGENT_ROLES = {
    "product": "Product Strategist", "architect": "Solution Architect",
    "planner": "Delivery Planner", "dev_backend": "Backend Engineer",
    "dev_frontend": "Frontend Engineer", "qa": "Quality Engineer",
}
AGENT_TOOLS = {
    "product": ["spec.read", "spec.write"],
    "architect": ["spec.read", "spec.write", "repository.inspect"],
    "planner": ["spec.read", "tasks.plan"],
    "dev_backend": ["filesystem", "git", "tests", "gates"],
    "dev_frontend": ["filesystem", "git", "tests", "gates"],
    "qa": ["filesystem", "tests", "gates", "traceability"],
}

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
    "agent_profiles": {}, # parametros por agente, persistidos localmente
    "custom_agents": [],  # agentes disponibles para asignacion manual/futura
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
    if not isinstance(cfg.get("agent_profiles"), dict):
        cfg["agent_profiles"] = {}
    if not isinstance(cfg.get("custom_agents"), list):
        cfg["custom_agents"] = []
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
    if isinstance(patch.get("agent_profiles"), dict):
        for node, profile in patch["agent_profiles"].items():
            if not isinstance(profile, dict):
                continue
            clean = dict(profile)
            clean["enabled"] = bool(profile.get("enabled", True))
            try:
                clean["temperature"] = max(0.0, min(2.0, float(profile.get("temperature", 0.2))))
            except (TypeError, ValueError):
                clean["temperature"] = 0.2
            try:
                clean["max_tokens"] = max(0, min(200000, int(profile.get("max_tokens", 0))))
            except (TypeError, ValueError):
                clean["max_tokens"] = 0
            clean["tools"] = [str(t) for t in profile.get("tools", []) if str(t).strip()]
            clean["prompt_addon"] = str(profile.get("prompt_addon", ""))
            node_id = slug(str(node))
            cfg["agent_profiles"][node_id] = clean
            cfg["agent_addons"][node_id] = clean["prompt_addon"]
    if isinstance(patch.get("custom_agents"), list):
        cfg["custom_agents"] = [
            dict(agent) for agent in patch["custom_agents"]
            if isinstance(agent, dict) and slug(str(agent.get("id", ""))) not in AGENT_NODES
        ]
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


def agent_profile(node_id: str) -> dict:
    """Perfil efectivo de un agente, con defaults seguros y retrocompatibles."""
    cfg = load()
    stored = cfg.get("agent_profiles", {}).get(node_id, {})
    profile = {
        "enabled": True, "provider": "", "model": "", "temperature": 0.2,
        "max_tokens": 0, "tools": list(AGENT_TOOLS.get(node_id, ["filesystem"])),
        "prompt_addon": cfg.get("agent_addons", {}).get(node_id, ""),
    }
    if isinstance(stored, dict):
        profile.update(stored)
    return profile


def agent_catalog() -> list[dict]:
    """Catalogo para la UI, combinando nodos del pipeline y perfiles locales."""
    try:
        pipeline = tomllib.loads(PIPELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        pipeline = {"node": []}
    result = []
    for node in pipeline.get("node", []):
        node_id = str(node.get("id", ""))
        if not node_id or node.get("type") == "human":
            continue
        prompt_path = PKG / str(node.get("prompt", ""))
        try:
            prompt_base = prompt_path.read_text(encoding="utf-8")
        except OSError:
            prompt_base = ""
        result.append({
            "id": node_id, "name": AGENT_ROLES.get(node_id, node_id.replace("_", " ").title()),
            "role": AGENT_ROLES.get(node_id, "Custom agent"), "built_in": True,
            "prompt_path": str(node.get("prompt", "")), "prompt_base": prompt_base,
            "writes": node.get("writes", []), "must_produce": node.get("must_produce", []),
            "gates": node.get("gates", []), "task_node": bool(node.get("task_node")),
            "next": node.get("next", ""), **agent_profile(node_id),
        })
    for custom in load().get("custom_agents", []):
        node_id = slug(str(custom.get("id", "")))
        result.append({
            "id": node_id, "name": custom.get("name") or node_id.replace("_", " ").title(),
            "role": custom.get("role") or "Custom agent", "built_in": False,
            "prompt_path": "", "prompt_base": custom.get("prompt_base", ""),
            "writes": custom.get("writes", []), "must_produce": [],
            "gates": custom.get("gates", []), "task_node": True, "next": "task_loop",
            **agent_profile(node_id),
        })
    return result

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

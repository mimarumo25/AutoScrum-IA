#!/usr/bin/env python3
"""Panel web local del pipeline SDD (organizado por pestañas).

Pestañas: Ejecutar · Tareas · Agentes · Configuración. El flujo del pipeline y el
log se muestran siempre a la derecha. Las tareas se guardan por proyecto en
project/<proyecto>/<tarea>/ y se pueden revisar con su estado y avance.

Solo stdlib (http.server). Escucha en 127.0.0.1.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import providers  # noqa: E402
import report  # noqa: E402
import tomllib  # noqa: E402
from webpage import PAGE  # noqa: E402  plantilla HTML del panel (modulo aparte)

ROOT = Path(__file__).resolve().parent
PY = sys.executable

_LOCK = threading.Lock()
RUN = {"status": "idle", "workdir": None, "log": [], "provider": None,
       "project": None, "task": None}

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "SDD_API_KEY"}
KEY_ENV.update({p: c["key_env"] for p, c in providers.OPENAI_PRESETS.items()})


def _git(wd, *args):
    subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True)


def _seed(wd: Path, idea: str):
    wd.mkdir(parents=True, exist_ok=True)
    if not (wd / ".git").exists():
        _git(wd, "init", "-q")
        _git(wd, "config", "user.email", "sdd@local")
        _git(wd, "config", "user.name", "sdd-pipeline")
        (wd / ".gitignore").write_text(config.GITIGNORE, encoding="utf-8")
    intake = wd / "spec/00_intake.yaml"
    intake.parent.mkdir(parents=True, exist_ok=True)
    intake.write_text(idea, encoding="utf-8")
    _git(wd, "add", "-A")
    _git(wd, "commit", "-qm", "chore: intake desde la interfaz web")


def _run_pipeline(wd: Path, env: dict, extra=()):
    logfile = wd / ".agent/run.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    # Al reanudar, el log se ABRE en modo append para no borrar el historial de la
    # corrida anterior; en una corrida nueva se sobreescribe.
    mode = "a" if "--resume" in extra else "w"
    fh = logfile.open(mode, encoding="utf-8")
    proc = subprocess.Popen(
        [PY, str(ROOT / "orchestrator.py"), "--workdir", str(wd), "--autonomous", *extra],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip("\n")
        with _LOCK:
            RUN["log"].append(line)
        fh.write(line + "\n"); fh.flush()
    proc.wait(); fh.close()
    with _LOCK:
        RUN["status"] = "done"


def _env_for(provider: str, model: str, key: str) -> dict:
    env = dict(os.environ)
    env["SDD_PROVIDER"] = provider
    if model:
        env["SDD_MODEL"] = model
    if key:
        env[KEY_ENV.get(provider, "ANTHROPIC_API_KEY")] = key
    return env


def _resume(body):
    """Continua un proyecto interrumpido o escalado sin perder lo ya commiteado.
    Usa el proveedor/modelo/clave guardados en config."""
    project = body.get("project", "").strip()
    task = body.get("task", "").strip() or "tarea-1"
    wd = config.resolve_output(project, task)
    if not (wd / ".agent/state.json").exists():
        return None
    cfg = config.load()
    provider = cfg.get("provider") or "anthropic"
    key = cfg["keys"].get(provider, "")
    env = _env_for(provider, cfg.get("model") or "", key)
    with _LOCK:
        RUN.update(status="running", workdir=str(wd), log=[],
                   provider=provider, project=project, task=task)
    threading.Thread(target=_run_pipeline, args=(wd, env, ("--resume",)), daemon=True).start()
    return wd


def _start(body):
    provider = body.get("provider", "anthropic")
    key = body.get("key", "")
    model = body.get("model", "").strip()
    project = body.get("project", "").strip()
    task = body.get("task", "").strip() or "tarea-1"
    config.save({"provider": provider, "model": model,
                 "keys": {provider: key} if key else {}})
    if not key:
        key = config.load()["keys"].get(provider, "")
    wd = config.resolve_output(project, task)
    env = _env_for(provider, model, key)
    _seed(wd, body.get("idea", ""))
    with _LOCK:
        RUN.update(status="running", workdir=str(wd), log=[],
                   provider=provider, project=project, task=task)
    threading.Thread(target=_run_pipeline, args=(wd, env), daemon=True).start()
    return wd


def _steps_from(state_path: Path):
    st = json.loads(state_path.read_text(encoding="utf-8"))
    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text())
    return (report.build_steps(st["history"]),
            [n["id"] for n in cfg["node"]] + ["done"], st.get("status"))


def _sprint_from(state_path: Path):
    """Tareas del plan para la vista de sprint del panel: id, nodo, estado y
    bloqueos. Antes el panel solo listaba carpetas de proyecto, no el sprint."""
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [{"id": t.get("id"), "node": t.get("node"), "title": t.get("title", ""),
             "status": t.get("status"), "kind": t.get("kind"),
             "blocked_by": t.get("blocked_by")} for t in (st.get("tasks") or [])]


def _state():
    with _LOCK:
        snap = dict(RUN); snap["log"] = list(RUN["log"])
    steps, nodes, final, sprint = [], [], None, []
    if snap["workdir"]:
        sp = Path(snap["workdir"]) / ".agent/state.json"
        if sp.exists():
            steps, nodes, final = _steps_from(sp)
            sprint = _sprint_from(sp)
    return {"status": snap["status"], "final": final, "provider": snap["provider"],
            "project": snap["project"], "task": snap["task"],
            "steps": steps, "nodes": nodes, "sprint": sprint, "log": snap["log"]}


def _task_view(project, task):
    wd = config.resolve_output(project, task)
    sp = wd / ".agent/state.json"
    if not sp.exists():
        return {"status": "idle", "final": "sin correr", "provider": None,
                "project": project, "task": task, "steps": [], "nodes": [], "log": []}
    steps, nodes, final = _steps_from(sp)
    lf = wd / ".agent/run.log"
    log = lf.read_text(encoding="utf-8", errors="replace").splitlines() if lf.exists() else []
    return {"status": "idle", "final": final, "provider": None,
            "project": project, "task": task, "steps": steps, "nodes": nodes, "log": log}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or "{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif u.path == "/idea":
            src = ROOT / "intake.yaml"
            self._send(200, src.read_text(encoding="utf-8") if src.exists() else "",
                       "text/plain; charset=utf-8")
        elif u.path == "/config":
            cfg = config.load()
            cfg["model_choices"] = providers.MODEL_CHOICES
            self._send(200, json.dumps(cfg))
        elif u.path == "/projects":
            self._send(200, json.dumps(config.list_projects()))
        elif u.path == "/tasks":
            self._send(200, json.dumps(config.list_tasks(q.get("project", [""])[0])))
        elif u.path == "/task":
            self._send(200, json.dumps(_task_view(q.get("project", [""])[0],
                                                   q.get("task", [""])[0])))
        elif u.path == "/state":
            self._send(200, json.dumps(_state()))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path == "/config":
            self._send(200, json.dumps(config.save(self._body())))
            return
        if self.path not in ("/run", "/resume"):
            self._send(404, "{}")
            return
        body = self._body()
        with _LOCK:
            busy = RUN["status"] == "running"
        if busy:
            self._send(409, json.dumps({"error": "ya hay una corrida en curso"}))
            return
        try:
            wd = _resume(body) if self.path == "/resume" else _start(body)
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": str(e)}))
            return
        if wd is None:   # solo /resume: no habia corrida previa que continuar
            self._send(409, json.dumps({"error": "este proyecto no tiene una corrida que continuar"}))
            return
        self._send(200, json.dumps({"ok": True, "workdir": str(wd)}))


def serve(host="127.0.0.1", port=8770):
    httpd = ThreadingHTTPServer((host, port), H)
    print(f"panel SDD en http://{host}:{port}  (Ctrl+C para salir)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ncerrando panel")


if __name__ == "__main__":
    serve()

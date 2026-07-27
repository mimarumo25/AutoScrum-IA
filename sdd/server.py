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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import providers  # noqa: E402
import report  # noqa: E402
import process_control  # noqa: E402
import tomllib  # noqa: E402
import lifecycle  # noqa: E402
import chronicle  # noqa: E402
from webpage import PAGE  # noqa: E402  plantilla HTML del panel (modulo aparte)

ROOT = Path(__file__).resolve().parent
PY = sys.executable

_LOCK = threading.Lock()
RUN = {"status": "idle", "workdir": None, "log": [], "provider": None,
       "project": None, "task": None}

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "SDD_API_KEY"}
KEY_ENV.update({p: c["key_env"] for p, c in providers.OPENAI_PRESETS.items()})


def _git(wd, *args):
    process_control.run_git(wd, *args, text=True)


def _claim_run() -> bool:
    """Reserva atomica: dos POST concurrentes no pueden iniciar dos corridas."""
    with _LOCK:
        if RUN["status"] in ("starting", "running"):
            return False
        RUN["status"] = "starting"
        return True


def _release_claim() -> None:
    with _LOCK:
        if RUN["status"] == "starting":
            RUN["status"] = "idle"


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
    try:
        with logfile.open(mode, encoding="utf-8") as fh:
            proc = subprocess.Popen(
                [PY, str(ROOT / "orchestrator.py"), "--workdir", str(wd), *extra],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout or ():
                line = line.rstrip("\n")
                with _LOCK:
                    RUN["log"].append(line)
                fh.write(line + "\n")
                fh.flush()
            proc.wait()
    except Exception as error:  # noqa: BLE001
        with _LOCK:
            RUN["log"].append(f"ERROR: {error}")
    finally:
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
    env["SDD_APPROVAL_ACTOR"] = "web"
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


def _artifact_list(workdir: Path) -> list[dict]:
    """Artefactos recientes, sin exponer archivos internos ni secretos."""
    if not workdir.exists():
        return []
    result = []
    for root in (workdir / "spec", workdir / "src", workdir / "tests"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 512_000:
                continue
            result.append({"path": path.relative_to(workdir).as_posix(),
                           "size": path.stat().st_size, "updated": path.stat().st_mtime})
    return sorted(result, key=lambda item: item["updated"], reverse=True)[:40]


def _runtime_agents(steps, sprint, run_status, task_summaries=None):
    """Proyecta pasos y journals concurrentes en microestados visuales."""
    current = steps[-1] if steps else None
    completed = {step.get("node") for step in steps if step.get("commit")}
    summaries = task_summaries or []
    out = []
    for agent in config.agent_catalog():
        node_id = agent["id"]
        tasks = [task for task in sprint if task.get("node") == node_id]
        node_runs = [item for item in summaries if item.get("node") == node_id]
        live_runs = [item for item in node_runs if item.get("status") not in ("done", "blocked", "escalated")]
        failed_runs = [item for item in node_runs if item.get("status") in ("blocked", "escalated")]
        state = "idle"
        if not agent.get("enabled", True):
            state = "disabled"
        elif live_runs and run_status == "running":
            # Antes de agent_called el modelo está razonando; después se ejecutan gates/tools.
            state = "tool_call" if any(item.get("calls", 0) for item in live_runs) else "thinking"
        elif failed_runs or tasks and any(t.get("status") in ("blocked", "escalated") for t in tasks):
            state = "error"
        elif current and current.get("node") == node_id and run_status == "running":
            state = "error" if any(not gate[1] for gate in current.get("gates", [])) else "thinking"
        elif node_id in completed or node_runs and all(item.get("status") == "done" for item in node_runs) or tasks and all(t.get("status") == "done" for t in tasks):
            state = "completed"
        elif run_status == "running" and (tasks or node_runs):
            state = "queued"
        task_total = max(len(tasks), len(node_runs))
        task_done = max(sum(t.get("status") == "done" for t in tasks),
                        sum(item.get("status") == "done" for item in node_runs))
        active_task = live_runs[-1].get("task_id", "") if live_runs else ""
        if not active_task and current and current.get("node") == node_id:
            active_task = current.get("task", "")
        out.append({"id": node_id, "name": agent.get("name", node_id),
                    "role": agent.get("role", ""), "state": state,
                    "enabled": agent.get("enabled", True), "tools": agent.get("tools", []),
                    "model": agent.get("model", ""), "tasks": task_total,
                    "tasks_done": task_done, "current_task": active_task,
                    "active_runs": len(live_runs)})
    return out


def _view_payload(workdir, status, provider, project, task):
    wd = Path(workdir) if workdir else None
    steps, nodes, final, sprint, engine, raw_state = [], [], None, [], None, {}
    if wd:
        state_path = wd / ".agent/state.json"
        if state_path.exists():
            try:
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                cfg = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
                steps = report.build_steps(raw_state.get("history", []))
                nodes = [n["id"] for n in cfg["node"]] + ["done"]
                final, engine = raw_state.get("status"), raw_state.get("engine")
                sprint = _sprint_from(state_path)
            except (ValueError, OSError, tomllib.TOMLDecodeError):
                pass
    iterations = []
    for index, step in enumerate(steps):
        failed = any(not gate[1] for gate in step.get("gates", []))
        iterations.append({**step, "id": f"iter-{index + 1:02d}", "index": index + 1,
                           "status": "error" if failed else "completed" if step.get("commit") else "active"})
    idea = ""
    if wd and (wd / "spec/00_intake.yaml").exists():
        idea = (wd / "spec/00_intake.yaml").read_text(encoding="utf-8", errors="replace")
    try:
        tokens = report._token_usage(wd) if wd else {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    except (OSError, ValueError):
        tokens = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    task_summaries = []
    if wd:
        try:
            task_summaries = [lifecycle.summary(wd, item["task_id"])
                              for item in lifecycle.all_tasks(wd)]
        except (OSError, ValueError, KeyError):
            task_summaries = []
    return {"status": status, "final": final, "provider": provider,
            "project": project, "task": task, "engine": engine,
            "steps": steps, "iterations": iterations, "nodes": nodes,
            "sprint": sprint, "agents": _runtime_agents(steps, sprint, status, task_summaries),
            "live_tasks": task_summaries, "tokens": tokens, "input": idea,
            "artifacts": _artifact_list(wd) if wd else [], "raw": {
                "run_id": raw_state.get("run_id"), "agent_calls": raw_state.get("agent_calls", 0),
                "attempts": raw_state.get("attempts", {}), "started_at": raw_state.get("started_at")}}


def _state():
    with _LOCK:
        snap = dict(RUN)
        snap["log"] = list(RUN["log"])
    payload = _view_payload(snap["workdir"], snap["status"], snap["provider"],
                            snap["project"], snap["task"])
    payload["log"] = snap["log"]
    return payload


def _task_view(project, task):
    wd = config.resolve_output(project, task)
    state_path = wd / ".agent/state.json"
    if not state_path.exists():
        payload = _view_payload(wd, "idle", None, project, task)
        payload["final"] = "sin correr"
        payload["log"] = []
        return payload
    payload = _view_payload(wd, "idle", None, project, task)
    log_path = wd / ".agent/run.log"
    payload["log"] = (log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                      if log_path.exists() else [])
    return payload

class ControlTowerServer(ThreadingHTTPServer):
    """Silencia desconexiones normales de navegadores con SSE/keep-alive."""

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError,
                              ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


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

    def _stream_states(self):
        """SSE de snapshots deduplicados; el cliente reconecta cada minuto."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        previous = None
        try:
            for seq in range(120):
                payload = json.dumps(_state(), ensure_ascii=False, separators=(",", ":"))
                if payload != previous:
                    message = f"id: {seq}\nevent: state\ndata: {payload}\n\n".encode("utf-8")
                    self.wfile.write(message)
                    self.wfile.flush()
                    previous = payload
                elif seq % 20 == 0:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            return

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
            cfg["agents"] = config.agent_catalog()
            self._send(200, json.dumps(cfg))
        elif u.path == "/projects":
            self._send(200, json.dumps(config.list_projects()))
        elif u.path == "/tasks":
            self._send(200, json.dumps(config.list_tasks(q.get("project", [""])[0])))
        elif u.path == "/task":
            self._send(200, json.dumps(_task_view(q.get("project", [""])[0],
                                                   q.get("task", [""])[0])))
        elif u.path == "/lifecycle":
            tid = q.get("task_id", [""])[0]
            project = q.get("project", [""])[0]
            task_name = q.get("task", [""])[0]
            with _LOCK:
                live_workdir = RUN.get("workdir")
            wd = config.resolve_output(project, task_name) if project else (
                Path(live_workdir) if live_workdir else None)
            if wd is None:
                self._send(200, json.dumps({"tasks": []}))
            elif not tid:
                self._send(200, json.dumps({"tasks": lifecycle.all_tasks(str(wd))}))
            else:
                self._send(200, json.dumps({
                    "summary": lifecycle.summary(str(wd), tid),
                    "events": lifecycle.read(str(wd), tid),
                    "tokens": lifecycle.total_token_usage_by_task(str(wd), tid),
                }))
        elif u.path == "/chronicle":
            project = q.get("project", [""])[0]
            task_name = q.get("task", [""])[0]
            with _LOCK:
                live_workdir = RUN.get("workdir")
            wd = config.resolve_output(project, task_name) if project else (
                Path(live_workdir) if live_workdir else None)
            visit_id = q.get("visit_id", [""])[0]
            try:
                recent = max(1, min(100, int(q.get("recent", ["20"])[0])))
            except ValueError:
                recent = 20
            if wd is None:
                self._send(200, json.dumps({"visits": []}))
            elif visit_id:
                self._send(200, json.dumps(chronicle.read_visit(str(wd), visit_id)))
            else:
                self._send(200, json.dumps({"visits": chronicle.all_visits(str(wd))[:recent]}))
        elif u.path == "/artifact":
            project = q.get("project", [""])[0]
            task_name = q.get("task", [""])[0]
            rel = q.get("path", [""])[0]
            wd = config.resolve_output(project, task_name)
            target = (wd / rel).resolve()
            try:
                target.relative_to(wd)
            except ValueError:
                self._send(403, "fuera del proyecto", "text/plain; charset=utf-8")
                return
            if not target.is_file() or target.stat().st_size > 512_000:
                self._send(404, "no disponible", "text/plain; charset=utf-8")
                return
            self._send(200, target.read_text(encoding="utf-8", errors="replace"),
                       "text/plain; charset=utf-8")
        elif u.path == "/events":
            self._stream_states()
        elif u.path == "/state":
            self._send(200, json.dumps(_state()))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path == "/config":
            with _LOCK:
                saved = config.save(self._body())
            self._send(200, json.dumps(saved))
            return
        if self.path not in ("/run", "/resume"):
            self._send(404, "{}")
            return
        body = self._body()
        if not _claim_run():
            self._send(409, json.dumps({"error": "ya hay una corrida en curso"}))
            return
        try:
            wd = _resume(body) if self.path == "/resume" else _start(body)
        except Exception as e:  # noqa: BLE001
            _release_claim()
            self._send(500, json.dumps({"error": str(e)}))
            return
        if wd is None:   # solo /resume: no habia corrida previa que continuar
            _release_claim()
            self._send(409, json.dumps({"error": "este proyecto no tiene una corrida que continuar"}))
            return
        self._send(200, json.dumps({"ok": True, "workdir": str(wd)}))


def serve(host="127.0.0.1", port=8770):
    httpd = ControlTowerServer((host, port), H)
    print(f"panel SDD en http://{host}:{port}  (Ctrl+C para salir)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ncerrando panel")


if __name__ == "__main__":
    serve(port=int(os.environ.get("SDD_PORT", "8770")))

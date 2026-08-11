"""HTTP y SSE del Control Tower."""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sdd.core import chronicle, config, lifecycle
from sdd.integrations import model_router, providers
from sdd.control_tower import runtime, state, views
from sdd.presentation.webpage import PAGE


class ControlTowerServer(ThreadingHTTPServer):
    """Silencia desconexiones normales de navegadores con SSE/keep-alive."""

    def handle_error(self, request, client_address):
        if isinstance(
            sys.exc_info()[1],
            (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
                TimeoutError,
            ),
        ):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}"
        )

    def _stream_states(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        previous = None
        heartbeat = 0
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while True:
                snapshot = views.current_state()
                payload = json.dumps(
                    snapshot, ensure_ascii=False, separators=(",", ":")
                )
                if payload != previous:
                    self.wfile.write(
                        f"id: {snapshot.get('revision', 0)}\nevent: state\ndata: {payload}\n\n".encode()
                    )
                    self.wfile.flush()
                    previous = payload
                elif heartbeat % 20 == 0:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                heartbeat += 1
                time.sleep(0.5)
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            TimeoutError,
        ):
            return

    def do_GET(self):
        request, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
        path = request.path
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        elif path == "/idea":
            source = runtime.ROOT / "intake.yaml"
            self._send(
                200,
                source.read_text(encoding="utf-8") if source.exists() else "",
                "text/plain; charset=utf-8",
            )
        elif path == "/config":
            payload = config.masked()
            payload["model_choices"] = providers.MODEL_CHOICES
            payload["agents"] = config.agent_catalog()
            self._send(200, json.dumps(payload))
        elif path == "/routing/preview":
            self._send(200, json.dumps(model_router.preview(), ensure_ascii=False))
        elif path == "/agent-bundle":
            self._send(
                200,
                json.dumps(config.agent_bundle(), ensure_ascii=False),
                "application/json; charset=utf-8",
            )
        elif path == "/projects":
            self._send(200, json.dumps(config.list_projects()))
        elif path == "/tasks":
            self._send(
                200, json.dumps(config.list_tasks(query.get("project", [""])[0]))
            )
        elif path == "/task":
            self._send(
                200,
                json.dumps(
                    views.task_view(
                        query.get("project", [""])[0], query.get("task", [""])[0]
                    )
                ),
            )
        elif path in {"/lifecycle", "/chronicle"}:
            self._send_history(path, query)
        elif path == "/artifact":
            self._send_artifact(query)
        elif path == "/events":
            self._stream_states()
        elif path == "/state":
            self._send(200, json.dumps(views.current_state()))
        else:
            self._send(404, "{}")

    def _workdir(self, query):
        project, task = query.get("project", [""])[0], query.get("task", [""])[0]
        with state.LOCK:
            live_workdir = state.RUN.get("workdir")
        return (
            config.resolve_output(project, task)
            if project
            else Path(live_workdir)
            if live_workdir
            else None
        )

    def _send_history(self, path, query):
        workdir = self._workdir(query)
        if workdir is None:
            self._send(
                200, json.dumps({"tasks" if path == "/lifecycle" else "visits": []})
            )
            return
        if path == "/lifecycle":
            task_id = query.get("task_id", [""])[0]
            payload = (
                {"tasks": lifecycle.all_tasks(str(workdir))}
                if not task_id
                else {
                    "summary": lifecycle.summary(str(workdir), task_id),
                    "events": lifecycle.read(str(workdir), task_id),
                    "tokens": lifecycle.total_token_usage_by_task(str(workdir), task_id),
                }
            )
        else:
            visit_id = query.get("visit_id", [""])[0]
            try:
                recent = max(1, min(100, int(query.get("recent", ["20"])[0])))
            except ValueError:
                recent = 20
            payload = (
                chronicle.read_visit(str(workdir), visit_id)
                if visit_id
                else {"visits": chronicle.all_visits(str(workdir))[:recent]}
            )
        self._send(200, json.dumps(payload))

    def _send_artifact(self, query):
        workdir = config.resolve_output(
            query.get("project", [""])[0], query.get("task", [""])[0]
        )
        target = (workdir / query.get("path", [""])[0]).resolve()
        try:
            target.relative_to(workdir)
        except ValueError:
            self._send(403, "fuera del proyecto", "text/plain; charset=utf-8")
            return
        if not target.is_file() or target.stat().st_size > 512_000:
            self._send(404, "no disponible", "text/plain; charset=utf-8")
            return
        self._send(
            200,
            target.read_text(encoding="utf-8", errors="replace"),
            "text/plain; charset=utf-8",
        )

    def do_POST(self):
        if self.path == "/models/discover":
            provider = str(self._body().get("provider") or "").strip().lower()
            try:
                entries = model_router.discover(provider)
                model_router.persist_discovery(provider, entries)
                self._send(
                    200,
                    json.dumps(
                        {
                            "provider": provider,
                            "models": entries,
                            "preview": model_router.preview(),
                        },
                        ensure_ascii=False,
                    ),
                )
            except model_router.ModelRoutingError as error:
                self._send(400, json.dumps({"error": str(error)}))
            return
        if self.path == "/config":
            with state.LOCK:
                config.save(self._body())
            self._send(200, json.dumps(config.masked()))
            return
        if self.path not in {"/run", "/resume"}:
            self._send(404, "{}")
            return
        body = self._body()
        if not runtime.claim_run():
            self._send(409, json.dumps({"error": "ya hay una corrida en curso"}))
            return
        try:
            workdir = (
                runtime.resume(body) if self.path == "/resume" else runtime.start(body)
            )
        except ValueError as error:
            runtime.release_claim()
            self._send(400, json.dumps({"error": str(error)}))
            return
        except Exception as error:  # noqa: BLE001
            runtime.release_claim()
            self._send(500, json.dumps({"error": str(error)}))
            return
        if workdir is None:
            runtime.release_claim()
            self._send(
                409,
                json.dumps(
                    {"error": "este proyecto no tiene una corrida que continuar"}
                ),
            )
            return
        self._send(200, json.dumps({"ok": True, "workdir": str(workdir)}))


def serve(host="127.0.0.1", port=8770):
    httpd = ControlTowerServer((host, port), Handler)
    print(f"panel SDD en http://{host}:{port}  (Ctrl+C para salir)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ncerrando panel")

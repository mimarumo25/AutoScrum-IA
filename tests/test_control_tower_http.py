import json
import threading
from pathlib import Path
from urllib.request import urlopen

from sdd.control_tower.http import ControlTowerServer, Handler
from sdd.core import lifecycle


def test_lifecycle_detail_endpoint_uses_packaged_lifecycle_module(monkeypatch):
    monkeypatch.setattr(Handler, "_workdir", lambda self, query: Path("workdir"))
    monkeypatch.setattr(
        lifecycle,
        "summary",
        lambda workdir, task_id: {"task_id": task_id, "status": "no-journal"},
    )
    monkeypatch.setattr(lifecycle, "read", lambda workdir, task_id: [])
    monkeypatch.setattr(
        lifecycle,
        "total_token_usage_by_task",
        lambda workdir, task_id: {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    )
    server = ControlTowerServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        with urlopen(
            f"http://127.0.0.1:{port}/lifecycle?task_id=T-001", timeout=2
        ) as response:
            payload = json.loads(response.read())
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload == {
        "summary": {"task_id": "T-001", "status": "no-journal"},
        "events": [],
        "tokens": {"input_tokens": 0, "output_tokens": 0, "calls": 0},
    }

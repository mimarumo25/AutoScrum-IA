#!/usr/bin/env python3
"""Punto de entrada compatible del Control Tower.

Las implementaciones viven en ``control_tower/`` por responsabilidad:
``state`` (estado y eventos), ``runtime`` (pipeline), ``views`` (payloads) y
``http`` (rutas HTTP/SSE).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdd.control_tower import runtime, state, views
from sdd.control_tower.http import PAGE, ControlTowerServer, serve
from sdd.control_tower.http import Handler as H
from sdd.core import config

__all__ = ["PAGE", "ControlTowerServer", "H", "config", "serve"]

# Compatibilidad para consumidores y pruebas existentes del módulo server.
ROOT = runtime.ROOT
PY = runtime.PY
KEY_ENV = runtime.KEY_ENV
RUN = state.RUN
_LOCK = state.LOCK
TERMINAL_STATUSES = state.TERMINAL_STATUSES
ACTIVE_PHASES = state.ACTIVE_PHASES
_activity = state.activity
_event_fields = state.event_fields
_humanize_failure = state.humanize_failure
_observe_pipeline_line = state.observe_pipeline_line
_claim_run = runtime.claim_run
_release_claim = runtime.release_claim
_seed = runtime.seed
_git = runtime._git
_run_pipeline = runtime.run_pipeline
_env_for = runtime.env_for
_resume = runtime.resume
_start = runtime.start
_sprint_from = views.sprint_from
_artifact_list = views.artifact_list
_runtime_agents = views.runtime_agents
_failure_from_history = views.failure_from_history
_view_payload = views.view_payload
_state = views.current_state
_repair_mojibake = views.repair_mojibake
_task_view = views.task_view


if __name__ == "__main__":
    serve(port=int(os.environ.get("SDD_PORT", "8770")))

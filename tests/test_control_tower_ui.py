"""Contratos del Control Tower y su proyección multiagente."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "sdd"
if str(SDD) not in sys.path:
    sys.path.insert(0, str(SDD))

import config  # noqa: E402
import server  # noqa: E402
from webpage import PAGE  # noqa: E402


class ControlTowerUiTests(unittest.TestCase):
    def test_shell_contains_primary_information_architecture(self):
        for contract in (
            'data-view="workspace"', 'data-view="history"',
            'data-view="results"', 'data-view="settings"',
            'id="iteration-list"', 'id="canvas-topology"',
            'id="drawer"', 'id="agent-list"', 'id="agent-editor"',
            'id="drawer-logs"', 'id="drawer-trace"',
            'aria-label="Inspector de agente"', 'prefers-reduced-motion:reduce',
        ):
            self.assertIn(contract, PAGE)

    def test_concurrent_journals_project_multiple_active_agents(self):
        catalog = [
            {"id": "architect", "name": "Architect", "role": "Architecture", "enabled": True, "tools": []},
            {"id": "dev_backend", "name": "Backend", "role": "Engineering", "enabled": True, "tools": []},
            {"id": "qa", "name": "QA", "role": "Quality", "enabled": True, "tools": []},
        ]
        journals = [
            {"task_id": "T-01", "node": "architect", "status": "unknown", "started": "now", "calls": 0},
            {"task_id": "T-02", "node": "dev_backend", "status": "unknown", "started": "now", "calls": 1},
            {"task_id": "T-03", "node": "qa", "status": "blocked", "started": "now", "calls": 1},
        ]
        with mock.patch.object(server.config, "agent_catalog", return_value=catalog):
            agents = server._runtime_agents([], [], "running", journals)
        states = {agent["id"]: agent["state"] for agent in agents}
        self.assertEqual(states, {"architect": "thinking", "dev_backend": "tool_call", "qa": "error"})
        self.assertEqual(sum(a["state"] in {"thinking", "tool_call", "streaming"} for a in agents), 2)

    def test_agent_profiles_are_validated_and_persisted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp-tests") as folder:
            target = Path(folder) / "config.json"
            with mock.patch.object(config, "CONFIG_PATH", target):
                saved = config.save({
                    "agent_profiles": {"architect": {
                        "enabled": False, "temperature": 9, "max_tokens": -2,
                        "tools": ["repository.inspect", ""], "prompt_addon": "Prioriza riesgos.",
                    }, "security_reviewer": {
                        "enabled": True, "temperature": 0.1, "max_tokens": 4096,
                        "tools": ["repository.inspect"], "prompt_addon": "Revisa amenazas.",
                    }},
                    "custom_agents": [{"id": "security_reviewer", "name": "Security Reviewer",
                                       "role": "Threat modeling", "prompt_base": "Audita el diseño."}],
                })
                loaded = config.load()
                catalog = config.agent_catalog()
        profile = saved["agent_profiles"]["architect"]
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["temperature"], 2.0)
        self.assertEqual(profile["max_tokens"], 0)
        self.assertEqual(profile["tools"], ["repository.inspect"])
        self.assertEqual(loaded["agent_profiles"]["architect"]["prompt_addon"], "Prioriza riesgos.")
        custom = next(agent for agent in catalog if agent["id"] == "security_reviewer")
        self.assertEqual(custom["name"], "Security Reviewer")
        self.assertEqual(custom["max_tokens"], 4096)


if __name__ == "__main__":
    unittest.main()
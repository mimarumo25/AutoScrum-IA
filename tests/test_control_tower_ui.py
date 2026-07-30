"""Contratos del Control Tower y su proyección multiagente."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "sdd"
if str(SDD) not in sys.path:
    sys.path.insert(0, str(SDD))

from sdd import server
from sdd.core import config
from sdd.presentation.webpage import PAGE


class ControlTowerUiTests(unittest.TestCase):
    def test_shell_contains_primary_information_architecture(self):
        for contract in (
            'data-view="workspace"', 'data-view="history"',
            'data-view="results"', 'data-view="settings"',
            'id="iteration-list"', 'id="canvas-topology"',
            'id="drawer"', 'id="agent-list"', 'id="agent-editor"',
            'id="drawer-logs"', 'id="drawer-trace"', 'idea-editor', 'editor-toolbar',
            'run-modal-body', 'editorToMarkdown', 'data-editor-command',
            'id="import-agents"', 'id="export-agents"', 'agent-system-prompt',
            'id="routing-preview"', 'id="save-routing"', 'id="discover-models"',
            'Automático por política', '/routing/preview', '/models/discover',
            'aria-label="Inspector de agente"', 'prefers-reduced-motion:reduce',
            'iterationArtifacts', 'activeOverlay', 'prepareTabs', 'sidebarBackdrop',
            'overlay-open', 'id="active-agents"', 'id="failure-alert"',
            'id="resume-failure"', 'id="replay-alert"', 'renderActiveAgents',
            'renderFailure', 'playFailureTone', 'ACTIVE_STATES',
            'setInterval(fetchState,1200)', 'cache:"no-store"',
        ):
            self.assertIn(contract, PAGE)

    def test_native_prompts_cover_v031_contracts(self):
        contracts = {
            "product.md": ["spec/10_product/prd.md", "MoSCoW", "Usabilidad",
                           "Seguridad", "Rendimiento", "Escalabilidad", "G1"],
            "architect.md": ["spec/20_arch/ARCHITECTURE.md", "OpenAPI 3.1",
                             "Given-When-Then", "Mermaid", "G2"],
            "planner.md": ["spec/30_plan/tasks.yaml", "path_ownership",
                           "dev_backend", "dev_frontend", "G10"],
            "dev_backend.md": ["500", ".env.example", "G5", "G6", "G7"],
            "dev_frontend.md": ["500", "OpenAPI", "reduced motion", "G7"],
            "qa.md": ["Gherkin", "tests/", "G8", "G9", "D-###"],
        }
        for filename, expected in contracts.items():
            prompt = (SDD / "agents" / filename).read_text(encoding="utf-8")
            for token in expected:
                self.assertIn(token, prompt, f"{filename} debe declarar {token}")

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
        self.assertEqual(states, {"architect": "thinking", "dev_backend": "tool_call", "qa": "waiting"})
        self.assertEqual(sum(a["state"] in {"thinking", "tool_call", "streaming"} for a in agents), 2)

    def test_actividad_en_vivo_tiene_prioridad_sobre_el_ultimo_gate(self):
        catalog = [
            {"id": "product", "name": "Product", "role": "Product", "enabled": True, "tools": []},
            {"id": "architect", "name": "Architect", "role": "Architecture", "enabled": True, "tools": []},
        ]
        steps = [
            {"node": "product", "commit": 1, "gates": [], "task": "-"},
            {"node": "architect", "commit": 0, "gates": [("G2", False)], "task": "-"},
        ]
        activity = {"phase": "retrying", "node": "architect", "task": "-",
                    "message": "Reintento 2 de architect", "attempt": 2}
        with mock.patch.object(server.config, "agent_catalog", return_value=catalog):
            agents = server._runtime_agents(
                steps, [], "running", [], activity, None, "architect")
        states = {agent["id"]: agent["state"] for agent in agents}
        self.assertEqual(states, {"product": "completed", "architect": "retrying"})
        architect = next(agent for agent in agents if agent["id"] == "architect")
        self.assertEqual(architect["activity_message"], "Reintento 2 de architect")
        self.assertEqual(architect["attempt"], 2)

    def test_handoff_muestra_quien_espera_y_quien_corrige(self):
        catalog = [
            {"id": "product", "name": "Product", "role": "Product",
             "enabled": True, "tools": []},
            {"id": "architect", "name": "Architect", "role": "Architecture",
             "enabled": True, "tools": []},
        ]
        with mock.patch.object(server.config, "agent_catalog", return_value=catalog):
            agents = server._runtime_agents(
                [], [], "running", [],
                {"phase": "retrying", "node": "product", "task": "",
                 "message": "product está corrigiendo un hallazgo"},
                None, "product", [{
                    "id": "R-001", "status": "assigned",
                    "failed_node": "architect", "owner": "product", "gate_id": "G2",
                }])
        by_id = {agent["id"]: agent for agent in agents}
        self.assertEqual(by_id["product"]["state"], "retrying")
        self.assertEqual(by_id["architect"]["state"], "waiting")
        self.assertIn("Esperando corrección de product",
                      by_id["architect"]["activity_message"])

    def test_stdout_del_pipeline_actualiza_microestados_y_causa(self):
        original = copy.deepcopy(server.RUN)
        self.addCleanup(lambda: server.RUN.clear() or server.RUN.update(original))
        server.RUN.update(status="running", activity={}, failure=None)
        server._observe_pipeline_line(">> nodo architect")
        self.assertEqual(server.RUN["activity"]["node"], "architect")
        self.assertEqual(server.RUN["activity"]["phase"], "thinking")
        server._observe_pipeline_line(
            "  [DEFECTO           ] gate=G2 owner=architect "
            "ubicacion=spec/20_arch/nfr.yaml:0 regla=nfr-no-medible "
            "evidencia=USAB-001 sin campo metrica")
        server._observe_pipeline_line(
            "  [ENRUTADO          ] a=architect intento=2 reanuda_en=architect")
        self.assertEqual(server.RUN["activity"]["phase"], "retrying")
        self.assertEqual(server.RUN["failure"]["gate"], "G2")
        self.assertIn("USAB-001", server.RUN["failure"]["reason"])
        server._observe_pipeline_line(
            "  [ESCALATE_HUMAN    ] motivo=architect:G2 fallo 3 veces")
        self.assertEqual(server.RUN["status"], "escalated")
        self.assertTrue(server.RUN["failure"]["can_resume"])

    def test_reanudacion_informa_que_el_agente_recibio_la_correccion(self):
        original = copy.deepcopy(server.RUN)
        self.addCleanup(lambda: server.RUN.clear() or server.RUN.update(original))
        server.RUN.update(
            status="escalated", activity={},
            failure={"reason": "fallo anterior", "can_resume": True},
        )

        server._observe_pipeline_line(
            "  [RECUPERACION_RESTAURADA] id=R-001 para=architect "
            "gate=G2 hallazgos=5")

        self.assertEqual(server.RUN["status"], "running")
        self.assertIsNone(server.RUN["failure"])
        self.assertEqual(server.RUN["activity"]["node"], "architect")
        self.assertEqual(server.RUN["activity"]["phase"], "retrying")
        self.assertIn("5 hallazgos", server.RUN["activity"]["message"])

    def test_estado_terminal_persistido_expone_fallo_accionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".agent").mkdir()
            state = {
                "run_id": "run-1", "status": "escalated", "cursor": "architect",
                "attempts": {"architect:G2": 3}, "agent_calls": 4, "tasks": [],
                "history": [
                    {"t": "2026-01-01T00:00:00Z", "event": "DEFECTO",
                     "gate": "G2", "owner": "architect",
                     "ubicacion": "spec/20_arch/nfr.yaml:0",
                     "regla": "nfr-no-medible", "evidencia": "Falta metrica"},
                    {"t": "2026-01-01T00:00:01Z", "event": "ESCALATE_HUMAN",
                     "motivo": "architect:G2 fallo 3 veces"},
                ],
            }
            (wd / ".agent/state.json").write_text(json.dumps(state), encoding="utf-8")
            payload = server._view_payload(wd, "done", "deepseek", "demo", "run")
        self.assertEqual(payload["status"], "escalated")
        self.assertEqual(payload["failure"]["node"], "architect")
        self.assertEqual(payload["failure"]["gate"], "G2")
        self.assertEqual(payload["failure"]["attempt"], 3)
        self.assertIn("Falta metrica", payload["failure"]["findings"])
        self.assertTrue(payload["failure"]["can_resume"])

    def test_fallo_nfr_se_explica_en_lenguaje_claro(self):
        state = {
            "run_id": "run-friendly", "status": "escalated", "cursor": "architect",
            "attempts": {"architect:G2": 3},
            "history": [
                *[{"event": "DEFECTO", "gate": "G2", "owner": "architect",
                   "regla": "nfr-no-medible", "ubicacion": "spec/20_arch/nfr.yaml:0",
                   "evidencia": finding} for finding in [
                       "NFR-USAB-01 sin campo metrica",
                       "NFR-USAB-02 sin campo metrica",
                       "NFR-SEC-01 sin campo metrica",
                       "NFR-SEC-02 sin campo metrica",
                       "NFR-SEC-03 sin campo metrica",
                   ]],
                {"t": "2026-01-01T00:00:01Z", "event": "ESCALATE_HUMAN",
                 "motivo": "architect:G2 fallo 3 veces"},
            ],
        }
        failure = server._failure_from_history(state, "escalated")
        self.assertEqual(failure["user_title"],
                         "Falta definir cómo comprobar 5 requisitos")
        self.assertIn("equipo de calidad no puede aprobar", failure["user_impact"])
        self.assertTrue(any("Facilidad de uso: 2 requisitos" in item
                            for item in failure["user_findings"]))
        self.assertTrue(any("Seguridad: 3 requisitos" in item
                            for item in failure["user_findings"]))
        self.assertIn("NFR-USAB-01 sin campo metrica",
                      failure["technical"]["findings"])

    def test_agent_profiles_are_validated_and_persisted(self):
        target = ROOT / ".test-control-tower-config.json"
        target.unlink(missing_ok=True)
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(config, "CONFIG_PATH", target):
            saved = config.save({
                    "agent_profiles": {"architect": {
                        "enabled": False, "temperature": 9, "max_tokens": -2,
                        "tools": ["repository.inspect", ""],
                        "system_prompt": "Arquitectura portable.",
                        "prompt_addon": "Prioriza riesgos.",
                    }, "security_reviewer": {
                        "enabled": True, "temperature": 0.1, "max_tokens": 4096,
                        "tools": ["repository.inspect"], "prompt_addon": "Revisa amenazas.",
                    }},
                    "custom_agents": [{"id": "security_reviewer", "name": "Security Reviewer",
                                       "role": "Threat modeling", "prompt_base": "Audita el diseño."}],
            })
            loaded = config.load()
            catalog = config.agent_catalog()
            bundle = config.agent_bundle()
        profile = saved["agent_profiles"]["architect"]
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["temperature"], 2.0)
        self.assertEqual(profile["max_tokens"], 0)
        self.assertEqual(profile["tools"], ["repository.inspect"])
        self.assertEqual(profile["system_prompt"], "Arquitectura portable.")
        self.assertEqual(loaded["agent_profiles"]["architect"]["prompt_addon"], "Prioriza riesgos.")
        custom = next(agent for agent in catalog if agent["id"] == "security_reviewer")
        self.assertEqual(custom["name"], "Security Reviewer")
        self.assertEqual(custom["max_tokens"], 4096)
        self.assertEqual(bundle["schema_version"], "autoscrum.agent-bundle/v1")
        self.assertEqual(bundle["version"], "0.3.1")
        self.assertEqual(bundle["agents"]["architect"]["system_prompt"],
                         "Arquitectura portable.")
        self.assertNotIn("keys", bundle)

    def test_runtime_log_repara_mojibake_sin_alterar_utf8_valido(self):
        self.assertEqual(server._repair_mojibake("administraciÃ³n y guÃ­as"),
                         "administración y guías")
        self.assertEqual(server._repair_mojibake("administración y guías"),
                         "administración y guías")


if __name__ == "__main__":
    unittest.main()

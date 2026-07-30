"""Contratos del enrutamiento adaptativo por rol."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd.integrations import model_router
from sdd.runtime import orchestrator


def cfg(*, keys=None, catalog=None, provider="anthropic", model=""):
    return {
        "provider": provider,
        "model": model,
        "keys": keys or {},
        "agent_profiles": {},
        "routing": {
            "mode": "adaptive",
            "role_tiers": dict(model_router.DEFAULT_ROLE_TIERS),
            "provider_priority": list(model_router.DEFAULT_PROVIDER_PRIORITY),
            "model_catalog": catalog or {},
            "reviewer": {"provider": "", "model": "",
                         "prefer_different_provider": True},
            "max_frontier_escalations_per_task": 1,
        },
    }


class RouterTest(unittest.TestCase):
    def test_tiers_por_rol_y_escalado(self):
        value = cfg(
            keys={"anthropic": "a"},
            catalog={"anthropic": [
                {"id": "cheap", "tier": "economy", "enabled": True},
                {"id": "middle", "tier": "balanced", "enabled": True},
                {"id": "strong", "tier": "frontier", "enabled": True},
            ]},
        )
        self.assertEqual(model_router.resolve_agent("product", cfg=value)["model"], "strong")
        self.assertEqual(model_router.resolve_agent("dev_backend", cfg=value)["model"], "cheap")
        self.assertEqual(model_router.resolve_agent("qa", cfg=value)["model"], "middle")
        escalated = model_router.resolve_agent(
            "dev_frontend", {"model_escalated": True}, value)
        self.assertEqual(escalated["model"], "strong")
        self.assertTrue(escalated["escalated"])

    def test_override_explicito_gana(self):
        value = cfg(keys={"glm": "g"})
        value["agent_profiles"]["planner"] = {
            "provider": "glm", "model": "glm-custom-pro",
        }
        selected = model_router.resolve_agent("planner", cfg=value)
        self.assertEqual((selected["provider"], selected["model"]),
                         ("glm", "glm-custom-pro"))
        self.assertIn("override", selected["selection_reason"])

    def test_fallback_al_mejor_configurado(self):
        value = cfg(keys={"deepseek": "d"}, provider="deepseek",
                    model="deepseek-flash")
        selected = model_router.resolve_agent("architect", cfg=value)
        self.assertEqual(selected["tier"], "economy")
        self.assertIn("frontier", selected["fallback_reason"])

    def test_revisor_prefiere_otro_proveedor(self):
        value = cfg(
            keys={"anthropic": "a", "openai": "o"},
            catalog={
                "anthropic": [{"id": "a-pro", "tier": "frontier", "enabled": True}],
                "openai": [{"id": "o-pro", "tier": "frontier", "enabled": True}],
            },
        )
        selected = model_router.resolve_review("anthropic", value)
        self.assertEqual(selected["provider"], "openai")
        self.assertIn("diversidad", selected["selection_reason"])

    def test_sin_credenciales_falla_y_preview_no_filtra_secretos(self):
        value = cfg(catalog={
            "anthropic": [{"id": "a-pro", "tier": "frontier", "enabled": True}],
        })
        with self.assertRaises(model_router.ModelRoutingError):
            model_router.resolve_agent("product", cfg=value)
        projected = model_router.preview(value)
        self.assertNotIn("keys", projected)
        self.assertEqual(projected["candidates"][0]["disabled_reason"],
                         "sin credencial")

    def test_entorno_se_restaura(self):
        value = cfg(keys={"deepseek": "secret"}, provider="deepseek",
                    model="deepseek-flash")
        selected = model_router.resolve_agent("dev_backend", cfg=value)
        before = os.environ.get("SDD_PROVIDER")
        with model_router.selection_environment(selected, value):
            self.assertEqual(os.environ["SDD_PROVIDER"], "deepseek")
            self.assertEqual(os.environ["SDD_MODEL_TIER"], "economy")
        self.assertEqual(os.environ.get("SDD_PROVIDER"), before)
        self.assertNotEqual(os.environ.get("DEEPSEEK_API_KEY"), "secret")


class EscalationTest(unittest.TestCase):
    def state(self):
        return {"attempts": {}, "status": "running", "cursor": "dev_backend",
                "defect_seq": 0, "tasks": []}

    @mock.patch.object(orchestrator.taskqueue, "publish_current")
    @mock.patch.object(orchestrator.lifecycle, "model_escalated")
    @mock.patch.object(orchestrator.lifecycle, "retried")
    def test_cada_tarea_escala_una_sola_vez(self, _retried, _event, _publish):
        task = {"id": "T-1", "node": "dev_backend", "status": "pending"}
        state = self.state()
        finding = [{"file": "src/a.py", "line": 1, "rule": "x", "evidence": "x"}]
        budget = {"max_retries_per_gate": 3, "max_defect_tasks": 12}
        node = {"id": "dev_backend"}
        orchestrator.handle_defect(
            state, ".", node, task, "dev_backend", "G4", finding, budget,
            lambda *_args, **_kwargs: None)
        orchestrator.handle_defect(
            state, ".", node, task, "dev_backend", "G4", finding, budget,
            lambda *_args, **_kwargs: None)
        self.assertTrue(task["model_escalated"])
        self.assertEqual(task["model_escalation_count"], 1)
        _event.assert_called_once()

    @mock.patch.object(orchestrator.taskqueue, "make_defect")
    @mock.patch.object(orchestrator.lifecycle, "blocked")
    @mock.patch.object(orchestrator.lifecycle, "retried")
    def test_fallo_de_otro_propietario_no_consume_escalado(
            self, _retried, _blocked, make_defect):
        task = {"id": "T-2", "node": "qa", "status": "pending"}
        state = self.state()
        state["tasks"] = [task]
        make_defect.return_value = {"id": "D-001"}
        finding = [{"file": "src/a.py", "line": 1, "rule": "x", "evidence": "x"}]
        orchestrator.handle_defect(
            state, ".", {"id": "qa"}, task, "dev_backend", "R2", finding,
            {"max_retries_per_gate": 3, "max_defect_tasks": 12},
            lambda *_args, **_kwargs: None)
        self.assertNotIn("model_escalated", task)


if __name__ == "__main__":
    unittest.main()

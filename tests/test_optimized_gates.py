"""Concurrencia alrededor de gates inmutables."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd.runtime import optimized_gates


def pipeline():
    return {"runtime": {"gate_concurrency": 4, "gate_timeout_seconds": 2}, "node": [{
        "id": "dev_backend", "writes": ["src/"],
        "gates": ["G7", "G0", "G4", "G5", "G6", "R2"],
    }]}


def report(gate, status="pass"):
    return {"gate_id": gate["id"], "name": gate["name"],
            "node": "dev_backend", "status": status,
            "default_owner": gate["default_owner"], "route_by": "path",
            "findings": [] if status == "pass" else [{"file": "src/x.py"}]}


class TestConcurrentGates(unittest.TestCase):
    def test_g7_primero_deterministas_concurrentes_revisor_ultimo(self):
        lock = threading.Lock()
        active = 0
        maximum = 0
        calls = []

        def fake(gate, _node, _workdir, _pipeline):
            nonlocal active, maximum
            calls.append(gate["id"])
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return report(gate)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
                optimized_gates, "_run_cached", side_effect=fake):
            results = optimized_gates.run_node_gates("dev_backend", tmp, pipeline())
        self.assertEqual([item["gate_id"] for item in results],
                         ["G7", "G0", "G4", "G5", "G6", "R2"])
        self.assertEqual(calls[0], "G7")
        self.assertEqual(calls[-1], "R2")
        self.assertGreaterEqual(maximum, 2)

    def test_revisor_se_omite_si_determinista_falla(self):
        calls = []

        def fake(gate, _node, _workdir, _pipeline):
            calls.append(gate["id"])
            return report(gate, "fail" if gate["id"] == "G4" else "pass")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
                optimized_gates, "_run_cached", side_effect=fake):
            results = optimized_gates.run_node_gates("dev_backend", tmp, pipeline())
        self.assertNotIn("R2", calls)
        self.assertTrue(any(item["status"] == "fail" for item in results))

    def test_revision_verde_se_reutiliza_si_artefactos_no_cambian(self):
        review_gate = optimized_gates.load_registry()["R2"]
        calls = []

        def fake(gate, _node, _workdir, _pipeline):
            calls.append(gate["id"])
            return report(gate)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
            with patch.object(optimized_gates, "_execute_gate", side_effect=fake):
                first = optimized_gates._run_cached(
                    review_gate, "dev_backend", tmp, pipeline())
                second = optimized_gates._run_cached(
                    review_gate, "dev_backend", tmp, pipeline())
        self.assertEqual(first, second)
        self.assertEqual(calls, ["R2"])


if __name__ == "__main__":
    unittest.main()

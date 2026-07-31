"""Concurrencia alrededor de gates inmutables."""
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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


class TestHistorialDeIntentos(unittest.TestCase):
    """El reporte canonico se sobrescribe en cada intento.

    Sin historial no se puede medir la tasa de PRIMERA pasada de un gate, que es
    exactamente la metrica que dice si un cambio mejora algo o solo lo mueve.
    """

    def test_cada_intento_anade_una_linea(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            optimized_gates._save_report(directory, "architect", {
                "gate_id": "G2", "status": "fail",
                "findings": [{"rule": "adr-sin-coste"}]})
            optimized_gates._save_report(directory, "architect", {
                "gate_id": "G2", "status": "pass", "findings": []})
            history = (directory / "architect.G2.history.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(history), 2)
            primero, segundo = (json.loads(line) for line in history)
            self.assertEqual(primero["status"], "fail")
            self.assertEqual(primero["rules"], ["adr-sin-coste"])
            self.assertEqual(segundo["status"], "pass")
            # El canonico refleja el ultimo intento, como antes.
            canonico = json.loads(
                (directory / "architect.G2.json").read_text(encoding="utf-8"))
            self.assertEqual(canonico["status"], "pass")

    def test_escrituras_concurrentes_no_intercalan(self):
        """Los gates corren en un ThreadPoolExecutor."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            report = {"gate_id": "G4", "status": "pass", "findings": []}
            with ThreadPoolExecutor(max_workers=8) as pool:
                for _ in range(40):
                    pool.submit(optimized_gates._save_report,
                                directory, "qa", dict(report))
            lines = (directory / "qa.G4.history.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 40)
            for line in lines:
                json.loads(line)          # cada linea es JSON completo

"""Pruebas de liveness: contencion y procesos colgados terminan acotados."""
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd import server
from sdd.core import process_control, run_lease
from sdd.presentation import cli
from sdd.runtime import optimized_gates, orchestrator


class TestRunLease(unittest.TestCase):
    def test_segunda_corrida_falla_rapido_y_luego_puede_reintentar(self):
        with tempfile.TemporaryDirectory() as tmp:
            with run_lease.acquire(tmp, 0):
                started = __import__("time").monotonic()
                with self.assertRaises(run_lease.RunBusyError):
                    with run_lease.acquire(tmp, 0.05):
                        pass
                self.assertLess(__import__("time").monotonic() - started, 0.5)
            with run_lease.acquire(tmp, 0) as lease:
                self.assertTrue(lease.path.exists())

    def test_orquestador_con_lease_ocupado_no_toca_el_estado(self):
        with tempfile.TemporaryDirectory() as tmp, run_lease.acquire(tmp, 0):
            result = subprocess.run(
                [sys.executable, "-m", "sdd.runtime.orchestrator",
                 "--workdir", tmp, "--simulate", "--autonomous"],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn("corrida rechazada", result.stdout)
            self.assertFalse((Path(tmp) / ".agent/state.json").exists())

    def test_clean_no_borra_una_corrida_activa(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / ".agent/keep.txt"
            marker.parent.mkdir()
            marker.write_text("active", encoding="utf-8")
            with run_lease.acquire(tmp, 0), patch.object(
                    process_control, "timeout_seconds", return_value=0.01):
                code = cli.clean(SimpleNamespace(workdir=tmp))
            self.assertEqual(code, 2)
            self.assertTrue(marker.exists())


class TestBoundedProcesses(unittest.TestCase):
    def test_git_timeout_se_convierte_en_resultado_acotado(self):
        error = subprocess.TimeoutExpired(["git"], 1, output=b"partial")
        with patch.object(process_control.subprocess, "run", side_effect=error):
            result = process_control.run_git(".", "status")
        self.assertEqual(result.returncode, 124)
        self.assertIn(b"tiempo configurado", result.stderr)

    def test_agente_timeout_se_enruta_como_fallo(self):
        cfg = {"runtime": {"simulate_cmd": "fake", "agent_cmd": "fake",
                           "agent_timeout_seconds": 0.01}}
        node = {"id": "product", "prompt": "agents/product.md"}
        error = subprocess.TimeoutExpired(["fake"], 0.01, output=b"partial")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                process_control.subprocess, "run", side_effect=error):
            code, detail = orchestrator.invoke_agent(
                node, tmp, cfg, True, "task")
        self.assertEqual(code, 124)
        self.assertIn("tiempo configurado", detail)

    def test_gate_timeout_es_rojo_no_excepcion(self):
        gate = {"id": "G0", "name": "x", "cmd": "{py} missing.py",
                "default_owner": "product", "route_by": "node"}
        pipeline = {"runtime": {"gate_timeout_seconds": 0.01}}
        error = subprocess.TimeoutExpired(["fake"], 0.01)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                process_control.subprocess, "run", side_effect=error):
            result = optimized_gates._execute_gate(gate, "product", tmp, pipeline)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["findings"][0]["rule"], "gate-timeout")


class TestWebReservation(unittest.TestCase):
    def test_solo_un_post_concurrente_reserva_la_corrida(self):
        with server._LOCK:
            original = dict(server.RUN)
            server.RUN["status"] = "idle"
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                claims = list(pool.map(lambda _item: server._claim_run(), range(8)))
            self.assertEqual(claims.count(True), 1)
            self.assertEqual(claims.count(False), 7)
        finally:
            with server._LOCK:
                server.RUN.clear()
                server.RUN.update(original)


if __name__ == "__main__":
    unittest.main()

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


class TestDiagnosticoDeOscilacion(unittest.TestCase):
    """Un gate que cambia de veredicto sobre la misma unidad tiene dos causas
    posibles con remedios OPUESTOS, y confundirlas cuesta caro: si es
    no-determinismo, dar mas informacion al agente le hace corregir contra
    ruido; si es regresion, es justo lo que necesita."""

    def _journal(self, base, entradas):
        path = base / ".agent/reports/qa.G9.history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in entradas),
                        encoding="utf-8")

    def test_misma_huella_veredictos_distintos_es_no_determinismo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._journal(base, [
                {"status": "fail", "tree_hash": "aaa", "rules": ["suite-roja"]},
                {"status": "pass", "tree_hash": "aaa", "rules": []},
            ])
            d = optimized_gates.diagnose_oscillation(str(base), "qa")
            self.assertEqual(d["diagnostico"], "no-determinismo")
            self.assertEqual(d["huellas_no_deterministas"], 1)

    def test_huella_distinta_en_cada_veredicto_es_regresion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._journal(base, [
                {"status": "fail", "tree_hash": "aaa", "rules": ["lint-rojo"]},
                {"status": "fail", "tree_hash": "bbb", "rules": ["typecheck-rojo"]},
                {"status": "pass", "tree_hash": "ccc", "rules": []},
            ])
            d = optimized_gates.diagnose_oscillation(str(base), "qa")
            self.assertEqual(d["diagnostico"], "regresion")
            self.assertEqual(d["huellas_no_deterministas"], 0)
            self.assertEqual(d["cambios_de_veredicto"], 1)

    def test_sin_cambios_es_estable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._journal(base, [{"status": "pass", "tree_hash": "aaa"}] * 3)
            self.assertEqual(
                optimized_gates.diagnose_oscillation(str(base), "qa")["diagnostico"],
                "estable")

    def test_sin_historial_lo_dice_en_vez_de_suponer(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = optimized_gates.diagnose_oscillation(tmp, "qa")
            self.assertEqual(d["motivo"], "sin historial")
            self.assertEqual(d["veredictos"], 0)

    def test_entradas_sin_huella_se_cuentan_aparte(self):
        """Los journals de corridas anteriores a la instrumentacion no llevan
        huella: no deben clasificarse como estables por omision."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._journal(base, [{"status": "fail"}, {"status": "pass"}])
            d = optimized_gates.diagnose_oscillation(str(base), "qa")
            self.assertEqual(d["sin_huella"], 2)
            self.assertEqual(d["huellas_no_deterministas"], 0)
            self.assertEqual(d["diagnostico"], "regresion")


class TestTransferenciaDeHistorial(unittest.TestCase):
    """Los nodos del bucle de tareas corren en worktrees efimeros: sin
    transferir, su historial de gates muere con el worktree — y G9 solo corre
    ahi, asi que era exactamente el historial que se perdia."""

    def _escribir(self, base, nombre, entradas):
        path = base / ".agent/reports" / nombre
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in entradas),
                        encoding="utf-8")

    def test_anexa_en_vez_de_sobrescribir(self):
        """Dos workers pueden tener journal del mismo nodo y gate; copiar el
        archivo perderia el de todos menos uno."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            destino, w1, w2 = base / "repo", base / "w1", base / "w2"
            self._escribir(destino, "qa.G9.history.jsonl",
                           [{"status": "fail", "tree_hash": "aaa"}])
            self._escribir(w1, "qa.G9.history.jsonl",
                           [{"status": "fail", "tree_hash": "bbb"}])
            self._escribir(w2, "qa.G9.history.jsonl",
                           [{"status": "pass", "tree_hash": "ccc"}])
            self.assertEqual(optimized_gates.transfer_history(w1, destino), 1)
            self.assertEqual(optimized_gates.transfer_history(w2, destino), 1)
            lineas = (destino / ".agent/reports/qa.G9.history.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lineas), 3)
            self.assertEqual([json.loads(l)["tree_hash"] for l in lineas],
                             ["aaa", "bbb", "ccc"])

    def test_solo_transfiere_journals_no_el_reporte_canonico(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            destino, origen = base / "repo", base / "w1"
            self._escribir(origen, "qa.G9.history.jsonl", [{"status": "pass"}])
            (origen / ".agent/reports/qa.G9.json").write_text("{}", encoding="utf-8")
            optimized_gates.transfer_history(origen, destino)
            self.assertTrue((destino / ".agent/reports/qa.G9.history.jsonl").exists())
            self.assertFalse((destino / ".agent/reports/qa.G9.json").exists())

    def test_worktree_sin_reportes_no_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(
                optimized_gates.transfer_history(base / "vacio", base / "repo"), 0)


class TestTransferenciaIdempotente(unittest.TestCase):
    def test_transferir_dos_veces_el_mismo_worktree_no_duplica(self):
        """Se observo en una corrida real del demo: el mismo worktree se
        transfiere mas de una vez y la linea repetida inflaba el conteo de
        veredictos del diagnostico."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origen = base / "w1"
            path = origen / ".agent/reports/qa.G9.history.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": 1.5, "status": "fail",
                                        "tree_hash": "aaa"}) + "\n",
                            encoding="utf-8")
            destino = base / "repo"
            optimized_gates.transfer_history(origen, destino)
            optimized_gates.transfer_history(origen, destino)
            optimized_gates.transfer_history(origen, destino)
            lineas = (destino / ".agent/reports/qa.G9.history.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lineas), 1)

    def test_lineas_distintas_del_mismo_gate_si_se_anexan(self):
        """La deduplicacion no debe tragarse veredictos legitimos distintos."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origen, destino = base / "w1", base / "repo"
            path = origen / ".agent/reports/qa.G9.history.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": 1.0, "status": "fail"}) + "\n",
                            encoding="utf-8")
            optimized_gates.transfer_history(origen, destino)
            path.write_text(json.dumps({"at": 1.0, "status": "fail"}) + "\n"
                            + json.dumps({"at": 2.0, "status": "pass"}) + "\n",
                            encoding="utf-8")
            optimized_gates.transfer_history(origen, destino)
            lineas = (destino / ".agent/reports/qa.G9.history.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lineas), 2)
            self.assertEqual([json.loads(l)["status"] for l in lineas],
                             ["fail", "pass"])

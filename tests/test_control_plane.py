"""Pruebas del plano de control: el router (supervisor sin juicio) y el techo
de presupuesto (escalacion a humano). El demo feliz NUNCA ejercita la escalacion;
esta suite si.

    python -m unittest discover -s tests
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))
from sdd.runtime.orchestrator import route

PY = sys.executable

PIPELINE = {"node": [
    {"id": "dev_backend", "writes": ["src/api/", "src/domain/"]},
    {"id": "dev_frontend", "writes": ["src/web/"]},
    {"id": "qa", "writes": ["tests/", "spec/40_qa/"]},
]}


def report(gate_id, node, findings, route_by="path", owner="dev_backend", status="fail"):
    return {"gate_id": gate_id, "node": node, "status": status,
            "route_by": route_by, "default_owner": owner, "findings": findings}


def f(path):
    return [{"file": path, "line": 1, "rule": "r", "evidence": "e"}]


class TestRouter(unittest.TestCase):
    """El supervisor enruta por propiedad del artefacto, sin heuristica semantica."""

    def test_todo_pass_no_enruta(self):
        reps = [report("G4", "dev_backend", [], status="pass")]
        self.assertEqual(route(reps, PIPELINE), (None, None, []))

    def test_enruta_por_path_del_hallazgo_no_por_nodo_que_corrio(self):
        # El reporte lo produjo dev_backend, pero el hallazgo esta en src/web/:
        # el dueno es dev_frontend. Enruta por propiedad, no por sintoma.
        reps = [report("G5", "dev_backend", f("src/web/x.ts"), route_by="path")]
        owner, gate, _ = route(reps, PIPELINE)
        self.assertEqual((owner, gate), ("dev_frontend", "G5"))

    def test_route_by_gate_ignora_el_path_y_usa_el_dueno_del_gate(self):
        # G8 apunta al .feature (bajo spec/), pero el responsable es QA.
        reps = [report("G8", "qa", f("spec/10_product/features/x.feature"),
                       route_by="gate", owner="qa")]
        owner, gate, _ = route(reps, PIPELINE)
        self.assertEqual((owner, gate), ("qa", "G8"))

    def test_g7_devuelve_al_nodo_infractor(self):
        # Violacion de propiedad: qa escribio en src/api/. G7 devuelve al nodo que corrio.
        reps = [report("G7", "qa", f("src/api/x.py"), owner="dev_backend")]
        owner, gate, _ = route(reps, PIPELINE)
        self.assertEqual((owner, gate), ("qa", "G7"))


class TestBudgetEscalation(unittest.TestCase):
    """Con un agente que nunca corrige, el pipeline debe escalar, no girar sin fin."""

    def _demo_repo(self, tmp):
        wd = Path(tmp)
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(wd), *args], check=True, capture_output=True)
        (wd / ".gitignore").write_text(".agent/\n")
        subprocess.run(["git", "-C", str(wd), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wd), "commit", "-qm", "init"], check=True,
                       capture_output=True)
        return wd

    def test_agente_atascado_escala_a_humano(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = self._demo_repo(tmp)
            proc = subprocess.run(
                [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd),
                 "--simulate", "--auto-approve-human"],
                capture_output=True, text=True,
                env={**__import__("os").environ, "SDD_FAKE_STUCK": "1"})
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("estado final: escalated", proc.stdout)
            self.assertIn("ESCALATE_HUMAN", proc.stdout)
            # Techo de reintentos = 2: agota en el 3er intento. No existe un segundo
            # presupuesto interno por escalado de modelo. Se mide sobre el contador del
            # orquestador: contar el substring "AGENTE" coincidia con AGENTE_INICIO,
            # AGENTE y AGENTE_EN_ESPERA a la vez, asi que medía vocabulario de log en
            # lugar de llamadas al agente.
            llamadas = int(re.search(r"llamadas a agente: (\d+)", proc.stdout).group(1))
            self.assertEqual(llamadas, 3, proc.stdout)


if __name__ == "__main__":
    unittest.main()

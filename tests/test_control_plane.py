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
from sdd.runtime.orchestrator import refund_attempts, route
from sdd.runtime.run_state import load_state, prepare_resume
from sdd.runtime.workflow_defects import classify_defect

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


class TestReembolsoDeReintentos(unittest.TestCase):
    """Un gate que pasa recupera su presupuesto de reintentos; un gate cuyo
    veredicto OSCILA no puede recuperarlo indefinidamente.

    Sin tope, cada `pass` intermedio devolvia el presupuesto entero y la unidad
    ciclaba sin converger. Medido en demo-fastapi-fullstack: attempts registraba
    T-003:G9=5 frente a 56 ejecuciones reales de G9, y solo los topes globales
    acotaron la corrida.
    """

    BUDGET = {"max_retries_per_gate": 2}

    def test_pasar_a_la_primera_no_consume_reembolso(self):
        """No hay nada que devolver si nunca se gasto: si esto consumiera cuota,
        un gate sano agotaria el tope de oscilacion sin haber oscilado nunca."""
        state = {"attempts": {}, "gate_refunds": {}}
        self.assertFalse(refund_attempts(state, "T-001:G9", self.BUDGET))
        self.assertEqual(state["gate_refunds"], {})

    def test_el_caso_legitimo_sigue_funcionando(self):
        """Un gate que fallo, se corrigio y paso debe quedar sin rencor: si vuelve
        a fallar mas tarde por algo distinto, merece presupuesto nuevo."""
        state = {"attempts": {"T-001:G4": 2}, "gate_refunds": {}}
        self.assertTrue(refund_attempts(state, "T-001:G4", self.BUDGET))
        self.assertNotIn("T-001:G4", state["attempts"])
        self.assertEqual(state["gate_refunds"]["T-001:G4"], 1)

    def test_un_fallo_aislado_no_consume_cuota_nunca(self):
        """Solo cuenta el reembolso que RESCATA a la unidad del borde.

        Sin esta distincion el tope se agotaba en el camino feliz: en el demo
        `product:G1` llegaba a 2 de 2 reembolsos solo por fallar y arreglarse dos
        veces, y un tercer ciclo legitimo habria escalado sin motivo.
        """
        state = {"attempts": {}, "gate_refunds": {}}
        for ciclo in range(10):
            state["attempts"]["product:G1"] = 1     # un fallo: nunca al borde
            self.assertTrue(refund_attempts(state, "product:G1", self.BUDGET),
                            f"el ciclo {ciclo} del camino feliz se bloqueo")
        self.assertEqual(state["gate_refunds"], {})

    def test_la_oscilacion_deja_de_refinanciarse(self):
        state = {"attempts": {}, "gate_refunds": {}}
        for ciclo in range(1, 3):          # dos reembolsos: el tope es 2
            state["attempts"]["T-003:G9"] = 2
            self.assertTrue(refund_attempts(state, "T-003:G9", self.BUDGET),
                            f"el ciclo {ciclo} debia reembolsarse")
        state["attempts"]["T-003:G9"] = 2
        self.assertFalse(refund_attempts(state, "T-003:G9", self.BUDGET))
        # El contador SOBREVIVE, asi que la escalacion normal puede ocurrir.
        self.assertEqual(state["attempts"]["T-003:G9"], 2)
        self.assertEqual(state["gate_refunds"]["T-003:G9"], 2)

    def test_el_tope_es_por_unidad_y_gate_no_global(self):
        """Que una unidad oscile no debe castigar a otra."""
        state = {"attempts": {}, "gate_refunds": {"T-003:G9": 2}}
        state["attempts"]["T-004:G9"] = 2
        self.assertTrue(refund_attempts(state, "T-004:G9", self.BUDGET))
        state["attempts"]["T-003:G9"] = 2
        self.assertFalse(refund_attempts(state, "T-003:G9", self.BUDGET))

    def test_con_cuota_agotada_un_fallo_aislado_sigue_perdonandose(self):
        """Agotada la cuota, un gate que no esta al borde todavia se reembolsa: el
        tope frena la oscilacion, no el progreso normal."""
        state = {"attempts": {"T-003:G9": 1}, "gate_refunds": {"T-003:G9": 2}}
        self.assertTrue(refund_attempts(state, "T-003:G9", self.BUDGET))
        self.assertNotIn("T-003:G9", state["attempts"])

    def test_la_escalacion_ocurre_cuando_deja_de_reembolsarse(self):
        """La consecuencia que importa: con el contador ya no refinanciado, el
        clasificador declara agotado el presupuesto en vez de ciclar."""
        state = {"attempts": {"T-003:G9": 2}, "gate_refunds": {"T-003:G9": 2},
                 "defect_seq": 0}
        self.assertFalse(refund_attempts(state, "T-003:G9", self.BUDGET))
        decision = classify_defect(
            state, {"id": "qa"}, {"id": "T-003", "node": "qa"}, "qa", "G9",
            [{"file": "src/api/main.py", "line": 0, "rule": "suite-roja",
              "evidence": "x"}],
            {"max_retries_per_gate": 2, "max_defect_tasks": 12})
        self.assertTrue(decision["exhausted"])
        self.assertEqual(decision["attempt"], 3)

    def test_reanudar_limpia_los_reembolsos(self):
        """Reanudar da presupuesto fresco. Si los reembolsos sobrevivieran, una
        corrida reanudada escalaria antes de intentar nada."""
        with tempfile.TemporaryDirectory() as tmp:
            state, _ = load_state(tmp, "product")
            state["status"] = "escalated"
            state["attempts"] = {"T-003:G9": 2}
            state["gate_refunds"] = {"T-003:G9": 2}
            prepare_resume(state, tmp)
            self.assertEqual(state["attempts"], {})
            self.assertEqual(state["gate_refunds"], {})

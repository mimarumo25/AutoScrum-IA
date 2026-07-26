"""Pruebas del revisor R1: el gate blando.

R1 es la unica pieza del sistema con juicio, asi que es la que mas cerca hay que
tenerla vigilada. Lo que se verifica aqui es que sus limites se cumplen:

  - solo `blocking` frena; `mejora` se registra y deja pasar;
  - una revision limpia no consume presupuesto de rondas;
  - agotado el tope, se sigue adelante DEJANDO CONSTANCIA;
  - si el revisor se cae o responde algo ilegible, la corrida continua y se avisa;
  - nunca se ejecuta sobre un artefacto que los gates deterministas ya reprobaron,
    y nunca puede volver verde un gate rojo.

    python -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
GATES = ROOT / "gates"
sys.path.insert(0, str(GATES))

import check_review  # noqa: E402

PY = sys.executable
PIPELINE = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))

PRD_OK = "# PRD reinscripcion\nFR-001 el acudiente renueva la matricula.\n"
FEATURE_OK = """
Caracteristica: Reinscripcion

  @FR-001 @SCN-001 @p1 @critical
  Escenario: renovacion exitosa
    Dado que el acudiente tiene sesion activa
    Cuando confirma la renovacion
    Entonces recibe el comprobante
"""


def sin_credenciales(**extra):
    """Entorno sin ninguna API key: garantiza que la prueba no llama a un modelo."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["ANTHROPIC_API_KEY"] = ""
    env.pop("SDD_SIMULATE", None)
    env.update(extra)
    return env


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)
        self.w("spec/00_intake.yaml", "idea: renovacion de matriculas\n")
        self.w("spec/10_product/prd.md", PRD_OK)
        self.w("spec/10_product/features/x.feature", FEATURE_OK)

    def tearDown(self):
        self._tmp.cleanup()

    def w(self, rel, body):
        p = self.wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")

    def revisar(self, node="product", max_rounds=2, env=None):
        proc = subprocess.run(
            [PY, str(GATES / "check_review.py"), "--workdir", str(self.wd),
             "--node", node, "--max-rounds", str(max_rounds),
             "--prompt", str(ROOT / "agents/reviewer.md")],
            capture_output=True, text=True,
            env=env if env is not None else sin_credenciales(SDD_SIMULATE="1"))
        findings = json.loads(proc.stdout or "{}").get("findings", [])
        return findings, proc

    def estado(self, node="product"):
        p = self.wd / f".agent/review/{node}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class TestParseo(unittest.TestCase):
    """La salida del revisor es texto de un modelo: hay que desconfiar de ella."""

    def test_sin_nada_parseable_es_error(self):
        hallazgos, error = check_review.parse("claro, aqui tienes mi analisis…")
        self.assertEqual(hallazgos, [])
        self.assertIn("formato reconocible", error)

    def test_json_en_fence_sin_bloque_review_se_acepta(self):
        # DeepSeek a veces devuelve ```json …``` en vez del envoltorio <<<REVIEW>>>.
        raw = ('Aqui esta mi analisis:\n```json\n{"findings": ['
               '{"severity":"blocking","file":"a.md","line":1,"rule":"r","evidence":"x"}]}\n```')
        hallazgos, error = check_review.parse(raw)
        self.assertIsNone(error)
        self.assertEqual(len(hallazgos), 1)

    def test_json_crudo_con_findings_se_acepta(self):
        raw = 'Reviso y encuentro:\n{"findings": []}\nEso es todo.'
        hallazgos, error = check_review.parse(raw)
        self.assertIsNone(error)
        self.assertEqual(hallazgos, [])

    def test_json_invalido_es_error(self):
        hallazgos, error = check_review.parse("<<<REVIEW>>>\n{no es json}\n<<<END>>>")
        self.assertIn("JSON invalido", error)

    def test_lista_vacia_es_respuesta_valida(self):
        hallazgos, error = check_review.parse('<<<REVIEW>>>{"findings": []}<<<END>>>')
        self.assertIsNone(error)
        self.assertEqual(hallazgos, [])

    def test_severidad_desconocida_se_degrada_a_mejora(self):
        # Ante la duda no se bloquea: inventarse una severidad no da poder de veto.
        raw = ('<<<REVIEW>>>{"findings":[{"severity":"critico-urgente",'
               '"file":"a.md","line":1,"rule":"r","evidence":"algo"}]}<<<END>>>')
        hallazgos, error = check_review.parse(raw)
        self.assertIsNone(error)
        self.assertEqual(hallazgos[0]["severity"], "mejora")

    def test_hallazgo_sin_evidencia_se_descarta(self):
        raw = ('<<<REVIEW>>>{"findings":[{"severity":"blocking","file":"a.md"},'
               '{"severity":"blocking","file":"b.md","evidence":"esto si"}]}<<<END>>>')
        hallazgos, _ = check_review.parse(raw)
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["file"], "b.md")


class TestSeveridad(RepoCase):
    """Solo lo blocking frena. Lo demas se registra y el pipeline sigue."""

    def test_blocking_frena_el_nodo(self):
        hallazgos, proc = self.revisar("product")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["rule"], "requisito-sin-caso-negativo")

    def test_mejora_no_frena_pero_queda_registrada(self):
        hallazgos, proc = self.revisar("architect")
        self.assertEqual(proc.returncode, 0, "una mejora no puede frenar la corrida")
        self.assertEqual(hallazgos, [])
        mejoras = self.estado("architect")["mejoras"]
        self.assertEqual(len(mejoras), 1)
        self.assertEqual(mejoras[0]["rule"], "nfr-unico")

    def test_revision_limpia_no_consume_presupuesto_de_rondas(self):
        self.revisar("product")                       # 1a: bloquea
        self.assertEqual(self.estado()["rounds"], 1)
        self.revisar("product")                       # 2a: limpia
        st = self.estado()
        self.assertEqual(st["rounds"], 1, "una revision limpia no gasta ronda")
        self.assertEqual(st["invocations"], 2)


class TestConvergencia(RepoCase):
    """Un revisor siempre encuentra algo: el tope es lo que garantiza que acabe."""

    def test_tope_de_rondas_deja_pasar_dejando_constancia(self):
        p = self.wd / ".agent/review/product.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"rounds": 2, "invocations": 2,
                                 "historial": [], "mejoras": [], "nota": ""}),
                     encoding="utf-8")
        hallazgos, proc = self.revisar("product", max_rounds=2)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(hallazgos, [])
        self.assertIn("tope de 2 ronda(s)", self.estado()["nota"])

    def test_con_tope_cero_no_revisa_nada(self):
        hallazgos, proc = self.revisar("product", max_rounds=0)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(hallazgos, [])


class TestDegradacion(RepoCase):
    """Un critico caido no puede tumbar la corrida, pero tampoco pasar en silencio."""

    def test_revisor_sin_proveedor_pasa_y_avisa(self):
        # Sin API key y sin modo simulado: providers falla. El gate debe dejar
        # pasar (los G* deterministas siguen sosteniendo la correccion) y avisar.
        hallazgos, proc = self.revisar("product", env=sin_credenciales())
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(hallazgos, [])
        nota = self.estado()["nota"]
        self.assertIn("revision no disponible", nota)
        self.assertIn("R1", proc.stderr)


class TestOrdenYAlcance(RepoCase):
    """R1 corre al final, solo sobre artefactos ya verdes, y no puede absolver."""

    def _gates(self, node):
        env = dict(sin_credenciales(SDD_SIMULATE="1"))
        proc = subprocess.run(
            [PY, str(GATES / "run_gates.py"), "--node", node, "--workdir", str(self.wd)],
            capture_output=True, text=True, env=env)
        return json.loads(proc.stdout or "[]"), proc

    def test_no_se_ejecuta_si_un_gate_determinista_fallo(self):
        # PRD con un FR sin escenario: G1 rojo. Criticar esto seria tirar tokens.
        self.w("spec/10_product/prd.md", PRD_OK + "FR-002 el admin consulta por sede.\n")
        reports, proc = self._gates("product")
        ids = [r["gate_id"] for r in reports]
        self.assertIn("G1", ids)
        self.assertNotIn("R1", ids, "R1 no debe criticar un artefacto ya reprobado")
        self.assertEqual(proc.returncode, 1)

    def test_se_ejecuta_cuando_los_deterministas_estan_verdes(self):
        reports, _ = self._gates("product")
        self.assertEqual([r["gate_id"] for r in reports], ["G0", "G1", "R1"])

    def test_r1_verde_no_absuelve_un_gate_rojo(self):
        # Aunque R1 diera verde, un G* rojo manda: el nodo no pasa.
        self.w("spec/10_product/prd.md", PRD_OK + "FR-002 el admin consulta por sede.\n")
        reports, proc = self._gates("product")
        self.assertTrue(any(r["status"] == "fail" for r in reports))
        self.assertEqual(proc.returncode, 1)


class TestEnrutamiento(unittest.TestCase):
    """El defecto vuelve al nodo que produjo el artefacto, no a un dueno fijo."""

    def test_r1_esta_declarado_como_route_by_node(self):
        registry = tomllib.loads((GATES / "registry.toml").read_text(encoding="utf-8"))
        r1 = next(g for g in registry["gate"] if g["id"] == "R1")
        self.assertEqual(r1["route_by"], "node")
        self.assertTrue(r1["skip_if_prior_failed"])

    def test_r1_solo_esta_en_los_nodos_de_especificacion(self):
        con_r1 = {n["id"] for n in PIPELINE["node"] if "R1" in n.get("gates", [])}
        self.assertEqual(con_r1, {"product", "architect", "planner"})

    def test_revisor_es_el_ultimo_gate_de_su_nodo(self):
        # R1 (spec) y R2 (codigo) siempre corren al final: primero los deterministas.
        for n in PIPELINE["node"]:
            gates = n.get("gates", [])
            if "R1" in gates or "R2" in gates:
                self.assertIn(gates[-1], ("R1", "R2"), n["id"])

    def test_r2_esta_en_los_nodos_de_codigo(self):
        con_r2 = {n["id"] for n in PIPELINE["node"] if "R2" in n.get("gates", [])}
        self.assertEqual(con_r2, {"dev_backend", "dev_frontend", "qa"})

    def test_r2_registrado_como_juicio_acotado(self):
        registry = tomllib.loads((GATES / "registry.toml").read_text(encoding="utf-8"))
        r2 = next(g for g in registry["gate"] if g["id"] == "R2")
        self.assertEqual(r2["route_by"], "node")
        self.assertTrue(r2["skip_if_prior_failed"])


class TestEstadoPorTarea(RepoCase):
    """R2 revisa por tarea: las rondas de una tarea no gastan el cupo de otra."""

    def test_estado_de_revision_se_lleva_por_tarea(self):
        self.w(".agent/current_task.json",
               json.dumps({"id": "T-001", "kind": "plan",
                           "deliverables": ["src/domain/x.py"]}))
        self.w("src/domain/x.py", "def f():\n    return 1\n")
        self.revisar("dev_backend", env=sin_credenciales(SDD_SIMULATE="1"))
        # El archivo de estado lleva el id de la tarea, no solo el nodo.
        self.assertTrue((self.wd / ".agent/review/dev_backend.T-001.json").exists())
        self.assertFalse((self.wd / ".agent/review/dev_backend.json").exists())


if __name__ == "__main__":
    unittest.main()

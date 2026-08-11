"""Pruebas de los gates que convierten el verde en una afirmacion verificable.

Cada clase reproduce un modo de fallo REAL de la corrida que motivo estos gates:

  G0  dev_backend murio sin escribir un solo archivo y G7/G4/G5 dieron verde,
      porque un gate que inspecciona artefactos no tiene nada que reprobar
      cuando no hay artefactos.
  G6  QA escribio seis pruebas que importaban src/calculator.js, src/parser.js y
      src/evaluator.js. Ninguno existia. El pipeline dio verde igual.
  G9  ningun gate ejecutaba nada: 544 lineas de pruebas que ni siquiera podian
      importar sus modulos contaban como cobertura.
  G10 el plan de tareas no existia; los prompts de dev ya lo daban por hecho.

    python -m unittest discover -s tests
"""
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
GATES = ROOT / "gates"
PY = sys.executable
# Nombre del interprete tal como lo resolvera shutil.which dentro del gate.
PYNAME = Path(shutil.which("python") or shutil.which("python3") or PY).name

PIPELINE = ROOT / "pipeline.toml"


def run_gate(script, *args):
    proc = subprocess.run([PY, str(GATES / script), *args],
                          capture_output=True, text=True)
    return json.loads(proc.stdout or "{}").get("findings", []), proc


def rules(findings):
    return {f["rule"] for f in findings}


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def w(self, rel, body=""):
        p = self.wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
        return p


class TestG0Deliverable(RepoCase):
    """El agujero central: verde sobre el vacio."""

    def _g0(self, node):
        return run_gate("check_deliverable.py", "--workdir", str(self.wd),
                        "--node", node, "--pipeline", str(PIPELINE))[0]

    def test_nodo_que_no_escribio_nada_es_detectado(self):
        # Exactamente el caso dev_backend: el agente murio, el repo quedo intacto.
        findings = self._g0("product")
        self.assertIn("entregable-ausente", rules(findings))

    def test_entregable_vacio_no_cuenta_como_entregado(self):
        self.w("spec/10_product/prd.md", "# PRD\nFR-001 algo\n")
        self.w("spec/10_product/features/x.feature", "   \n\n")
        findings = self._g0("product")
        self.assertIn("entregable-vacio", rules(findings))

    def test_entregables_completos_pasan(self):
        self.w("spec/10_product/prd.md", "# PRD\nFR-001 algo\n")
        self.w("spec/10_product/features/x.feature", "Caracteristica: x\n")
        self.assertEqual(self._g0("product"), [])

    def test_deliverables_de_la_tarea_activa_tambien_se_exigen(self):
        # dev_backend no declara must_produce: su contrato lo fija la tarea.
        self.w(".agent/current_task.json", json.dumps(
            {"id": "T-001", "deliverables": ["src/domain/parser.py"]}))
        findings = self._g0("dev_backend")
        self.assertIn("entregable-ausente", rules(findings))
        self.assertIn("T-001", findings[0]["evidence"])


class TestG6Imports(RepoCase):
    """El fallo literal de la corrida: pruebas que importan modulos inexistentes."""

    def _g6(self):
        return run_gate("check_imports.py", "--workdir", str(self.wd),
                        "--roots", "src,tests")[0]

    def test_import_js_a_modulo_inexistente_es_detectado(self):
        self.w("tests/unit/parser.test.js",
               "import { evaluate } from '../../src/calculator.js';\n")
        findings = self._g6()
        self.assertEqual(rules(findings), {"import-no-resuelve"})
        self.assertIn("calculator.js", findings[0]["evidence"])

    def test_import_js_resuelto_pasa(self):
        self.w("src/calculator.js", "export const evaluate = () => 1;\n")
        self.w("tests/unit/parser.test.js",
               "import { evaluate } from '../../src/calculator.js';\n")
        self.assertEqual(self._g6(), [])

    def test_paquete_del_gestor_no_se_juzga(self):
        # 'vitest' es una dependencia npm, no un modulo del repo: fuera de alcance.
        self.w("tests/unit/a.test.js", "import { describe } from 'vitest';\n")
        self.assertEqual(self._g6(), [])

    def test_import_python_local_inexistente_es_detectado(self):
        self.w("src/api/handler.py", "from src.domain.matricula import renovar\n")
        self.assertEqual(rules(self._g6()), {"import-no-resuelve"})

    def test_stdlib_no_se_juzga(self):
        self.w("src/api/handler.py", "import os\nimport json\n")
        self.assertEqual(self._g6(), [])

    def test_extension_omitida_resuelve(self):
        self.w("src/web/client.js", "export const x = 1;\n")
        self.w("src/web/main.js", "import { x } from './client';\n")
        self.assertEqual(self._g6(), [])


class TestG9Suite(RepoCase):
    """El unico gate que ejecuta. Sin el, el verde no significa nada."""

    def _g9(self):
        return run_gate("check_suite.py", "--workdir", str(self.wd), "--timeout", "120")[0]

    def test_sin_toolchain_no_se_puede_afirmar_nada(self):
        self.assertEqual(rules(self._g9()), {"toolchain-no-declarado"})

    def test_suite_roja_es_detectada_y_se_atribuye_al_codigo(self):
        self.w("src/domain/regla.py", """
            def evaluar(x):
                return 10 / x        # revienta con 0
        """)
        self.w("tests/test_regla.py", """
            import unittest
            from src.domain.regla import evaluar

            class T(unittest.TestCase):
                def test_cero(self):
                    evaluar(0)
        """)
        self.w("spec/20_arch/toolchain.yaml",
               f"language: python\ndir: .\ntest: {PYNAME} -m unittest discover -s tests\n")
        findings = self._g9()
        self.assertEqual(rules(findings), {"suite-roja"})
        # El traceback delata al modulo de dominio: el defecto va a su dueno,
        # no a QA por el hecho de que la prueba este en tests/.
        self.assertEqual(findings[0]["file"], "src/domain/regla.py")

    def test_suite_verde_pasa(self):
        self.w("src/domain/regla.py", "def evaluar(x):\n    return x + 1\n")
        self.w("tests/test_regla.py", """
            import unittest
            from src.domain.regla import evaluar

            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertEqual(evaluar(1), 2)
        """)
        self.w("spec/20_arch/toolchain.yaml",
               f"language: python\ndir: .\ntest: {PYNAME} -m unittest discover -s tests\n")
        self.assertEqual(self._g9(), [])

    def _toolchain(self, **pasos):
        """toolchain.yaml con los pasos dados. Se invocan scripts en vez de
        comandos con comillas: shlex.split no trata igual las comillas en win."""
        lineas = ["language: python", "dir: ."]
        lineas += [f"{paso}: {PYNAME} {orden}" for paso, orden in pasos.items()]
        self.w("spec/20_arch/toolchain.yaml", "\n".join(lineas) + "\n")

    def test_reporta_todos_los_pasos_rotos_no_solo_el_primero(self):
        """Antes cortaba en el primer paso rojo.

        En la corrida real de demo-fastapi-fullstack eso produjo 13 defectos en
        secuencia (lint -> suite -> typecheck -> suite -> lint...) porque cada
        vuelta destapaba una capa distinta que YA estaba roja. Cada vuelta cuesta
        una llamada al modelo con todo el contexto reconstruido, asi que ocultar
        3 de los 4 fallos multiplica por 4 el coste de la misma correccion.
        """
        self.w("falla.py", "import sys\nsys.exit(1)\n")
        self.w("src/domain/regla.py", "def evaluar(x):\n    return 10 / x\n")
        self.w("tests/test_regla.py", """
            import unittest
            from src.domain.regla import evaluar

            class T(unittest.TestCase):
                def test_cero(self):
                    evaluar(0)
        """)
        self._toolchain(lint="falla.py", typecheck="falla.py",
                        test="-m unittest discover -s tests")
        self.assertEqual(rules(self._g9()),
                         {"lint-rojo", "typecheck-rojo", "suite-roja"})

    def test_instalar_si_corta_el_resto(self):
        """Sin dependencias, tipar y probar no significan nada: seguir solo
        produciria hallazgos derivados que despistan al agente."""
        self.w("falla.py", "import sys\nsys.exit(1)\n")
        self._toolchain(install="falla.py", lint="falla.py", test="falla.py")
        self.assertEqual(rules(self._g9()), {"instalacion-fallida"})

    def test_un_binario_ausente_corta_el_resto(self):
        """Ningun agente arregla escribiendo codigo un binario que falta."""
        self.w("falla.py", "import sys\nsys.exit(1)\n")
        self.w("spec/20_arch/toolchain.yaml",
               "language: python\ndir: .\n"
               "lint: binario-que-no-existe-jamas check\n"
               f"test: {PYNAME} falla.py\n")
        self.assertEqual(rules(self._g9()), {"toolchain-no-disponible"})

    def test_el_veredicto_no_cambia_solo_su_detalle(self):
        """La propiedad que hace seguro este cambio: para cualquier arbol el
        pass/fail es identico al de antes. Solo cambia CUANTOS hallazgos trae un
        fail, nunca si es fail. Un arbol verde con varios pasos sigue pasando."""
        self.w("pasa.py", "import sys\nsys.exit(0)\n")
        self.w("src/domain/regla.py", "def evaluar(x):\n    return x + 1\n")
        self.w("tests/test_regla.py", """
            import unittest
            from src.domain.regla import evaluar

            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertEqual(evaluar(1), 2)
        """)
        self._toolchain(lint="pasa.py", typecheck="pasa.py",
                        test="-m unittest discover -s tests")
        self.assertEqual(self._g9(), [])

    def test_tabla_de_cobertura_no_culpa_al_backend_por_asercion_de_qa(self):
        self.w("src/api/main.py", "def health():\n    return 'ok'\n")
        self.w("tests/test_ui.py", "def test_ui():\n    assert False\n")
        self.w("fake_fail.py",
               "import sys\n"
               "print('src/api/main.py 21 0 100%')\n"
               "print('FAILED tests/test_ui.py::test_ui - AssertionError')\n"
               "sys.exit(1)\n")
        self.w("spec/20_arch/toolchain.yaml",
               f"language: python\ndir: .\ntest: {PYNAME} fake_fail.py\n")

        findings = self._g9()

        self.assertEqual(rules(findings), {"suite-roja"})
        self.assertEqual(findings[0]["file"], "tests/test_ui.py")

    def test_coverage_insuficiente_es_detectada(self):
        # El paso coverage es un comando mas; si falla (umbral no alcanzado) se
        # reporta como cobertura-insuficiente. Se usa un script en disco en vez de
        # 'python -c', porque en Windows shlex(posix=False) conserva las comillas.
        self.w("src/domain/regla.py", "def f(x):\n    return x\n")
        self.w("tests/test_regla.py",
               "import unittest\nfrom src.domain.regla import f\n"
               "class T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(f(1),1)\n")
        self.w("cov_fail.py", "import sys\nsys.exit(1)\n")
        self.w("spec/20_arch/toolchain.yaml",
               f"language: python\ndir: .\ntest: {PYNAME} -m unittest discover -s tests\n"
               f"coverage: {PYNAME} cov_fail.py\n")
        self.assertEqual(rules(self._g9()), {"cobertura-insuficiente"})

    def test_lint_rojo_es_detectado(self):
        self.w("src/domain/regla.py", "def f(x):\n    return x\n")
        self.w("tests/test_regla.py",
               "import unittest\nfrom src.domain.regla import f\n"
               "class T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(f(1))\n")
        self.w("lint_fail.py", "import sys\nsys.exit(2)\n")
        self.w("spec/20_arch/toolchain.yaml",
               f"language: python\ndir: .\nlint: {PYNAME} lint_fail.py\n"
               f"test: {PYNAME} -m unittest discover -s tests\n")
        self.assertEqual(rules(self._g9()), {"lint-rojo"})

    def test_binario_ausente_escala_en_vez_de_culpar_al_codigo(self):
        self.w("spec/20_arch/toolchain.yaml",
               "language: node\ndir: .\ntest: binario-que-no-existe-jamas test\n")
        self.assertEqual(rules(self._g9()), {"toolchain-no-disponible"})

    def test_toolchain_sin_comando_test_es_invalido(self):
        self.w("spec/20_arch/toolchain.yaml", "language: node\ndir: .\ninstall: echo hola\n")
        self.assertEqual(rules(self._g9()), {"toolchain-sin-test"})


class TestG10Plan(RepoCase):
    """El plan es el contrato que hace posible el bucle de tareas."""

    PLAN_OK = """
        tasks:
          - id: T-001
            title: dominio
            node: dev_backend
            fr_refs: [FR-001]
            deliverables: [src/domain/x.py]
            depends_on: []
            acceptance: x() devuelve error tipado
          - id: T-002
            title: pruebas
            node: qa
            fr_refs: [FR-001]
            deliverables: [tests/test_x.py]
            depends_on: [T-001]
            acceptance: SCN-001 cubierto y suite en verde
    """

    def _g10(self):
        return run_gate("check_plan.py", "--workdir", str(self.wd),
                        "--pipeline", str(PIPELINE))[0]

    def setUp(self):
        super().setUp()
        self.w("spec/10_product/prd.md", "# PRD\nFR-001 algo observable.\n")

    def test_plan_ausente_es_detectado(self):
        self.assertEqual(rules(self._g10()), {"plan-ausente"})

    def test_plan_valido_pasa(self):
        self.w("spec/30_plan/tasks.yaml", self.PLAN_OK)
        self.assertEqual(self._g10(), [])

    def test_readme_raiz_es_entregable_valido_de_backend(self):
        plan = self.PLAN_OK.replace(
            "deliverables: [src/domain/x.py]",
            "deliverables: [src/domain/x.py, README.md]",
        )
        self.w("spec/30_plan/tasks.yaml", plan)
        self.assertEqual(self._g10(), [])

    def test_prompt_consolida_documentacion_en_readme_raiz(self):
        prompt = (ROOT / "agents/planner.md").read_text(encoding="utf-8")
        self.assertIn("consolidala\n   en README.md en la raiz", prompt)
        self.assertIn("no crees src/README.md", prompt)
        self.assertIn("fr_refs validos", prompt)

    def test_ciclo_de_dependencias_es_detectado(self):
        self.w("spec/30_plan/tasks.yaml", self.PLAN_OK.replace(
            "depends_on: []", "depends_on: [T-002]"))
        self.assertIn("ciclo-de-dependencias", rules(self._g10()))

    def test_fr_sin_tarea_es_alcance_perdido(self):
        self.w("spec/10_product/prd.md", "# PRD\nFR-001 uno.\nFR-002 dos.\n")
        self.w("spec/30_plan/tasks.yaml", self.PLAN_OK)
        findings = self._g10()
        self.assertIn("fr-sin-tarea", rules(findings))
        self.assertTrue(any("FR-002" in f["evidence"] for f in findings))

    def test_entregable_fuera_de_propiedad_del_nodo(self):
        self.w("spec/30_plan/tasks.yaml",
               self.PLAN_OK.replace("[src/domain/x.py]", "[tests/test_x.py]"))
        self.assertIn("entregable-fuera-de-propiedad", rules(self._g10()))

    def test_qa_dividida_es_rechazada(self):
        # G8/G9 verifican el proyecto entero por tarea; QA partida en dos se atasca.
        dos_qa = self.PLAN_OK + """
          - id: T-003
            title: pruebas de integracion
            node: qa
            fr_refs: [FR-001]
            deliverables: [tests/test_api.py]
            depends_on: [T-002]
            acceptance: SCN-006 cubierto y suite en verde
    """
        self.w("spec/30_plan/tasks.yaml", dos_qa)
        self.assertIn("qa-dividida", rules(self._g10()))

    def test_plan_sin_qa_es_detectado(self):
        self.w("spec/30_plan/tasks.yaml", """
            tasks:
              - id: T-001
                title: dominio
                node: dev_backend
                fr_refs: [FR-001]
                deliverables: [src/domain/x.py]
                depends_on: []
                acceptance: x() devuelve error tipado
        """)
        self.assertIn("plan-sin-qa", rules(self._g10()))


if __name__ == "__main__":
    unittest.main()

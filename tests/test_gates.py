"""Pruebas de los gates de andamiaje (stdlib unittest, sin dependencias).

Cada gate se ejerce contra fixtures deliberadamente rotos y contra fixtures
limpios. Esto prueba que los checkers detectan violaciones en codigo que NUNCA
vieron — no en el guion de fake_agent — y que respetan su contrato:
stdout = {"findings":[...]} y exit 1 si hay hallazgos.

    python -m unittest discover -s tests        # cero dependencias
    pytest tests/                                # si esta instalado
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
GATES = ROOT / "gates"
PY = sys.executable


def run_checker(script, *args):
    """Corre un checker y devuelve (findings, returncode) segun su contrato."""
    proc = subprocess.run([PY, str(GATES / script), *map(str, args)],
                          capture_output=True, text=True)
    try:
        findings = json.loads(proc.stdout or "{}").get("findings", [])
    except json.JSONDecodeError:
        raise AssertionError(f"{script} no emitio JSON valido:\n"
                             f"STDOUT={proc.stdout!r}\nSTDERR={proc.stderr!r}")
    return findings, proc.returncode


def write(base, rel, body):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.lstrip("\n"), encoding="utf-8")
    return p


def rules(findings):
    return {f["rule"] for f in findings}


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def wdp(self):  # workdir como argumento --workdir (posix, seguro en win)
        return self.wd.as_posix()


class TestG1Traceability(GateTestCase):
    """G1: todo FR-### necesita al menos un escenario Gherkin."""

    def test_fr_sin_escenario_es_detectado(self):
        write(self.wd, "spec/10_product/prd.md", "FR-001 x\nFR-002 y\n")
        write(self.wd, "spec/10_product/features/f.feature",
              "@FR-001 @SCN-001\nEscenario: a\n")  # FR-002 sin escenario
        findings, rc = run_checker("check_traceability.py",
                                   "--mode", "product", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("fr-sin-escenario", rules(findings))

    def test_id_de_escenario_duplicado_es_detectado(self):
        write(self.wd, "spec/10_product/prd.md", "FR-001 x\n")
        write(self.wd, "spec/10_product/features/f.feature",
              "@FR-001 @SCN-001\nEscenario: a\n@FR-001 @SCN-001\nEscenario: b\n")
        findings, rc = run_checker("check_traceability.py",
                                   "--mode", "product", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("id-duplicado", rules(findings))

    def test_spec_completa_pasa(self):
        write(self.wd, "spec/10_product/prd.md", "FR-001 x\nFR-002 y\n")
        write(self.wd, "spec/10_product/features/f.feature",
              "@FR-001 @SCN-001\nEscenario: a\n@FR-002 @SCN-002\nEscenario: b\n")
        findings, rc = run_checker("check_traceability.py",
                                   "--mode", "product", "--workdir", self.wdp())
        self.assertEqual((rc, findings), (0, []))


class TestG2ArchSpec(GateTestCase):
    """G2: artefactos verificables presentes, NFR medibles, ADR con alternativas y coste."""

    def _minimo_valido(self):
        write(self.wd, "spec/20_arch/nfr.yaml",
              "nfr:\n  - id: NFR-001\n    metrica: latencia_p95_ms\n"
              "    umbral: 800\n    gate_id: manual\n")  # 'manual': no lo verifica una maquina
        write(self.wd, "spec/20_arch/api/openapi.yaml",
              "openapi: 3.1.0\ninfo: {title: x, version: '1'}\npaths: {}\n")
        write(self.wd, "spec/20_arch/env-contract.yaml",
              "variables:\n  - name: PAYMENT_API_URL\n    tipo: url\n    requerida: true\n")
        write(self.wd, "spec/20_arch/threat-model.md", "# STRIDE\nOWASP A01\n")
        write(self.wd, "spec/20_arch/adr/ADR-001.md",
              "# ADR-001\nAlternativa descartada: A.\nAlternativa descartada: B.\n"
              "Coste mensual: 28 USD.\n")

    def test_artefacto_faltante_es_detectado(self):
        self._minimo_valido()
        (self.wd / "spec/20_arch/threat-model.md").unlink()
        findings, rc = run_checker("check_arch_spec.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("artefacto-faltante", rules(findings))

    def test_nfr_sin_umbral_es_detectado(self):
        self._minimo_valido()
        write(self.wd, "spec/20_arch/nfr.yaml",
              "nfr:\n  - id: NFR-002\n    metrica: x\n")  # sin umbral ni gate_id
        findings, rc = run_checker("check_arch_spec.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("nfr-no-medible", rules(findings))

    def test_nfr_con_gate_inexistente_es_detectado(self):
        # Un NFR no puede decir "me verifica G11" si G11 no existe: eso es un
        # umbral que nadie comprueba disfrazado de verificado.
        self._minimo_valido()
        write(self.wd, "spec/20_arch/nfr.yaml",
              "nfr:\n  - id: NFR-001\n    metrica: x\n    umbral: 5\n    gate_id: G99\n")
        findings, rc = run_checker("check_arch_spec.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("nfr-gate-inexistente", rules(findings))

    def test_adr_sin_alternativas_ni_coste_es_detectado(self):
        self._minimo_valido()
        write(self.wd, "spec/20_arch/adr/ADR-001.md", "# ADR-001\nElegimos X porque si.\n")
        findings, rc = run_checker("check_arch_spec.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertTrue({"adr-sin-alternativas", "adr-sin-coste"} & rules(findings))

    def test_spec_tecnica_completa_pasa(self):
        self._minimo_valido()
        findings, rc = run_checker("check_arch_spec.py", "--workdir", self.wdp())
        self.assertEqual((rc, findings), (0, []))


class TestG4FileSize(GateTestCase):
    """G4: limite duro de lineas por archivo."""

    def test_archivo_sobre_el_limite_duro_es_detectado(self):
        write(self.wd, "src/api/big.py", "x = 1\n" * 600)
        findings, rc = run_checker("check_file_size.py", "--workdir", self.wdp(),
                                   "--hard", "500", "--warn", "300")
        self.assertEqual(rc, 1)
        self.assertIn("max-lines", rules(findings))

    def test_archivo_corto_pasa(self):
        write(self.wd, "src/api/small.py", "x = 1\n" * 50)
        findings, rc = run_checker("check_file_size.py", "--workdir", self.wdp(),
                                   "--hard", "500", "--warn", "300")
        self.assertEqual((rc, findings), (0, []))


class TestG5Hardcoding(GateTestCase):
    """G5: sin valores de entorno en codigo; toda env usada declarada en el contrato."""

    def test_url_quemada_es_detectada(self):
        write(self.wd, "src/api/x.py", 'URL = "https://api.wompi.co/v1/transactions"\n')
        findings, rc = run_checker("check_hardcoding.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("hardcoded-url", rules(findings))

    def test_env_no_declarada_es_detectada(self):
        write(self.wd, "src/api/x.py", 'import os\nv = os.environ["TENANT_HEADER"]\n')
        findings, rc = run_checker("check_hardcoding.py", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("env-no-declarada", rules(findings))

    def test_env_declarada_y_ejemplificada_pasa(self):
        write(self.wd, "spec/20_arch/env-contract.yaml",
              "variables:\n  - name: PAYMENT_API_URL\n    tipo: url\n")
        write(self.wd, ".env.example", "PAYMENT_API_URL=https://sandbox.example.test\n")
        write(self.wd, "src/api/x.py",
              'import os\nv = os.environ["PAYMENT_API_URL"]\n')
        findings, rc = run_checker("check_hardcoding.py", "--workdir", self.wdp())
        self.assertEqual((rc, findings), (0, []))


class TestG7TestIntegrity(GateTestCase):
    """G7: un nodo solo puede escribir dentro de sus paths declarados."""

    def _git_init(self):
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", str(self.wd), *args], check=True,
                           capture_output=True)

    def test_dev_escribiendo_en_tests_es_violacion(self):
        self._git_init()
        write(self.wd, "tests/sneaky.py", "# el Dev no puede tocar esto\n")
        findings, rc = run_checker("check_test_integrity.py", "--workdir", self.wdp(),
                                   "--node", "dev_backend",
                                   "--pipeline", (ROOT / "pipeline.toml").as_posix())
        self.assertEqual(rc, 1)
        self.assertIn("violacion-de-propiedad", rules(findings))

    def test_dev_escribiendo_en_su_path_pasa(self):
        self._git_init()
        write(self.wd, "src/api/handler.py", "def h(): ...\n")
        findings, rc = run_checker("check_test_integrity.py", "--workdir", self.wdp(),
                                   "--node", "dev_backend",
                                   "--pipeline", (ROOT / "pipeline.toml").as_posix())
        self.assertEqual((rc, findings), (0, []))


class TestG8ScenarioCoverage(GateTestCase):
    """G8: todo escenario @critical debe tener una prueba que lo referencie por id."""

    def test_escenario_critico_sin_prueba_es_detectado(self):
        write(self.wd, "spec/10_product/features/f.feature",
              "@FR-001 @SCN-001 @critical\nEscenario: a\n")
        write(self.wd, "tests/test_otra_cosa.py", "def test_algo(): assert True\n")
        findings, rc = run_checker("check_traceability.py",
                                   "--mode", "qa", "--workdir", self.wdp())
        self.assertEqual(rc, 1)
        self.assertIn("escenario-critico-sin-prueba", rules(findings))

    def test_escenario_critico_con_prueba_pasa(self):
        write(self.wd, "spec/10_product/features/f.feature",
              "@FR-001 @SCN-001 @critical\nEscenario: a\n")
        write(self.wd, "tests/test_scn.py", "def test_SCN_001(): assert True\n")
        findings, rc = run_checker("check_traceability.py",
                                   "--mode", "qa", "--workdir", self.wdp())
        self.assertEqual((rc, findings), (0, []))


if __name__ == "__main__":
    unittest.main()

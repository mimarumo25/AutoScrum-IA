"""Pruebas del agente real SIN red: el parser de bloques de archivo, el rechazo
de rutas inseguras y el filtrado por paths permitidos. La llamada al modelo no
se ejercita aqui (eso requiere API key); esto verifica toda la maquinaria que
rodea a esa llamada.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))
from sdd.runtime import agent as real_agent


class TestComposeSystemPrompt(unittest.TestCase):
    def test_override_addon_y_herramientas_forman_el_prompt_efectivo(self):
        profile = {
            "system_prompt": "PROMPT IMPORTADO",
            "prompt_addon": "Prioriza G7.",
            "tools": ["gates", "", "tests"],
        }
        with mock.patch.object(Path, "read_text", return_value="PROMPT NATIVO"):
            prompt = real_agent.compose_system_prompt({"prompt": "base.md"}, profile)
        self.assertIn("PROMPT IMPORTADO", prompt)
        self.assertNotIn("PROMPT NATIVO", prompt)
        self.assertIn("Prioriza G7.", prompt)
        self.assertIn("gates, tests", prompt)


class TestParseFiles(unittest.TestCase):
    def test_extrae_multiples_bloques(self):
        text = ("<<<FILE: src/api/x.py>>>\nprint(1)\n<<<END>>>\n"
                "<<<FILE: .env.example>>>\nPAYMENT_API_URL=https://ex.test\n<<<END>>>")
        files = real_agent.parse_files(text)
        self.assertEqual([p for p, _ in files], ["src/api/x.py", ".env.example"])
        self.assertIn("print(1)", files[0][1])

    def test_tolera_fences_de_markdown(self):
        text = "```\n<<<FILE: a.txt>>>\nhola\n<<<END>>>\n```"
        files = real_agent.parse_files(text)
        self.assertEqual(files, [("a.txt", "hola")])

    def test_sin_bloques_devuelve_vacio(self):
        self.assertEqual(real_agent.parse_files("no hay archivos aqui"), [])


class TestSafeTarget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ruta_normal_ok(self):
        self.assertIsNotNone(real_agent._safe_target(self.wd, "src/api/x.py"))

    def test_traversal_rechazado(self):
        self.assertIsNone(real_agent._safe_target(self.wd, "../../etc/passwd"))

    def test_ruta_absoluta_rechazada(self):
        self.assertIsNone(real_agent._safe_target(self.wd, "/etc/passwd"))
        self.assertIsNone(real_agent._safe_target(self.wd, "C:/Windows/x"))


class TestWriteFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_escribe_dentro_de_paths_y_omite_fuera(self):
        files = [("src/api/x.py", "print(1)"),
                 ("tests/sneaky.py", "# el dev no puede escribir aqui")]
        written, skipped = real_agent.write_files(self.wd, ["src/api/"], files)
        self.assertEqual(written, ["src/api/x.py"])
        self.assertEqual(len(skipped), 1)
        self.assertTrue((self.wd / "src/api/x.py").exists())
        self.assertFalse((self.wd / "tests/sneaky.py").exists())

    def test_omite_traversal(self):
        written, skipped = real_agent.write_files(
            self.wd, ["src/"], [("../escape.py", "x")])
        self.assertEqual(written, [])
        self.assertEqual(skipped[0][1], "ruta insegura")


class TestGatherContext(unittest.TestCase):
    """El agente debe LEER el codigo fuente que va a integrar, no solo la spec.

    Regresion de la corrida real: QA importaba nombres de clase inventados porque
    gather_specs descartaba los .py; la suite ni arrancaba. Ahora QA ve src/ y los
    nodos de codigo ven los modulos que su tarea declara en `context`.
    """
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def w(self, rel, body):
        p = self.wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_qa_ve_el_codigo_fuente_real(self):
        self.w("src/domain/store.py", "class MemoryStore:\n    pass\n")
        ctx = real_agent.gather_specs(self.wd, "qa", [])
        self.assertIn("MemoryStore", ctx, "QA debe ver el simbolo real, no adivinarlo")
        self.assertIn("src/domain/store.py", ctx)

    def test_context_de_tarea_incluye_modulo_consumido(self):
        self.w("src/domain/service.py", "def crear_enlace():\n    return 1\n")
        # extra_globs simula el `context` de la tarea (lo pasa gather_task).
        ctx = real_agent.gather_specs(self.wd, "dev_backend", ["src/domain/service.py"])
        self.assertIn("crear_enlace", ctx)


if __name__ == "__main__":
    unittest.main()

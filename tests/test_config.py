"""Pruebas de la configuracion persistente (config.py), aisladas en un tmpdir."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))
import config  # noqa: E402


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root, self._cp = config.ROOT, config.CONFIG_PATH
        config.ROOT = Path(self._tmp.name)
        config.CONFIG_PATH = config.ROOT / "config.json"

    def tearDown(self):
        config.ROOT, config.CONFIG_PATH = self._root, self._cp
        self._tmp.cleanup()


class TestLoadSave(ConfigCase):
    def test_defaults_sin_archivo(self):
        c = config.load()
        self.assertEqual(c["output_base"], "project")
        self.assertEqual(c["keys"], {})

    def test_guardar_y_recargar(self):
        config.save({"provider": "deepseek", "theme": "dark",
                     "keys": {"deepseek": "sk-123"}})
        c = config.load()
        self.assertEqual(c["provider"], "deepseek")
        self.assertEqual(c["theme"], "dark")
        self.assertEqual(c["keys"]["deepseek"], "sk-123")

    def test_key_vacia_no_borra_la_guardada(self):
        config.save({"keys": {"anthropic": "sk-real"}})
        config.save({"provider": "anthropic", "keys": {"anthropic": ""}})
        self.assertEqual(config.load()["keys"]["anthropic"], "sk-real")

    def test_multiples_llaves_por_proveedor(self):
        config.save({"keys": {"anthropic": "a"}})
        config.save({"keys": {"deepseek": "d"}})
        self.assertEqual(config.load()["keys"], {"anthropic": "a", "deepseek": "d"})


class TestResolveOutput(ConfigCase):
    def test_default_va_a_project_nombre(self):
        p = config.resolve_output("mi-portal")
        self.assertEqual(p, (config.ROOT / "project" / "mi-portal").resolve())

    def test_nombre_se_sanea(self):
        p = config.resolve_output("Mi Portal!! /../x")
        self.assertNotIn("..", p.as_posix())
        self.assertTrue(p.name and p.name not in (".", ".."))

    def test_base_absoluta_se_respeta(self):
        base = self._tmp.name
        p = config.resolve_output("x", output_base=base)
        self.assertEqual(p, (Path(base) / "x").resolve())

    def test_slug(self):
        self.assertEqual(config.slug("Hola Mundo"), "Hola-Mundo")
        self.assertEqual(config.slug("  "), "sin-nombre")


class TestMasked(ConfigCase):
    def test_enmascara_llaves(self):
        config.save({"keys": {"anthropic": "sk-ant-1234567890abcd"}})
        m = config.masked()
        self.assertNotEqual(m["keys"]["anthropic"], "sk-ant-1234567890abcd")
        self.assertIn("…", m["keys"]["anthropic"])


if __name__ == "__main__":
    unittest.main()

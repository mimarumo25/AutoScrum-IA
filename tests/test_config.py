"""Pruebas de la configuracion persistente (config.py), aisladas en un tmpdir."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))
from sdd.core import config


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root, self._cp = config.ROOT, config.CONFIG_PATH
        config.ROOT = Path(self._tmp.name)
        config.CONFIG_PATH = config.ROOT / "config.json"

    def tearDown(self):
        config.ROOT, config.CONFIG_PATH = self._root, self._cp
        self._tmp.cleanup()


class TestCredenciales(ConfigCase):
    """Una key en claro en disco es una copia del secreto que nadie pidio."""

    def test_no_duplica_el_secreto_si_la_variable_de_entorno_esta_definida(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-del-entorno"}):
            config.save({"keys": {"deepseek": "sk-desde-la-ui"}})
        self.assertEqual(config.load()["keys"], {},
                         "con la variable definida no hace falta guardarla en disco")

    def test_sigue_guardando_para_quien_no_usa_variables(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("DEEPSEEK_API_KEY", None)
            config.save({"keys": {"deepseek": "sk-desde-la-ui"}})
        self.assertEqual(config.load()["keys"]["deepseek"], "sk-desde-la-ui")

    def test_reporta_los_proveedores_en_claro_sin_revelar_la_key(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        config.save({"keys": {"deepseek": "sk-secreta"}})
        reporte = config.plaintext_key_providers()
        self.assertEqual(reporte, ["deepseek"])
        self.assertNotIn("sk-secreta", str(reporte))

    def test_masked_nunca_devuelve_material_secreto(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        config.save({"keys": {"deepseek": "sk-secreta"}})
        visible = config.masked()
        self.assertEqual(visible["keys"], {})
        self.assertTrue(visible["key_status"]["deepseek"])
        self.assertNotIn("sk-secreta", json.dumps(visible))

    def test_el_nombre_de_variable_sale_del_router_no_de_una_copia(self):
        self.assertEqual(config.key_env_name("anthropic"), "ANTHROPIC_API_KEY")
        self.assertEqual(config.key_env_name("deepseek"), "DEEPSEEK_API_KEY")


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

    def test_list_tasks_omite_directorios_internos(self):
        project = config.ROOT / "project" / "demo"
        (project / ".sdd-locks").mkdir(parents=True)
        (project / "tarea-visible").mkdir()
        tasks = config.list_tasks("demo")
        self.assertEqual([item["task"] for item in tasks], ["tarea-visible"])


class TestMasked(ConfigCase):
    def test_no_expone_llaves(self):
        config.save({"keys": {"anthropic": "sk-ant-1234567890abcd"}})
        m = config.masked()
        self.assertEqual(m["keys"], {})
        self.assertTrue(m["key_status"]["anthropic"])


if __name__ == "__main__":
    unittest.main()

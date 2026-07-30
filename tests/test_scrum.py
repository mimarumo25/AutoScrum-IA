"""El scrum solo ordena; seguridad y dependencias siguen deterministas."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd.runtime import scrum


def ready():
    return [
        {"id": "T-2", "node": "dev_frontend", "kind": "plan",
         "fr_refs": ["FR-002"]},
        {"id": "T-1", "node": "dev_backend", "kind": "plan",
         "fr_refs": ["FR-001"]},
        {"id": "D-001", "node": "dev_backend", "kind": "defect",
         "fr_refs": ["FR-001"]},
    ]


class TestPrioritize(unittest.TestCase):
    def test_no_llama_modelo_si_caben_todas(self):
        def boom(_system, _user):
            raise AssertionError("modelo innecesario")
        result = scrum.prioritize(ready(), critical_frs=set(), slots=6,
                                  simulate=False, complete_fn=boom)
        self.assertEqual([task["id"] for task in result], ["D-001", "T-1", "T-2"])

    def test_defecto_y_critico_primero(self):
        result = scrum.prioritize(ready(), critical_frs={"FR-002"}, slots=1)
        self.assertEqual([task["id"] for task in result], ["D-001", "T-2", "T-1"])

    def test_fallback_si_modelo_devuelve_orden_invalido(self):
        result = scrum.prioritize(
            ready(), critical_frs=set(), slots=1, simulate=False,
            complete_fn=lambda _s, _u: '<<<ORDER>>>["T-9"]<<<END>>>')
        self.assertEqual([task["id"] for task in result], ["D-001", "T-1", "T-2"])

    def test_respeta_permutacion_valida(self):
        result = scrum.prioritize(
            ready(), critical_frs=set(), slots=1, simulate=False,
            complete_fn=lambda _s, _u: '<<<ORDER>>>["T-1","D-001","T-2"]<<<END>>>')
        self.assertEqual([task["id"] for task in result], ["T-1", "D-001", "T-2"])

    def test_lee_frs_criticos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "spec/10_product/features"
            root.mkdir(parents=True)
            (root / "x.feature").write_text(
                "@FR-001 @SCN-001 @critical\nEscenario: x\n"
                "@FR-002 @SCN-002\nEscenario: y\n", encoding="utf-8")
            self.assertEqual(scrum.read_critical_frs(tmp), {"FR-001"})


if __name__ == "__main__":
    unittest.main()

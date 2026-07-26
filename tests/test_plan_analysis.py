"""Analisis de camino critico sin mutar el plan firmado."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

import plan_analysis  # noqa: E402


class TestPlanAnalysis(unittest.TestCase):
    def test_camino_critico_ancho_y_descendientes(self):
        tasks = [
            {"id": "T-1", "depends_on": [], "deliverables": ["src/a.py"], "context": []},
            {"id": "T-2", "depends_on": ["T-1"], "deliverables": ["src/b.py"],
             "context": ["src/a.py"]},
            {"id": "T-3", "depends_on": ["T-1"], "deliverables": ["src/c.py"],
             "context": ["src/a.py"]},
        ]
        result = plan_analysis.analyze(tasks)
        self.assertEqual(result["critical_path"], 2)
        self.assertEqual(result["max_ready_wave"], 2)
        self.assertEqual(result["descendants"]["T-1"], 2)
        self.assertEqual(result["advisories"], [])

    def test_marca_dependencia_sin_respaldo_en_context(self):
        tasks = [
            {"id": "T-1", "depends_on": [], "deliverables": ["src/api/a.py"]},
            {"id": "T-2", "depends_on": ["T-1"], "deliverables": ["src/web/b.js"],
             "context": ["spec/openapi.yaml"]},
        ]
        advisory = plan_analysis.analyze(tasks)["advisories"][0]
        self.assertEqual((advisory["task"], advisory["dependency"]), ("T-2", "T-1"))


if __name__ == "__main__":
    unittest.main()

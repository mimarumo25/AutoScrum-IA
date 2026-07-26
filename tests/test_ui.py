"""Pruebas de la capa de presentacion: report.py, server.py y cli.py.

Eran 1.295 lineas sin una sola prueba, incluido todo el panel web. No se puede
levantar un navegador aqui, asi que se prueba lo que SI es determinista: que el
HTML se genera con los datos correctos, que los ayudantes del servidor arman el
payload esperado, y que el CLI segmenta el historial y parsea sus subcomandos.

    python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import report  # noqa: E402
import server  # noqa: E402


def _state(**over):
    st = {"run_id": "R1", "status": "done", "agent_calls": 5, "attempts": {},
          "started_at": 0, "tasks": [], "history": []}
    st.update(over)
    return st


class TestReport(unittest.TestCase):
    def test_build_steps_segmenta_por_visita_y_marca_commit(self):
        history = [
            {"event": "AGENTE", "nodo": "product", "tarea": "-"},
            {"event": "GATE G1", "estado": "pass"},
            {"event": "APROBADO", "nodo": "product", "accion": "commit"},
            {"event": "AGENTE", "nodo": "architect", "tarea": "-"},
            {"event": "APROBADO", "nodo": "architect", "accion": "sin-commit"},
        ]
        steps = report.build_steps(history)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["commit"], 1)
        self.assertEqual(steps[1]["commit"], 0, "sin-commit no cuenta como commit")

    def test_verdict_distingue_incompleto_de_completado(self):
        completo = _state(tasks=[{"status": "done"}, {"status": "done"}])
        self.assertEqual(report._verdict(completo), "COMPLETADO")
        parcial = _state(tasks=[{"status": "done"}, {"status": "pending"}])
        self.assertIn("INCOMPLETO", report._verdict(parcial))

    def test_render_html_incluye_estado_y_es_responsivo(self):
        html = report.render_html(_state(history=[
            {"event": "AGENTE", "nodo": "product", "tarea": "-"}]),
            ["product", "architect"], Path("/x"))
        self.assertIn("done", html)
        # El rail dejo de ser 7 columnas fijas (habia 8 nodos): ahora es responsivo.
        self.assertIn("auto-fit", html)
        self.assertNotIn("repeat(7,1fr)", html)

    def test_write_run_report_crea_REPORT_md_con_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".agent").mkdir()
            def fake_git(_wd, *a):
                class R: stdout = ""
                return R()
            report.write_run_report(_state(tasks=[{"id": "T-001", "node": "dev_backend",
                                    "title": "x", "status": "done"}]),
                                    str(wd), "mi tarea", fake_git)
            texto = (wd / ".agent/REPORT.md").read_text(encoding="utf-8")
            self.assertIn("Tokens", texto)
            self.assertIn("modo simulado", texto)  # sin usage.jsonl

    def test_token_usage_suma_el_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".agent").mkdir()
            (wd / ".agent/usage.jsonl").write_text(
                '{"input_tokens": 10, "output_tokens": 3, "calls": 1}\n'
                '{"input_tokens": 5, "output_tokens": 2, "calls": 1}\n', encoding="utf-8")
            u = report._token_usage(wd)
            self.assertEqual((u["input_tokens"], u["output_tokens"], u["calls"]), (15, 5, 2))

    def test_performance_section_resume_metricas(self):
        with tempfile.TemporaryDirectory() as tmp:
            import metrics
            metrics.record(tmp, "gate_process", duration_ms=20)
            metrics.record(tmp, "gate_process", duration_ms=10)
            text = "\n".join(report._performance_section(tmp))
            self.assertIn("gate_process", text)
            self.assertIn("30 ms total", text)


class TestServer(unittest.TestCase):
    def test_page_incluye_pestanas_y_sprint(self):
        self.assertIn("data-tab=tasks", server.PAGE)
        self.assertIn("paintSprint", server.PAGE, "el panel debe renderizar el sprint")

    def test_sprint_from_extrae_tareas_del_estado(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({"tasks": [
                {"id": "T-001", "node": "dev_backend", "title": "x", "status": "done"},
                {"id": "T-002", "node": "qa", "status": "blocked", "blocked_by": "D-001"}]}),
                encoding="utf-8")
            sprint = server._sprint_from(sp)
            self.assertEqual(len(sprint), 2)
            self.assertEqual(sprint[1]["blocked_by"], "D-001")

    def test_sprint_from_tolera_estado_ilegible(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text("{ no es json", encoding="utf-8")
            self.assertEqual(server._sprint_from(sp), [])


class TestCli(unittest.TestCase):
    def test_visits_segmenta_incluyendo_la_tarea(self):
        history = [
            {"event": "AGENTE", "nodo": "dev_backend", "tarea": "T-001"},
            {"event": "GATE G0", "estado": "pass"},
            {"event": "AGENTE", "nodo": "dev_backend", "tarea": "T-002"},
        ]
        visits = cli._visits(history)
        self.assertEqual([v["tarea"] for v in visits], ["T-001", "T-002"])

    def test_build_parser_reconoce_subcomandos(self):
        parser = cli.build_parser()
        extra = {"gates": ["--node", "x", "--workdir", "y"],
                 "resume": ["--node", "x", "--workdir", "y"]}
        for cmd in ("demo", "run", "gates", "resume", "show", "view", "web", "test"):
            a = parser.parse_args([cmd] + extra.get(cmd, []))
            self.assertEqual(a.cmd, cmd)

    def test_print_review_backlog_muestra_mejoras(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / ".agent/review").mkdir(parents=True)
            (wd / ".agent/review/architect.json").write_text(json.dumps(
                {"mejoras": [{"rule": "nfr-unico", "evidence": "falta NFR de disponibilidad",
                              "file": "spec/20_arch/nfr.yaml", "line": 0}], "nota": ""}),
                encoding="utf-8")
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli._print_review_backlog(wd)
            self.assertIn("nfr-unico", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Pruebas de la migracion durable a LangGraph."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from execution_journal import invoke_once  # noqa: E402
from graph_runtime import _delta, _merge_results  # noqa: E402
import cli  # noqa: E402
import metrics  # noqa: E402
import parallel_tasks  # noqa: E402
import task_worktrees  # noqa: E402

PY = sys.executable


def git_repo(path: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.email", "test@sdd.local"],
                 ["config", "user.name", "sdd-test"]):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True)
    (path / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    return path


class TestExecutionJournal(unittest.TestCase):
    def test_reutiliza_una_visita_completada(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def operation():
                calls.append("call")
                return 0, "ok"

            first = invoke_once(tmp, "visit-1", operation)
            second = invoke_once(tmp, "visit-1", operation)

            self.assertEqual(first, (0, "ok"))
            self.assertEqual(second, first)
            self.assertEqual(calls, ["call"], "la llamada externa no debe duplicarse")


class TestCompactState(unittest.TestCase):
    def test_delta_solo_devuelve_canales_modificados(self):
        before = {"status": "running", "history": [{"event": "A"}],
                  "tasks": [], "parallel_results": {}}
        after = deepcopy(before)
        after["status"] = "done"
        self.assertEqual(_delta(before, after), {"status": "done"})

    def test_resultados_paralelos_se_pueden_liberar(self):
        merged = _merge_results({"B:T-1": {"ok": True}},
                                {"__sdd_reset__": True})
        self.assertEqual(merged, {})

    def test_metricas_se_resumen_por_operacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics.record(tmp, "gate", duration_ms=2.5)
            metrics.record(tmp, "gate", duration_ms=3.5)
            self.assertEqual(metrics.summarize(tmp)["gate"],
                             {"count": 2, "duration_ms": 6.0})

    def test_transfiere_telemetria_de_un_worker(self):
        with tempfile.TemporaryDirectory() as source, \
                tempfile.TemporaryDirectory() as destination:
            metrics.record(source, "agent_process", duration_ms=8)
            metrics.record_usage(source, input_tokens=11, output_tokens=3)
            metrics.transfer(source, destination)
            self.assertEqual(
                metrics.summarize(destination)["agent_process"]["count"], 1)
            usage = (Path(destination) / metrics.USAGE_PATH).read_text(
                encoding="utf-8")
            self.assertIn('"input_tokens":11', usage)


class TestSafeBatch(unittest.TestCase):
    def test_solo_agrupa_huellas_no_superpuestas(self):
        tasks = [
            {"id": "T-1", "node": "a", "kind": "plan",
             "deliverables": ["src/api/a.py"]},
            {"id": "T-2", "node": "b", "kind": "plan",
             "deliverables": ["src/web/a.js"]},
            {"id": "T-3", "node": "c", "kind": "plan",
             "deliverables": ["src/api/a.py"]},
        ]
        selected = task_worktrees.safe_batch(tasks, {}, 3)
        self.assertEqual([task["id"] for task in selected], ["T-1", "T-2"])

    def test_incluye_defectos_disjuntos(self):
        tasks = [
            {"id": "D-1", "kind": "defect", "deliverables": ["src/a.py"]},
            {"id": "D-2", "kind": "defect", "deliverables": ["src/b.py"]},
            {"id": "T-3", "kind": "plan", "deliverables": ["src/a.py"]},
        ]
        selected = task_worktrees.safe_batch(tasks, {}, 6)
        self.assertEqual([task["id"] for task in selected], ["D-1", "D-2"])

    def test_ola_ancha_incluye_todas_las_huellas_disjuntas(self):
        tasks = [{"id": f"T-{index}", "kind": "plan",
                  "deliverables": [f"src/{index}.py"]} for index in range(5)]
        self.assertEqual(len(task_worktrees.safe_batch(tasks, {}, 6)), 5)


class TestParallelDefectBudget(unittest.TestCase):
    def test_colector_respeta_tope_global_de_defectos(self):
        events = []
        runner = parallel_tasks.ParallelTasks(
            ".", None, {"budget": {"max_defect_tasks": 2}}, {}, False,
            None, lambda _state, event, **fields: events.append((event, fields)),
            None, lambda value: value, None)
        task = {"id": "T-1", "node": "qa", "status": "pending",
                "workspace": {"path": "unused"}}
        state = {"tasks": [task], "defect_seq": 2, "agent_calls": 0,
                 "history": [], "attempts": {}, "status": "running"}
        result = {"task_id": "T-1", "task": task, "outcome": "blocked",
                  "defect": {"node": "dev_backend", "gate_id": "G9",
                              "findings": []},
                  "agent_calls": 0, "history": [], "attempts": {}}

        with patch.object(task_worktrees, "cleanup") as cleanup:
            keep_running = runner._collect_one(state, result)

        self.assertFalse(keep_running)
        self.assertEqual(state["status"], "escalated")
        self.assertIn("tope de tareas de defecto alcanzado", events[-1][1]["motivo"])
        cleanup.assert_called_once_with(".", task)


class TestWorktreeRecovery(unittest.TestCase):
    def test_prepare_reconstruye_worktree_si_falto_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            task = {"id": "T-1", "node": "dev"}
            first = task_worktrees.prepare(str(wd), "run-1", task)
            reconstructed = task_worktrees.prepare(
                str(wd), "run-1", {"id": "T-1", "node": "dev"})
            self.assertEqual(reconstructed["path"], first["path"])
            task["workspace"] = first
            task_worktrees.cleanup(str(wd), task)

    def test_integracion_completada_no_duplica_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            task = {"id": "T-1", "node": "dev"}
            task["workspace"] = task_worktrees.prepare(str(wd), "run-1", task)
            worktree = Path(task["workspace"]["path"])
            (worktree / "src").mkdir()
            (worktree / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(worktree), "add", "src/a.py"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "worker"],
                           check=True, capture_output=True)

            def commit_fn(repo, message, _allowed):
                proc = subprocess.run(["git", "-C", str(repo), "commit", "-m", message],
                                      capture_output=True, text=True)
                return proc.returncode == 0, message if proc.returncode == 0 else proc.stderr

            saved = deepcopy(task)
            first = task_worktrees.integrate(
                str(wd), task, ["src/"], "feat(dev): T-1", commit_fn)
            self.assertEqual(first[0], "committed")
            task_worktrees.cleanup(str(wd), task)
            second = task_worktrees.integrate(
                str(wd), saved, ["src/"], "feat(dev): T-1", commit_fn)
            self.assertEqual(second, first)
            count = subprocess.run(
                ["git", "-C", str(wd), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(count, "2")

    def test_clean_retira_worktrees_antes_de_borrar_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            task = {"id": "T-1", "node": "dev"}
            task["workspace"] = task_worktrees.prepare(str(wd), "run-1", task)
            state_path = wd / ".agent/state.json"
            state_path.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            self.assertEqual(cli.clean(SimpleNamespace(workdir=str(wd))), 0)
            self.assertFalse((wd / ".agent").exists())
            listing = subprocess.run(
                ["git", "-C", str(wd), "worktree", "list", "--porcelain"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(listing.count("worktree "), 1)
            branches = subprocess.run(
                ["git", "-C", str(wd), "branch", "--list", "sdd/*"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(branches.strip(), "")


class TestDurableHumanGate(unittest.TestCase):
    def test_checkpoint_interrupt_firma_y_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            base = [PY, str(ROOT / "orchestrator.py"),
                    "--workdir", str(wd), "--simulate"]

            stopped = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            state_path = wd / ".agent/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "waiting_human")
            self.assertEqual(state["engine"], "langgraph")
            self.assertEqual(state["cursor"], "human_gate")
            self.assertFalse((wd / "src").exists())

            checkpoint = wd / state["checkpoint_db"]
            self.assertTrue(checkpoint.exists())
            connection = sqlite3.connect(checkpoint)
            try:
                count = connection.execute(
                    "select count(*) from checkpoints").fetchone()[0]
            finally:
                connection.close()
            self.assertGreater(count, 0)

            resumed = subprocess.run(base + ["--resume"], capture_output=True, text=True)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "done")
            self.assertTrue(state["human_approval"]["approved"])
            self.assertEqual(state["human_approval"]["actor"], "cli")
            self.assertEqual(len(state["human_approval"]["spec_hash"]), 64)
            self.assertTrue(any(
                event.get("event") == "APROBADO"
                and event.get("nodo") == "human_gate"
                for event in state["history"]))

    def test_send_workers_ejecutan_batch_paralelo_y_limpian_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            proc = subprocess.run(
                [PY, str(ROOT / "orchestrator.py"), "--workdir", str(wd),
                 "--simulate", "--autonomous"],
                capture_output=True, text=True,
                env={**os.environ, "SDD_FAKE_PARALLEL": "1"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            state = json.loads((wd / ".agent/state.json").read_text(encoding="utf-8"))
            batches = [event for event in state["history"]
                       if event.get("event") == "BATCH"]
            self.assertTrue(any(event.get("tareas", 0) >= 3 for event in batches))
            self.assertEqual(state["status"], "done")
            self.assertTrue(all(task["status"] == "done" for task in state["tasks"]))
            self.assertEqual(state.get("parallel_results"), {})
            summary = metrics.summarize(wd)
            self.assertGreater(summary.get("state_projection", {}).get("count", 0), 0)
            self.assertGreater(summary.get("agent_process", {}).get("count", 0), 5)
            worktrees = subprocess.run(
                ["git", "-C", str(wd), "worktree", "list", "--porcelain"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(worktrees.count("worktree "), 1)
            branches = subprocess.run(
                ["git", "-C", str(wd), "branch", "--list", "sdd/*"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(branches.strip(), "")


if __name__ == "__main__":
    unittest.main()

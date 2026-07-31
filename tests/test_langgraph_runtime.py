"""Pruebas de la migracion durable a LangGraph."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd.core.execution_journal import invoke_once
from sdd.presentation import cli
from sdd.runtime import agent, graph_runtime
from sdd.runtime.graph_runtime import _delta, _merge_results
from sdd.core import metrics
from sdd.runtime import (orchestrator, parallel_tasks, task_worktrees,
                         work_unit_graph, workflow_defects)
from sdd.runtime.artifact_integrity import content_hash

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


class TestIntegratedEvaluationContract(unittest.TestCase):
    def test_rechazo_tipado_exige_feedback(self):
        with self.assertRaises(ValueError):
            orchestrator.Evaluation(
                unit_id="product:linear", node="product", approved=False)

    def test_decision_humana_reject_exige_feedback(self):
        with self.assertRaises(ValueError):
            orchestrator.HumanDecision(action="reject")

    def test_revision_no_disponible_escala_sin_reintentar_productor(self):
        state = {"status": "running", "agent_calls": 1, "attempts": {},
                 "history": []}
        finding = {"file": "agents/product.md", "line": 0,
                   "rule": "revision-no-disponible",
                   "evidence": "R1 sin credenciales"}

        budget = {"max_retries_per_gate": 2}
        emit = lambda current, event, **fields: current["history"].append(
            {"event": event, **fields})
        decision = workflow_defects.classify_defect(
            state, {"id": "product"}, None, "product", "R1", [finding], budget)
        workflow_defects.escalate_defect(
            state, ".", {"id": "product"}, None, decision, budget, emit)

        self.assertEqual(state["status"], "escalated")
        self.assertEqual(state["agent_calls"], 1)
        self.assertEqual(state["attempts"], {})

    def test_checkpoints_usan_durabilidad_sincrona(self):
        source = Path(graph_runtime.__file__).read_text(encoding="utf-8")
        self.assertIn('durability="sync"', source)


class TestParallelWorkerBudget(unittest.TestCase):
    def runner(self, generate=None):
        return parallel_tasks.ParallelTasks(
            workdir=".", args=SimpleNamespace(),
            cfg={"budget": {"max_agent_calls": 5, "max_retries_per_gate": 2}},
            nodes={"dev": {"id": "dev", "writes": []}}, auto_human=False,
            generate_fn=generate or Mock(), evaluate_fn=Mock(), log_fn=Mock(),
            commit_fn=Mock(), normalize_fn=lambda value: value, project_fn=Mock())

    def work_unit(self, generate=None):
        return work_unit_graph.WorkUnitGraph(
            ".", SimpleNamespace(),
            {"budget": {"max_agent_calls": 5, "max_retries_per_gate": 2,
                         "max_wall_minutes": 180, "max_output_tokens": 0}},
            {"dev": {"id": "dev", "writes": []}}, False,
            generate or Mock(), Mock(), Mock(), lambda value: value,
            lambda _workdir: {"output_tokens": 0})

    def test_reparte_deterministicamente_si_remaining_es_menor_que_siblings(self):
        sends = self.runner().dispatch({
            "agent_calls": 4,
            "parallel_batch": {"id": "B-1", "task_ids": ["T-1", "T-2", "T-3"]},
        })
        quotas = [send.arg["parallel_batch"]["agent_quota"] for send in sends]
        self.assertEqual(quotas, [1, 0, 0])
        self.assertLessEqual(sum(quotas), 1)

    def test_remaining_cero_asigna_ceros_y_no_invoca_productor(self):
        generate = Mock()
        runner = self.runner(generate)
        sends = runner.dispatch({
            "agent_calls": 5,
            "parallel_batch": {"id": "B-1", "task_ids": ["T-1", "T-2"]},
        })
        self.assertEqual(
            [send.arg["parallel_batch"]["agent_quota"] for send in sends], [0, 0])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(task_worktrees, "preserve"), \
                patch.object(work_unit_graph.taskqueue, "publish_current"), \
                patch.object(work_unit_graph.lifecycle, "started"), \
                patch.object(work_unit_graph.metrics, "transfer"), \
                patch.object(work_unit_graph.chronicle, "transfer"):
            value = sends[0].arg
            value.update(tasks=[{"id": "T-1", "node": "dev", "status": "pending",
                                 "workspace": {"path": tmp}}], history=[], attempts={},
                         status="running")
            worker = self.work_unit(generate)
            prepared = worker.prepare(value)
            generated = {**prepared, **worker.generate(prepared)}
            result = worker.finalize(generated)
        generate.assert_not_called()
        self.assertEqual(result["parallel_results"]["B-1:T-1"]["agent_calls"], 0)

    def test_worker_conserva_feedback_y_retry_h1(self):
        observed = {}

        def generate(local, *_args):
            observed.update(feedback=local["feedback"], retry=local["retry_count"])
            local["status"] = "escalated"

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(task_worktrees, "preserve"), \
                patch.object(work_unit_graph.taskqueue, "publish_current"), \
                patch.object(work_unit_graph.lifecycle, "started"):
            worker = self.work_unit(generate)
            prepared = worker.prepare({
                "worker_task_id": "T-1", "agent_calls": 4,
                "parallel_batch": {"id": "B-1", "agent_quota": 1},
                "tasks": [{"id": "T-1", "node": "dev", "status": "pending",
                           "workspace": {"path": tmp},
                           "evaluation_feedback": "agrega el caso limite"}],
                "history": [], "attempts": {"T-1:H1": 1}, "status": "running",
            })
            worker.generate(prepared)
        self.assertEqual(observed, {"feedback": "agrega el caso limite", "retry": 1})

    def test_budget_check_se_repite_antes_de_retry_y_respeta_cuota_exacta(self):
        calls = []

        def generate(current, *_args):
            calls.append("generate")
            current["agent_calls"] += 1

        with tempfile.TemporaryDirectory() as tmp:
            worker = self.work_unit(generate)
            state = {
                "tasks": [{"id": "T-1", "node": "dev",
                           "workspace": {"path": tmp}}],
                "parallel_batch": {"id": "B-1", "agent_quota": 2},
                "agent_calls": 3, "started_at": time.time(),
                "history": [], "attempts": {}, "status": "running",
                "work_unit_started": True, "work_unit_error": "",
            }
            for _ in range(2):
                checked = worker.budget_check(state)
                self.assertFalse(checked.get("work_unit_error"))
                state = worker.generate(checked)
            exhausted = worker.budget_check(state)
        self.assertEqual(calls, ["generate", "generate"])
        self.assertEqual(exhausted["work_unit_error"], "agent quota agotada")

    def test_budget_check_bloquea_en_limite_exacto_de_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = work_unit_graph.WorkUnitGraph(
                ".", SimpleNamespace(),
                {"budget": {"max_agent_calls": 5, "max_retries_per_gate": 2,
                             "max_wall_minutes": 180, "max_output_tokens": 10}},
                {"dev": {"id": "dev", "writes": []}}, False,
                Mock(), Mock(), Mock(), lambda value: value,
                lambda _workdir: {"output_tokens": 10})
            state = {
                "tasks": [{"id": "T-1", "node": "dev",
                           "workspace": {"path": tmp}}],
                "parallel_batch": {"agent_quota": 1}, "agent_calls": 4,
                "started_at": time.time(), "history": [], "attempts": {},
                "status": "running", "work_unit_error": "",
            }
            checked = worker.budget_check(state)
        self.assertIn("10 >= 10", checked["work_unit_error"])

    def test_budget_check_bloquea_en_limite_exacto_de_tiempo(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self.work_unit()
            state = {
                "tasks": [{"id": "T-1", "node": "dev",
                           "workspace": {"path": tmp}}],
                "parallel_batch": {"agent_quota": 1}, "agent_calls": 4,
                "started_at": 100.0, "history": [], "attempts": {},
                "status": "running", "work_unit_error": "",
            }
            with patch.object(work_unit_graph.time, "time", return_value=10900.0):
                checked = worker.budget_check(state)
        self.assertEqual(checked["work_unit_error"], "max_wall_minutes agotado")


class TestHumanFeedbackPrompt(unittest.TestCase):
    def test_gather_task_incluye_feedback_y_hallazgo_h1(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp) / ".agent/current_task.json"
            current.parent.mkdir()
            current.write_text(json.dumps({
                "id": "T-1", "title": "API", "acceptance": "verde",
                "evaluation_feedback": "agrega un caso limite",
                "findings": [{"file": "src/api.py", "line": 8,
                              "rule": "rechazo-humano",
                              "evidence": "agrega un caso limite"}],
            }), encoding="utf-8")
            prompt, _ = agent.gather_task(Path(tmp))
        self.assertIn("FEEDBACK DE LA REVISION HUMANA", prompt)
        self.assertIn("agrega un caso limite", prompt)
        self.assertIn("src/api.py:8 rechazo-humano", prompt)


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

    def test_writes_compartidos_serializan_entregables_disjuntos(self):
        tasks = [
            {"id": "T-1", "node": "dev", "deliverables": ["src/a.py"]},
            {"id": "T-2", "node": "dev", "deliverables": ["src/b.py"]},
        ]
        nodes = {"dev": {"writes": ["src/shared/"]}}
        selected = task_worktrees.safe_batch(tasks, nodes, 2)
        self.assertEqual([task["id"] for task in selected], ["T-1"])


class TestParallelDefectBudget(unittest.TestCase):
    def test_colector_respeta_tope_global_de_defectos(self):
        events = []
        runner = parallel_tasks.ParallelTasks(
            workdir=".", args=None, cfg={"budget": {"max_defect_tasks": 2}},
            nodes={}, auto_human=False, generate_fn=None, evaluate_fn=None,
            log_fn=lambda _state, event, **fields: events.append((event, fields)),
            commit_fn=None, normalize_fn=lambda value: value, project_fn=None)
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

        self.assertTrue(keep_running)
        self.assertEqual(state["status"], "running")
        self.assertEqual(task["status"], "needs_input")
        self.assertEqual(events[-1][0], "RAMA_EN_ESPERA")
        self.assertIn("tope de 2 correcciones", events[-1][1]["motivo"])
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
    def test_crash_de_proyeccion_no_reemplaza_checkpoint_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            state_path = wd / ".agent/state.json"
            args = SimpleNamespace(workdir=str(wd), human_decision=None,
                                   human_feedback="", simulate=True)
            cfg = {
                "budget": {"max_agent_calls": 5, "max_retries_per_gate": 1,
                           "max_wall_minutes": 10, "max_output_tokens": 0},
                "runtime": {"max_concurrency": 1},
            }
            nodes = {"human_gate": {"id": "human_gate", "type": "human",
                                    "next": "done", "writes": []}}
            durable = {
                "run_id": "projection-crash", "cursor": "human_gate",
                "status": "escalated", "started_at": time.time(),
                "agent_calls": 0, "tasks": [], "history": [], "attempts": {},
            }
            with self.assertRaisesRegex(OSError, "projection crash"):
                graph_runtime.run_pipeline(
                    durable, state_path, args, cfg, nodes, False, Mock(), Mock(),
                    Mock(), Mock(), lambda *_args: (_ for _ in ()).throw(
                        OSError("projection crash")),
                    lambda _wd: {"output_tokens": 0}, Mock())
            stale = {**durable, "status": "running"}
            recovered = graph_runtime.run_pipeline(
                stale, state_path, args, cfg, nodes, False, Mock(), Mock(), Mock(),
                Mock(), orchestrator.save, lambda _wd: {"output_tokens": 0}, Mock())
        self.assertEqual(recovered["status"], "escalated")

    def test_gate_legado_interrumpe_sin_autoaprobar(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            state_path = wd / ".agent/state.json"
            state = {
                "run_id": "legacy-run", "cursor": "human_gate",
                "status": "running", "started_at": time.time(),
                "agent_calls": 0, "tasks": [], "history": [], "attempts": {},
            }
            args = SimpleNamespace(workdir=str(wd), human_decision=None,
                                   human_feedback="", simulate=True)
            cfg = {
                "budget": {"max_agent_calls": 5, "max_retries_per_gate": 1,
                           "max_wall_minutes": 10, "max_output_tokens": 0},
                "runtime": {"max_concurrency": 1},
            }
            nodes = {
                "human_gate": {"id": "human_gate", "type": "human",
                               "next": "product", "writes": []},
                "product": {"id": "product", "type": "agent",
                            "next": "done", "writes": []},
            }
            result = graph_runtime.run_pipeline(
                state, state_path, args, cfg, nodes, False, Mock(), Mock(), Mock(),
                Mock(), orchestrator.save, lambda _wd: {"output_tokens": 0}, Mock())
        self.assertEqual(result["status"], "waiting_human")
        self.assertEqual(result["pending_review"]["kind"], "legacy")
        self.assertNotIn("human_approval", result)

    def test_cambio_lineal_durante_hitl_no_se_commitea(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            base = [PY, "-m", "sdd.runtime.orchestrator",
                    "--workdir", str(wd), "--simulate"]
            stopped = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            artifact = wd / "spec/10_product/prd.md"
            artifact.write_text(artifact.read_text(encoding="utf-8") + "\nmutado\n",
                                encoding="utf-8")
            resumed = subprocess.run(
                base + ["--resume", "--human-decision", "accept"],
                capture_output=True, text=True)
            state = json.loads((wd / ".agent/state.json").read_text(encoding="utf-8"))
            commits = subprocess.run(
                ["git", "-C", str(wd), "log", "--format=%s"],
                capture_output=True, text=True, check=True).stdout
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(state["status"], "waiting_human")
        self.assertNotIn("docs(product)", commits)
        self.assertIn("contenido-cambio-tras-evaluacion", resumed.stdout)

    def test_cambio_en_worktree_durante_hitl_no_se_integra(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            task = {"id": "T-1", "node": "dev", "status": "pending",
                    "deliverables": ["src/a.py"]}
            task["workspace"] = task_worktrees.prepare(str(wd), "run-1", task)
            worktree = Path(task["workspace"]["path"])
            (worktree / "src").mkdir()
            artifact = worktree / "src/a.py"
            artifact.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(worktree), "add", "src/a.py"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "worker"],
                           check=True, capture_output=True)
            evaluation = {"unit_id": "dev:T-1", "node": "dev", "task_id": "T-1",
                          "approved": True, "content_roots": ["src/a.py"],
                          "content_hash": content_hash(worktree, ["src/a.py"])}
            artifact.write_text("VALUE = 2\n", encoding="utf-8")
            result = {"task_id": "T-1", "task": deepcopy(task),
                      "outcome": "awaiting_human", "evaluation": evaluation,
                      "collected": True}
            state = {
                "tasks": [task], "parallel_batch": {"id": "B-1"},
                "parallel_results": {"B-1:T-1": result},
                "pending_review": {"task_ids": ["T-1"]}, "history": [],
                "attempts": {}, "status": "running", "agent_calls": 0,
            }
            commit = Mock()
            runner = parallel_tasks.ParallelTasks(
                str(wd), None, {"budget": {"max_retries_per_gate": 2}},
                {"dev": {"id": "dev", "writes": ["src/"]}}, False,
                None, None, Mock(), commit, lambda value: value, Mock())
            with patch.object(task_worktrees, "cleanup") as cleanup:
                current = runner.approve_human(state, {"actor": "qa"})
            task_worktrees.cleanup(str(wd), task)
        commit.assert_not_called()
        cleanup.assert_called_once()
        self.assertEqual(current["tasks"][0]["status"], "pending")
    def test_rechazo_humano_reanuda_generador_con_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            base = [PY, "-m", "sdd.runtime.orchestrator",
                    "--workdir", str(wd), "--simulate"]
            stopped = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)

            rejected = subprocess.run(
                base + ["--resume", "--human-decision", "reject",
                        "--human-feedback", "agrega un caso limite"],
                capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 0,
                             rejected.stdout + rejected.stderr)
            state = json.loads((wd / ".agent/state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "waiting_human")
            self.assertEqual(state["attempts"]["product:H1"], 1)
            decisions = [item.get("decision", {}) for item in state["iterations"]
                         if item.get("stage") == "human_review"]
            self.assertTrue(any(item.get("action") == "reject" and
                                item.get("feedback") == "agrega un caso limite"
                                for item in decisions))

            bypass = subprocess.run(base + ["--from", "architect"],
                                    capture_output=True, text=True)
            self.assertEqual(bypass.returncode, 1)
            self.assertIn("decision HITL pendiente", bypass.stdout)

    def test_checkpoint_interrupt_firma_y_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            base = [PY, "-m", "sdd.runtime.orchestrator",
                    "--workdir", str(wd), "--simulate"]

            stopped = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            state_path = wd / ".agent/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "waiting_human")
            self.assertEqual(state["engine"], "langgraph")
            self.assertEqual(state["cursor"], "human_review")
            self.assertEqual(state["pending_review"]["unit_ids"], ["product:linear"])
            self.assertIsInstance(state["evaluation"], dict)
            self.assertTrue(state["evaluation"]["approved"])
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

            # Corrompe la proyeccion: al consumir interrupt() solo debe entrar la
            # decision; cursor, reloj y evaluacion vienen del checkpoint SQLite.
            state["started_at"] = 1
            state["cursor"] = "product"
            state["evaluation"]["approved"] = False
            state_path.write_text(json.dumps(state), encoding="utf-8")
            resumed_output = ""
            for _ in range(12):
                resumed = subprocess.run(
                    base + ["--resume", "--human-decision", "accept"],
                    capture_output=True,
                                         text=True)
                resumed_output += resumed.stdout + resumed.stderr
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state["status"] == "done":
                    break
                self.assertEqual(state["status"], "waiting_human",
                                 resumed_output[-3000:])
            self.assertEqual(state["status"], "done", resumed_output[-3000:])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "done")
            self.assertTrue(state["human_approval"]["approved"])
            self.assertEqual(state["human_approval"]["actor"], "cli")
            self.assertEqual(len(state["human_approval"]["spec_hash"]), 64)
            self.assertNotIn("max_wall_minutes agotado", resumed_output)
            self.assertNotEqual(state["started_at"], 1)
            self.assertTrue(any(
                event.get("event") == "APROBADO"
                and event.get("nodo") == "product"
                for event in state["history"]))
            approved_units = {unit for record in state["human_approvals"]
                              for unit in record.get("unit_ids", [])}
            self.assertTrue({"product:linear", "architect:linear", "planner:linear"}
                            <= approved_units)

    def test_send_workers_ejecutan_batch_paralelo_y_limpian_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            proc = subprocess.run(
                [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd),
                 "--simulate", "--autonomous"],
                capture_output=True, text=True,
                env={**os.environ, "SDD_FAKE_PARALLEL": "1"})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            state = json.loads((wd / ".agent/state.json").read_text(encoding="utf-8"))
            batches = [event for event in state["history"]
                       if event.get("event") == "BATCH"]
            # La huella real puede serializar tareas que antes parecian disjuntas
            # por entregables pero comparten writes del nodo.
            self.assertTrue(batches)
            self.assertGreaterEqual(sum(event.get("tareas", 0) for event in batches), 4)
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

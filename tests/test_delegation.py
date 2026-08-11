"""Regresiones de identidad y divide-y-venceras del sprint."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sdd.control_tower import agent_instances
from sdd.core import lifecycle
from sdd.runtime import (agent, delegation, parallel_tasks, task_worktrees,
                         taskqueue, work_unit_graph)


NODES = {
    "dev": {"id": "dev", "task_node": True, "writes": ["src/"]},
    "qa": {"id": "qa", "task_node": True, "writes": ["tests/"]},
}

def parent_task():
    task = {
        "id": "T-001", "title": "servicio completo", "node": "dev",
        "fr_refs": ["FR-001"],
        "deliverables": ["src/domain.py", "src/api.py"],
        "depends_on": [], "acceptance": "servicio verificable", "scope": "api",
        "kind": "plan", "status": "pending", "depth": 0,
    }
    delegation.ensure_identity(task)
    delegation.allow_delegation(task)
    return task


def proposal():
    return {
        "reason": "dominio y API son resultados independientes",
        "subtasks": [
            {
                "key": "S1", "title": "implementar dominio", "node": "dev",
                "fr_refs": ["FR-001"], "deliverables": ["src/domain.py"],
                "depends_on": [], "acceptance": "el dominio valida entradas",
                "scope": "domain",
            },
            {
                "key": "S2", "title": "exponer API", "node": "dev",
                "fr_refs": ["FR-001"], "deliverables": ["src/api.py"],
                "depends_on": ["S1"], "acceptance": "la API usa el dominio",
                "scope": "api",
            },
        ],
    }


class TestDelegationProtocol(unittest.TestCase):
    def test_parsea_json_estricto(self):
        parsed = delegation.parse_proposal(
            "<<<DELEGATE>>>\n" + json.dumps(proposal()) +
            "\n<<<END_DELEGATE>>>")
        self.assertEqual(parsed, proposal())

    def test_rechaza_json_invalido_y_bloques_duplicados(self):
        with self.assertRaises(delegation.DelegationError):
            delegation.parse_proposal(
                "<<<DELEGATE>>>{bad}<<<END_DELEGATE>>>")
        block = "<<<DELEGATE>>>{}<<<END_DELEGATE>>>"
        with self.assertRaises(delegation.DelegationError):
            delegation.parse_proposal(block + block)

    def test_prompt_publica_identidad_y_autoridad(self):
        task = parent_task()
        path_type = type(Path("."))
        with patch.object(path_type, "exists", return_value=True), \
                patch.object(path_type, "read_text",
                             return_value=json.dumps(task)):
            text, _ = agent.gather_task(Path("."))
        self.assertIn("agent:dev:t-001", text)
        self.assertIn('"allowed": true', text)


class TestDelegationValidation(unittest.TestCase):
    def test_crea_hijos_con_identidad_linaje_y_dependencias(self):
        events = []
        with patch.object(lifecycle, "_emit",
                          side_effect=lambda _wd, _id, event:
                          events.append(event)):
            tasks = [parent_task()]
            children = delegation.apply_proposal(
                tasks, tasks[0], proposal(), NODES, {"budget": {}}, ".")

            self.assertEqual(tasks[0]["status"], "delegated")
            self.assertEqual(tasks[0]["child_ids"], ["T-001.1", "T-001.2"])
            self.assertEqual(children[1]["depends_on"], ["T-001.1"])
            self.assertEqual(children[0]["agent"]["parent_id"],
                             "agent:dev:t-001")
            self.assertEqual(children[0]["agent"]["lineage"],
                             ["agent:dev:t-001"])
            self.assertEqual(children[0]["depth"], 1)
            created = next(event for event in events
                           if event.get("event") == "created")
            self.assertEqual(created["agent_id"], "agent:dev:t-001.1")
            self.assertEqual(created["parent_task_id"], "T-001")

    def test_aplicacion_es_idempotente_y_detecta_deriva(self):
        tasks = [parent_task()]
        first = delegation.apply_proposal(
            tasks, tasks[0], proposal(), NODES, {"budget": {}})
        second = delegation.apply_proposal(
            tasks, tasks[0], proposal(), NODES, {"budget": {}})
        self.assertEqual([item["id"] for item in second],
                         [item["id"] for item in first])
        changed = proposal()
        changed["reason"] = "otra decision"
        with self.assertRaises(delegation.DelegationError):
            delegation.apply_proposal(
                tasks, tasks[0], changed, NODES, {"budget": {}})

    def test_rechaza_escape_cobertura_incompleta_y_nodo_sin_autoridad(self):
        bad_path = proposal()
        bad_path["subtasks"][1]["deliverables"] = ["../secret.txt"]
        with self.assertRaisesRegex(delegation.DelegationError, "insegura"):
            parent = parent_task()
            delegation.apply_proposal(
                [parent], parent, bad_path, NODES, {"budget": {}})

        incomplete = proposal()
        incomplete["subtasks"][1]["deliverables"] = ["src/domain.py"]
        incomplete["subtasks"][1]["depends_on"] = ["S1"]
        with self.assertRaisesRegex(delegation.DelegationError, "no cubren"):
            parent = parent_task()
            delegation.apply_proposal(
                [parent], parent, incomplete, NODES, {"budget": {}})

        wrong_node = proposal()
        wrong_node["subtasks"][0]["node"] = "qa"
        with self.assertRaisesRegex(delegation.DelegationError, "propiedad"):
            parent = parent_task()
            delegation.apply_proposal(
                [parent], parent, wrong_node, NODES, {"budget": {}})

    def test_rechaza_ciclo_y_escritura_concurrente_del_mismo_archivo(self):
        cycle = proposal()
        cycle["subtasks"][0]["depends_on"] = ["S2"]
        with self.assertRaisesRegex(delegation.DelegationError, "ciclo"):
            parent = parent_task()
            delegation.apply_proposal(
                [parent], parent, cycle, NODES, {"budget": {}})

        parent = parent_task()
        parent["deliverables"] = ["src/domain.py"]
        overlap = proposal()
        for child in overlap["subtasks"]:
            child["deliverables"] = ["src/domain.py"]
            child["depends_on"] = []
        with self.assertRaisesRegex(delegation.DelegationError, "serializacion"):
            delegation.apply_proposal(
                [parent], parent, overlap, NODES, {"budget": {}})

    def test_limites_de_profundidad_y_fanout_son_fail_closed(self):
        parent = parent_task()
        parent["depth"] = 2
        with self.assertRaisesRegex(delegation.DelegationError, "autoridad"):
            delegation.apply_proposal(
                [parent], parent, proposal(), NODES,
                {"budget": {"max_delegation_depth": 2}})
        oversized = proposal()
        oversized["subtasks"].append({
            "key": "S3", "title": "documentar API", "node": "dev",
            "fr_refs": ["FR-001"], "deliverables": ["src/api.py"],
            "depends_on": ["S2"], "acceptance": "API documentada",
            "scope": "api-docs",
        })
        with self.assertRaisesRegex(delegation.DelegationError, "entre 2 y 2"):
            parent = parent_task()
            delegation.apply_proposal(
                [parent], parent, oversized, NODES,
                {"budget": {"max_subtasks_per_task": 2}})

    def test_rollup_cierra_padre_y_libera_dependientes(self):
        tasks = [parent_task()]
        children = delegation.apply_proposal(
            tasks, tasks[0], proposal(), NODES, {"budget": {}})
        downstream = {
            "id": "T-002", "node": "qa", "kind": "plan", "status": "pending",
            "depends_on": ["T-001"],
        }
        tasks.append(downstream)
        children[0]["status"] = "done"
        self.assertEqual(delegation.rollup(tasks), [])
        self.assertNotIn(downstream, taskqueue.runnable(tasks))
        children[1]["status"] = "done"
        self.assertEqual(delegation.rollup(tasks), ["T-001"])
        self.assertIn(downstream, taskqueue.runnable(tasks))


class TestDelegationIntegration(unittest.TestCase):
    def worker(self, workdir):
        cfg = {"budget": {"max_agent_calls": 6, "max_retries_per_gate": 2,
                           "max_wall_minutes": 30, "max_output_tokens": 0}}
        return work_unit_graph.WorkUnitGraph(
            workdir, SimpleNamespace(), cfg, NODES, False, Mock(), Mock(), Mock(),
            lambda value: value, lambda _path: {"output_tokens": 0})

    def test_worker_emite_outcome_delegated_sin_ejecutar_gates(self):
        tmp = "."
        with patch.object(delegation, "read_proposal",
                          return_value=proposal()):
            task = parent_task()
            task["workspace"] = {"path": tmp}
            worker = self.worker(tmp)
            state = {
                "tasks": [task], "worker_task_id": "T-001",
                "parallel_batch": {"id": "B-1", "agent_quota": 2},
                "agent_calls": 4, "history": [], "attempts": {},
                "generation": {"returncode": delegation.RETURN_CODE},
                "work_unit_error": "", "decomposition": None,
            }
            decomposed = worker.decompose(state)
            with patch.object(task_worktrees, "preserve"), \
                    patch.object(work_unit_graph.metrics, "transfer"), \
                    patch.object(work_unit_graph.chronicle, "transfer"), \
                    patch.object(work_unit_graph.optimized_gates,
                                 "transfer_history"):
                result = worker.finalize(decomposed)
        output = result["parallel_results"]["B-1:T-001"]
        self.assertEqual(worker.after_generate(state), "decompose")
        self.assertEqual(output["outcome"], "delegated")
        self.assertEqual(output["decomposition"], proposal())

    def test_fan_in_materializa_hijos_y_expone_instancias(self):
        tmp = "."
        with patch.object(lifecycle, "created"), \
                patch.object(lifecycle, "delegated"):
            parent = parent_task()
            parent["workspace"] = {"path": tmp}
            result = {
                "task_id": "T-001", "task": deepcopy(parent),
                "outcome": "delegated", "decomposition": proposal(),
                "evaluation": None, "agent_calls": 1, "history": [],
                "iterations": [], "attempts": {}, "gate_refunds": {},
            }
            state = {
                "tasks": [parent], "parallel_batch": {"id": "B-1"},
                "parallel_results": {"B-1:T-001": result},
                "collect_queue": ["B-1:T-001"], "history": [],
                "iterations": [], "attempts": {}, "gate_refunds": {},
                "agent_calls": 0, "defect_seq": 0, "status": "running",
            }
            log = Mock()
            runner = parallel_tasks.ParallelTasks(
                tmp, None, {"budget": {}}, NODES, False, None, None, log,
                Mock(), lambda value: value, Mock())
            with patch.object(task_worktrees, "cleanup"):
                current = runner.delegate_result(state)

            sprint = [{key: task.get(key) for key in (
                "id", "title", "node", "status", "agent", "parent_task_id",
                "child_ids", "depth")}
                for task in current["tasks"]]
            instances = agent_instances.project(sprint, [])
        self.assertEqual(current["tasks"][0]["status"], "delegated")
        self.assertEqual(len(current["tasks"]), 3)
        self.assertEqual(len(instances), 3)
        self.assertEqual(instances[1]["parent_id"], "agent:dev:t-001")
        self.assertEqual(parallel_tasks.ParallelTasks.route_batch({
            "collect_queue": ["B-1:T-001"],
            "parallel_results": {"B-1:T-001": {"outcome": "delegated"}},
        }), "delegate")


if __name__ == "__main__":
    unittest.main()

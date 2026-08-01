"""Normalizacion y deltas del estado serializable de LangGraph."""
import copy
import hashlib

from sdd.runtime.workflow_state import PipelineState, RESET_RESULTS


def copy_state(state: dict[str, object]) -> PipelineState:
    return copy.deepcopy(dict(state))


def delta(before: PipelineState, after: PipelineState) -> dict[str, object]:
    changed: dict[str, object] = {}
    for key, value in after.items():
        if before.get(key) == value:
            continue
        changed[key] = ({RESET_RESULTS: True}
                        if key == "parallel_results" and not value and before.get(key)
                        else value)
    return changed


def normalize(state: PipelineState) -> PipelineState:
    defaults = {
        "tasks": [], "history": [], "attempts": {}, "gate_refunds": {},
        "current_task": None,
        "resume_at": None, "resume_stack": [], "recoveries": [],
        "recovery_seq": 0, "active_visit": None, "batch_seq": 0,
        "parallel_batch": None, "parallel_results": {}, "worker_task_id": None,
        "human_approvals": [], "human_decision": None, "generation": None,
        "evaluation": None, "pending_review": None, "defect_decision": None,
        "feedback": "", "retry_count": 0, "iterations": [],
        "work_unit_started": False, "work_unit_error": "",
    }
    for key, value in defaults.items():
        state.setdefault(key, copy.deepcopy(value))
    state["engine"] = "langgraph"
    state["checkpoint_db"] = ".agent/checkpoints.sqlite"
    return state


def visit_id(state: PipelineState) -> str:
    identity = ":".join((str(state.get("run_id", "run")),
                         str(state.get("cursor", "node")),
                         str(state.get("current_task") or "linear"),
                         str(state.get("agent_calls", 0))))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def has_interrupt(snapshot: object) -> bool:
    return bool(getattr(snapshot, "interrupts", ()))

"""Estado unico del grafo principal y sus subgrafos."""
from typing import Annotated, TypedDict


RESET_RESULTS = "__sdd_reset__"


def merge_results(left: dict[str, object],
                  right: dict[str, object]) -> dict[str, object]:
    """Acumula ramas de Send y permite liberar el lote ya colectado."""
    if right.get(RESET_RESULTS) is True:
        return {key: value for key, value in right.items()
                if key != RESET_RESULTS}
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    run_id: str
    cursor: str
    status: str
    attempts: dict[str, int]
    # Cuantas veces se le ha devuelto el presupuesto de reintentos a cada
    # unidad:gate. Un gate que pasa lo recupera, pero no indefinidamente: un
    # veredicto que oscila convertiria ese reembolso en una via de escape.
    gate_refunds: dict[str, int]
    agent_calls: int
    started_at: float
    original_started_at: float
    resume_started_at: float
    resume_history: list[dict[str, object]]
    resume_checkpoint: dict[str, object]
    resume_recovery: dict[str, object] | None
    tasks: list[dict[str, object]]
    current_task: str | None
    defect_seq: int
    history: list[dict[str, object]]
    resume_at: str | None
    resume_stack: list[str]
    recoveries: list[dict[str, object]]
    recovery_seq: int
    active_visit: str | None
    human_approval: dict[str, object]
    human_approvals: list[dict[str, object]]
    human_decision: dict[str, object] | None
    generation: dict[str, object] | None
    evaluation: dict[str, object] | None
    pending_review: dict[str, object] | None
    defect_decision: dict[str, object] | None
    feedback: str
    retry_count: int
    iterations: list[dict[str, object]]
    engine: str
    checkpoint_db: str
    batch_seq: int
    parallel_batch: dict[str, object] | None
    parallel_results: Annotated[dict[str, object], merge_results]
    worker_task_id: str | None
    work_unit_started: bool
    work_unit_error: str
    content_validation: str
    schedule_route: str
    ready_task_ids: list[str]
    collect_queue: list[str]
    review_result_keys: list[str]
    approval_next: str


class WorkUnitOutput(TypedDict, total=False):
    """Proyeccion reducer que una rama devuelve al fan-in principal."""

    parallel_results: Annotated[dict[str, object], merge_results]

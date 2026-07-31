"""Contratos serializables compartidos por el workflow LangGraph."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Finding(BaseModel):
    file: str
    line: int = 0
    rule: str
    evidence: str


class Evaluation(BaseModel):
    """Resultado tipado del evaluador persistido en PipelineState."""

    unit_id: str
    node: str
    task_id: str | None = None
    approved: bool
    gate_id: str | None = None
    owner: str | None = None
    feedback: str = ""
    findings: list[Finding] = Field(default_factory=list)
    reports: list[dict[str, object]] = Field(default_factory=list)
    solution: dict[str, object] = Field(default_factory=dict)
    content_roots: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def rejection_has_feedback(self):
        if not self.approved and not self.feedback.strip():
            raise ValueError("una evaluacion rechazada requiere feedback")
        return self


class HumanDecision(BaseModel):
    action: Literal["accept", "reject"]
    actor: str = "human"
    feedback: str = ""

    @model_validator(mode="after")
    def rejection_has_feedback(self):
        if self.action == "reject" and not self.feedback.strip():
            raise ValueError("reject requiere feedback")
        return self


class DefectDecision(BaseModel):
    """Clasificacion pura; sus efectos se ejecutan en nodos distintos."""

    route: Literal["retry", "delegate", "escalate"]
    failed_node: str
    owner: str
    gate_id: str
    task_id: str | None = None
    findings: list[Finding]
    attempt: int = 0
    exhausted: bool = False
    infrastructure: bool = False
    project_escalation: bool = False
    reason: str = ""

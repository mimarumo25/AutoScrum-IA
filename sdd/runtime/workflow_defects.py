"""Clasificacion y efectos de defectos para nodos LangGraph explicitos."""
from sdd.core import config, lifecycle, process_control
from sdd.runtime import taskqueue
from sdd.runtime.workflow_contracts import DefectDecision, Finding


ENVIRONMENT_RULES = {
    "toolchain-no-disponible", "entorno-sin-red", "suite-colgada",
    "revision-no-disponible", "proveedor-no-disponible",
}


def linear_recovery_context(state, owner):
    """Construye la tarea visible que recibe un agente al corregir a otro."""
    assigned = [item for item in state.setdefault("recoveries", [])
                if item.get("status") == "assigned" and item.get("owner") == owner]
    findings = []
    seen = set()
    for recovery in assigned:
        for finding in recovery.get("findings") or []:
            identity = (finding.get("file"), finding.get("line"),
                        finding.get("rule"), finding.get("evidence"))
            if identity in seen:
                continue
            seen.add(identity)
            findings.append(finding)
    gates = sorted({str(item.get("gate_id")) for item in assigned})
    sources = sorted({str(item.get("failed_node")) for item in assigned})
    return {
        "id": "+".join(str(item["id"]) for item in assigned),
        "title": f"Corregir hallazgos solicitados por {', '.join(sources)}",
        "node": owner, "kind": "defect", "status": "pending",
        "gate_id": ",".join(gates), "findings": findings,
        "deliverables": [],
        "context": sorted({finding.get("file") for finding in findings
                           if finding.get("file") and "*" not in finding.get("file")}),
        "depends_on": [], "fr_refs": [],
        "acceptance": "Corregir los hallazgos y dejar verdes los gates indicados",
        "model_escalated": any(item.get("model_escalated") for item in assigned),
        "model_escalation_count": max(
            [int(item.get("model_escalation_count", 0)) for item in assigned] or [0]),
        "recovery_ids": [str(item["id"]) for item in assigned],
    }


def assign_linear_recovery(state, workdir, failed_node, owner, gate_id,
                           findings, attempt):
    """Crea o actualiza un handoff durable sin elegir la siguiente arista."""
    recoveries = state.setdefault("recoveries", [])
    recovery = next((item for item in reversed(recoveries)
                     if item.get("status") == "assigned"
                     and item.get("failed_node") == failed_node
                     and item.get("owner") == owner
                     and item.get("gate_id") == gate_id), None)
    if recovery is None:
        state["recovery_seq"] = int(state.get("recovery_seq", 0)) + 1
        recovery = {
            "id": f"R-{state['recovery_seq']:03d}",
            "failed_node": failed_node, "owner": owner,
            "gate_id": gate_id, "status": "assigned",
            "model_escalation_count": 0,
        }
        recoveries.append(recovery)
    recovery["findings"] = findings
    recovery["attempt"] = attempt
    if owner != failed_node:
        stack = state.setdefault("resume_stack", [])
        if not stack or stack[-1] != failed_node:
            stack.append(failed_node)
    taskqueue.publish_current(workdir, linear_recovery_context(state, owner))
    return recovery


def resolve_linear_recoveries(state, workdir, owner, next_node, log_fn):
    """Libera a los agentes que esperaban una correccion del propietario."""
    assigned = [item for item in state.setdefault("recoveries", [])
                if item.get("status") == "assigned" and item.get("owner") == owner]
    if not assigned:
        return None
    for recovery in assigned:
        recovery["status"] = "corrected"
        recovery["resolved_by"] = owner
        state.setdefault("attempts", {}).pop(
            f"{recovery.get('owner')}:{recovery.get('gate_id')}", None)
    taskqueue.clear_current(workdir)
    return_target = next((str(item.get("failed_node")) for item in reversed(assigned)
                          if item.get("failed_node") != owner), None)
    stack = state.setdefault("resume_stack", [])
    if return_target:
        while stack:
            if stack.pop() == return_target:
                break
    log_fn(state, "CORRECCION_RECIBIDA", de=owner,
           reanuda=return_target or next_node,
           recuperaciones=",".join(str(item["id"]) for item in assigned))
    return return_target


def classify_defect(state, node, task, owner, gate_id, findings, budget):
    """Clasifica sin mutar estado; LangGraph elige el nodo de efectos."""
    normalized = [Finding.model_validate(item) for item in findings]
    environment = next((item for item in normalized
                        if item.rule in ENVIRONMENT_RULES), None)
    task_id = str(task["id"]) if task else None
    key = f"{task_id}:{gate_id}" if task_id else f"{owner}:{gate_id}"
    attempt = int(state.get("attempts", {}).get(key, 0)) + (0 if environment else 1)
    exhausted = attempt > int(budget["max_retries_per_gate"])
    defect_limit = int(budget.get("max_defect_tasks", 12))
    defect_exhausted = int(state.get("defect_seq", 0)) >= defect_limit

    if environment:
        route = "escalate"
        reason = (f"{environment.rule} - requiere intervencion en la maquina: "
                  f"{environment.evidence[:160]}")
    elif task is None:
        route = "escalate" if exhausted else ("retry" if owner == node["id"]
                                                else "delegate")
        reason = "max_retries_per_gate agotado" if exhausted else ""
    elif exhausted:
        route = "escalate" if defect_exhausted else "delegate"
        reason = (f"la rama alcanzo el tope de {defect_limit} correcciones"
                  if defect_exhausted else "")
    elif owner == node["id"]:
        route, reason = "retry", ""
    elif defect_exhausted:
        route, reason = "escalate", "tope de tareas de defecto alcanzado"
    else:
        route, reason = "delegate", ""

    return DefectDecision(
        route=route, failed_node=str(node["id"]), owner=str(owner),
        gate_id=str(gate_id), task_id=task_id, findings=normalized,
        attempt=attempt, exhausted=exhausted, infrastructure=environment is not None,
        project_escalation=bool(task is None or (not exhausted and defect_exhausted)),
        reason=reason,
    ).model_dump(mode="json")


def _record_defect(state, workdir, node, task, decision, budget, log_fn):
    findings = decision["findings"]
    gate_id = str(decision["gate_id"])
    owner = str(decision["owner"])
    for finding in findings[:5]:
        log_fn(state, "DEFECTO", gate=gate_id, owner=owner,
               ubicacion=f"{finding['file']}:{finding['line']}",
               regla=finding["rule"], evidencia=finding["evidence"])
    if decision.get("infrastructure"):
        return
    if gate_id == "G7":
        for finding in findings:
            process_control.run_git(workdir, "checkout", "--", finding["file"], text=True)
            process_control.run_git(workdir, "clean", "-fd", finding["file"], text=True)
        log_fn(state, "REVERT", archivos=len(findings),
               motivo="violacion de propiedad")
    key = f"{task['id']}:{gate_id}" if task else f"{owner}:{gate_id}"
    state.setdefault("attempts", {})[key] = int(decision["attempt"])
    if task:
        lifecycle.retried(workdir, task["id"], gate_id, int(decision["attempt"]),
                          int(budget["max_retries_per_gate"]))
    eligible = {
        "dev_backend": {"G0", "G4", "G5", "G6", "G7", "R2"},
        "dev_frontend": {"G0", "G4", "G5", "G6", "G7", "R2"},
        "qa": {"G8", "G9", "R2"},
    }
    routing = config.load().get("routing", {})
    max_escalations = int(routing.get("max_frontier_escalations_per_task", 1))
    if (task and owner == node["id"] and gate_id in eligible.get(node["id"], set())
            and int(task.get("model_escalation_count", 0)) < max_escalations):
        task["model_escalation_count"] = int(task.get("model_escalation_count", 0)) + 1
        task["model_escalated"] = True
        task["model_escalation_gate"] = gate_id
        taskqueue.publish_current(workdir, task)
        lifecycle.model_escalated(workdir, task["id"], gate_id,
                                  task["model_escalation_count"])
        log_fn(state, "MODEL_ESCALATION", tarea=task["id"], gate=gate_id,
               tier="frontier", intento=task["model_escalation_count"])


def retry_defect(state, workdir, node, task, decision, budget, log_fn):
    """Aplica exclusivamente efectos de una arista retry."""
    _record_defect(state, workdir, node, task, decision, budget, log_fn)
    if task is None:
        recovery = assign_linear_recovery(
            state, workdir, node["id"], decision["owner"], decision["gate_id"],
            decision["findings"], decision["attempt"])
        log_fn(state, "AGENTE_EN_ESPERA", nodo=node["id"], espera=decision["owner"],
               gate=decision["gate_id"], recuperacion=recovery["id"])
    state["cursor"] = str(decision["owner"])
    log_fn(state, "ENRUTADO", a=decision["owner"], intento=decision["attempt"],
           reanuda_en=task["id"] if task else decision["owner"])


def delegate_defect(state, workdir, node, task, decision, budget, log_fn):
    """Materializa un handoff lineal o una tarea de correccion."""
    _record_defect(state, workdir, node, task, decision, budget, log_fn)
    if task is None:
        recovery = assign_linear_recovery(
            state, workdir, node["id"], decision["owner"], decision["gate_id"],
            decision["findings"], decision["attempt"])
        state["cursor"] = str(decision["owner"])
        log_fn(state, "AGENTE_EN_ESPERA", nodo=node["id"], espera=decision["owner"],
               gate=decision["gate_id"], recuperacion=recovery["id"])
        log_fn(state, "CORRECCION_ASIGNADA", id=recovery["id"], de=node["id"],
               a=decision["owner"], gate=decision["gate_id"],
               intento=decision["attempt"])
        return
    state["defect_seq"] = int(state.get("defect_seq", 0)) + 1
    defect = taskqueue.make_defect(
        state["tasks"], decision["owner"], decision["gate_id"],
        decision["findings"], task, state["defect_seq"], workdir)
    lifecycle.blocked(workdir, task["id"], defect["id"], decision["gate_id"],
                      decision["findings"])
    log_fn(state, "AGENTE_EN_ESPERA", nodo=node["id"], espera=decision["owner"],
           tarea=task["id"], correccion=defect["id"], gate=decision["gate_id"])
    log_fn(state, "DEFECTO_TAREA", id=defect["id"], para=decision["owner"],
           bloquea=task["id"], gate=decision["gate_id"])
    state["cursor"] = "task_loop"


def escalate_defect(state, workdir, node, task, decision, budget, log_fn):
    """Finaliza una rama o el proyecto sin rutas fail-open."""
    _record_defect(state, workdir, node, task, decision, budget, log_fn)
    reason = str(decision.get("reason") or "la correccion automatica no convergio")
    if task is not None and not decision.get("project_escalation"):
        taskqueue.mark_needs_input(state["tasks"], task["id"], reason,
                                   decision["gate_id"])
        lifecycle.blocked(workdir, task["id"], "human-input", decision["gate_id"],
                          decision["findings"])
        state["current_task"] = None
        state["cursor"] = "task_loop"
        log_fn(state, "RAMA_EN_ESPERA", tarea=task["id"], nodo=node["id"],
               gate=decision["gate_id"], motivo=reason)
        return
    if task is None and not decision.get("infrastructure"):
        recovery = assign_linear_recovery(
            state, workdir, node["id"], decision["owner"], decision["gate_id"],
            decision["findings"], decision["attempt"])
        recovery["status"] = "needs_input"
        log_fn(state, "RECUPERACION_EN_ESPERA", id=recovery["id"], nodo=node["id"],
               espera=decision["owner"], gate=decision["gate_id"], motivo=reason)
    state["status"] = "escalated"
    log_fn(state, "ESCALATE_HUMAN", nodo=node["id"], gate=decision["gate_id"],
           motivo=reason)

"""Estado efimero de una corrida y proyeccion de eventos del orquestador."""

import re
import threading
import time

LOCK = threading.Lock()
RUN = {
    "status": "idle",
    "workdir": None,
    "log": [],
    "provider": None,
    "project": None,
    "task": None,
    "revision": 0,
    "updated_at": 0.0,
    "activity": {
        "phase": "idle",
        "node": "",
        "task": "",
        "message": "Esperando una ejecución",
    },
    "failure": None,
}

TERMINAL_STATUSES = {"done", "completed", "escalated", "waiting_human", "error"}
ACTIVE_PHASES = {
    "starting",
    "thinking",
    "streaming",
    "tool_call",
    "validating",
    "retrying",
}
_FIELD_RE = re.compile(r"([A-Za-z_][\w-]*)=(.*?)(?=\s+[A-Za-z_][\w-]*=|$)")
_AGENT_NAMES = {
    "product": "Definición del producto",
    "architect": "Arquitectura de la solución",
    "planner": "Planificación del trabajo",
    "dev_backend": "Desarrollo del servidor",
    "dev_frontend": "Desarrollo de la interfaz",
    "qa": "Control de calidad",
    "human_gate": "Aprobación humana",
}
_NFR_AREAS = {
    "USAB": ("Facilidad de uso", "un tiempo máximo o un porcentaje de éxito"),
    "SEC": ("Seguridad", "un control concreto que una prueba pueda confirmar"),
    "PERF": ("Rendimiento", "un tiempo de respuesta o una capacidad mínima"),
    "REL": ("Confiabilidad", "un límite de errores o una disponibilidad esperada"),
}


def run_update(**changes):
    with LOCK:
        RUN.update(changes)
        RUN["revision"] = int(RUN.get("revision", 0)) + 1
        RUN["updated_at"] = time.time()


def activity(phase, node="", task="", message="", **extra):
    return {
        "phase": phase,
        "node": node or "",
        "task": task or "",
        "message": message or "",
        "updated_at": time.time(),
        **extra,
    }


def event_fields(text):
    return {
        match.group(1): match.group(2).strip()
        for match in _FIELD_RE.finditer(text or "")
    }


def humanize_failure(reason="", findings=None, rule="", node="", attempt=None):
    """Traduce diagnósticos internos a una explicación útil para una persona."""
    technical = list(dict.fromkeys(item for item in (findings or []) if item))
    metric_items = [
        match.group(1).upper()
        for item in technical
        if (
            match := re.search(
                r"NFR-([A-Z]+)-\d+\s+sin campo metrica", item, re.IGNORECASE
            )
        )
    ]
    metric_problem = (
        rule == "nfr-no-medible"
        or metric_items
        or any(
            "falta metrica" in item.lower() or "sin campo metrica" in item.lower()
            for item in technical
        )
    )
    if metric_problem:
        count = len(metric_items) or max(len(technical), 1)
        groups = []
        for code in dict.fromkeys(metric_items):
            amount = metric_items.count(code)
            area, example = _NFR_AREAS.get(
                code, ("Calidad del sistema", "un resultado numérico verificable")
            )
            groups.append(
                f"{area}: {amount} {'requisito' if amount == 1 else 'requisitos'} sin una medida comprobable; por ejemplo, {example}."
            )
        if not groups:
            groups = [
                "El requisito describe una intención, pero no indica un valor concreto que permita comprobar si se cumplió."
            ]
        noun = "requisito" if count == 1 else "requisitos"
        return {
            "user_title": f"Falta definir cómo comprobar {count} {noun}",
            "user_reason": "El diseño explica lo que debería lograr el sistema, pero no indica qué resultado concreto demostraría que esos requisitos se cumplen.",
            "user_impact": "Sin esas medidas, el equipo de calidad no puede aprobar el diseño ni crear pruebas objetivas.",
            "user_action": "El agente de arquitectura debe añadir una medida y un valor esperado a cada requisito. Después puede continuar desde este punto.",
            "user_findings": groups,
        }
    stage = _AGENT_NAMES.get(node, node or "La ejecución")
    attempt_text = f" después de {attempt} intentos" if attempt else ""
    return {
        "user_title": f"{stage} no pudo completar la revisión",
        "user_reason": f"La revisión no pudo aprobarse{attempt_text}.",
        "user_impact": "El proceso se detuvo para evitar continuar con información incompleta.",
        "user_action": "Revisa la explicación disponible y continúa desde este punto cuando esté corregida.",
        "user_findings": [],
    }


def observe_pipeline_line(line: str) -> None:
    """Convierte el stdout del orquestador en microestados visibles en vivo."""
    stripped = line.strip()
    node_match = re.match(r">>\s+nodo\s+([\w-]+)(?:\s+·\s+(\S+))?", stripped)
    if node_match:
        node, task = node_match.groups()
        run_update(
            status="running",
            activity=activity(
                "thinking",
                node,
                task or "",
                f"{node} está analizando el contexto y preparando su respuesta",
            ),
        )
        return
    if stripped.startswith("[provider]") or "[provider]" in stripped:
        with LOCK:
            current = dict(RUN.get("activity") or {})
        reason = stripped.split("[provider]", 1)[-1].strip()
        run_update(
            activity=activity(
                "retrying",
                current.get("node"),
                current.get("task"),
                reason,
                reason=reason,
            ),
            failure={
                "id": f"provider:{time.time_ns()}",
                "severity": "warning",
                "node": current.get("node", ""),
                "gate": "provider",
                "reason": reason,
                "findings": [],
                "can_resume": False,
            },
        )
        return
    event_match = re.search(r"\[([^\]]+)\]\s*(.*)$", line)
    if not event_match:
        return
    event, fields = event_match.group(1).strip(), event_fields(event_match.group(2))
    with LOCK:
        current, current_failure = (
            dict(RUN.get("activity") or {}),
            dict(RUN.get("failure") or {}),
        )
    node = (
        fields.get("nodo")
        or fields.get("a")
        or fields.get("owner")
        or current.get("node", "")
    )
    task = fields.get("tarea") or current.get("task", "")
    if event == "AGENTE_INICIO":
        run_update(
            status="running",
            activity=activity(
                "thinking",
                node,
                task,
                f"{node} está consultando el modelo y construyendo la respuesta",
                call=fields.get("llamada", ""),
            ),
        )
    elif event == "AGENTE":
        result = fields.get("resultado", "")
        failed = result and "exit=0" not in result and result != "human"
        run_update(
            activity=activity(
                "error" if failed else "validating",
                node,
                task,
                f"{node} falló: {result}"
                if failed
                else f"{node} terminó; validando artefactos y gates",
            )
        )
    elif event == "GATES_INICIO" or event.startswith("GATE "):
        gate = event.removeprefix("GATE ").strip() if event.startswith("GATE ") else ""
        status = fields.get("estado", "")
        run_update(
            activity=activity(
                "validating",
                node,
                task,
                f"Validando {gate}: {status}"
                if gate
                else f"{node} está ejecutando gates deterministas",
                gate=gate,
                gate_status=status,
            )
        )
    elif event == "DEFECTO":
        reason = fields.get("evidencia") or fields.get("regla") or "Gate rechazado"
        failure = {
            "id": f"{node}:{fields.get('gate', '')}:{reason}",
            "severity": "warning",
            "node": node,
            "gate": fields.get("gate", ""),
            "reason": reason,
            "rule": fields.get("regla", ""),
            "location": fields.get("ubicacion", ""),
            "findings": [reason],
            "can_resume": False,
            **humanize_failure(reason, [reason], fields.get("regla", ""), node),
        }
        run_update(failure=failure)
    elif event == "ENRUTADO":
        attempt, reason = (
            fields.get("intento", ""),
            current_failure.get("reason", "El gate solicitó correcciones"),
        )
        run_update(
            status="running",
            activity=activity(
                "retrying",
                node,
                task,
                f"Reintento {attempt} de {node}: {reason}",
                attempt=attempt,
                gate=current_failure.get("gate", ""),
            ),
            failure={
                **current_failure,
                "severity": "warning",
                "node": node,
                "attempt": attempt,
                "can_resume": False,
            },
        )
    elif event == "AGENTE_EN_ESPERA":
        waiting_for = fields.get("espera", "otro agente")
        run_update(
            status="running",
            activity=activity(
                "waiting",
                fields.get("nodo", node),
                task,
                f"{fields.get('nodo', node)} espera una corrección de {waiting_for}",
                gate=fields.get("gate", ""),
                waiting_for=waiting_for,
                recovery=fields.get("recuperacion") or fields.get("correccion", ""),
            ),
        )
    elif event == "CORRECCION_ASIGNADA":
        owner = fields.get("a") or node
        run_update(
            status="running",
            activity=activity(
                "retrying",
                owner,
                task,
                f"{owner} está corrigiendo un hallazgo de {fields.get('de', 'otro agente')}",
                gate=fields.get("gate", ""),
                attempt=fields.get("intento", ""),
                recovery=fields.get("id", ""),
            ),
        )
    elif event == "RECUPERACION_RESTAURADA":
        owner, amount = fields.get("para") or node, fields.get("hallazgos", "")
        suffix = f" ({amount} hallazgos)" if amount else ""
        run_update(
            status="running",
            failure=None,
            activity=activity(
                "retrying",
                owner,
                task,
                f"{owner} recibió la corrección pendiente{suffix} y la está reparando",
                gate=fields.get("gate", ""),
                recovery=fields.get("id", ""),
            ),
        )
    elif event == "CORRECCION_RECIBIDA":
        target = fields.get("reanuda") or node
        run_update(
            status="running",
            failure=None,
            activity=activity(
                "validating",
                target,
                task,
                f"Corrección recibida de {fields.get('de', 'otro agente')}; {target} continúa con su validación",
            ),
        )
    elif event == "RAMA_EN_ESPERA":
        reason, branch_node = (
            fields.get("motivo") or "La rama necesita información adicional",
            fields.get("nodo") or node,
        )
        run_update(
            status="running",
            activity=activity(
                "waiting",
                branch_node,
                fields.get("tarea", task),
                reason,
                gate=fields.get("gate", ""),
            ),
            failure={
                "id": f"branch:{fields.get('tarea', task)}:{fields.get('gate', '')}",
                "severity": "warning",
                "node": branch_node,
                "gate": fields.get("gate", ""),
                "reason": reason,
                "findings": current_failure.get("findings", []),
                "can_resume": False,
                "user_title": "Una rama espera una corrección",
                "user_reason": reason,
                "user_impact": "Las tareas independientes continúan ejecutándose.",
                "user_action": "La rama se retomará cuando llegue la corrección necesaria.",
                "user_findings": [],
            },
        )
    elif event in {"RAMAS_EN_ESPERA", "RECUPERACION_EN_ESPERA"}:
        reason, branch_node = (
            fields.get("motivo") or "No quedan tareas independientes para ejecutar",
            fields.get("nodo") or node,
        )
        run_update(
            status="waiting_human",
            activity=activity(
                "waiting", branch_node, task, reason, gate=fields.get("gate", "")
            ),
            failure={
                "id": f"waiting:{fields.get('id', '')}:{reason}",
                "severity": "warning",
                "node": branch_node,
                "gate": fields.get("gate", ""),
                "reason": reason,
                "findings": current_failure.get("findings", []),
                "can_resume": True,
                "user_title": "El proyecto espera una corrección concreta",
                "user_reason": reason,
                "user_impact": "Las ramas independientes ya terminaron o no dependen de esta corrección.",
                "user_action": "Corrige la rama indicada y continúa; el trabajo completado se conserva.",
                "user_findings": [],
            },
        )
    elif event in {"ESCALATE_HUMAN", "PRESUPUESTO"}:
        reason = fields.get("motivo") or "La ejecución requiere intervención"
        failure = {
            **current_failure,
            "id": f"{node}:{event}:{reason}",
            "severity": "error",
            "node": node,
            "reason": reason,
            "can_resume": True,
            **humanize_failure(
                reason,
                current_failure.get("findings", []),
                current_failure.get("rule", ""),
                node,
                current_failure.get("attempt"),
            ),
        }
        run_update(
            status="escalated",
            activity=activity(
                "error", node, task, reason, gate=failure.get("gate", "")
            ),
            failure=failure,
        )
    elif event == "GATE_HUMANO":
        reason = fields.get("accion") or "La ejecución espera aprobación humana"
        run_update(
            status="waiting_human",
            activity=activity("blocked", node or "human_gate", task, reason),
            failure={
                "id": f"human:{reason}",
                "severity": "warning",
                "node": node or "human_gate",
                "gate": "human_gate",
                "reason": reason,
                "findings": [],
                "can_resume": True,
            },
        )
    elif event == "APROBADO":
        run_update(
            failure=None,
            activity=activity(
                "completed",
                node,
                task,
                f"{node} completó su trabajo; preparando el siguiente paso",
            ),
        )

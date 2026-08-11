"""Proyeccion segura de decisiones, delegaciones y conversaciones del runtime."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sdd.core import chronicle

MAX_EVENTS = 80
MAX_VISITS = 16
MAX_EXCERPT = 640

_SPACE_RE = re.compile(r"[ \t\f\v]+")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|authorization|access[_ -]?token)\s*[:=]\s*\S+"),
)
_KINDS = {
    "AGENTE_INICIO": "delegation",
    "AGENTE": "io",
    "APROBADO": "decision",
    "EVALUACION": "decision",
    "ENRUTADO": "decision",
    "PRESUPUESTO": "decision",
    "ESCALATE_HUMAN": "decision",
    "GATE_HUMANO": "decision",
    "CORRECCION_ASIGNADA": "delegation",
    "CORRECCION_RECIBIDA": "delegation",
    "AGENTE_EN_ESPERA": "delegation",
    "AGENTE_DELEGA": "delegation",
    "TAREA_DIVIDIDA": "delegation",
    "RECUPERACION_RESTAURADA": "delegation",
    "DEFECTO": "gate",
    "GATES_INICIO": "gate",
}


def _excerpt(value: Any, limit: int = MAX_EXCERPT) -> str:
    text = str(value or "").replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[credencial omitida]", text)
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _event_kind(name: str) -> str:
    if name.startswith("GATE "):
        return "gate"
    if "CORRECCION" in name or "RECUPERACION" in name:
        return "delegation"
    return _KINDS.get(name, "status")


def _actor_target(name: str, item: dict[str, Any]) -> tuple[str, str]:
    node = str(item.get("nodo") or item.get("node") or item.get("owner") or "")
    if name == "AGENTE_INICIO":
        return "orchestrator", node
    if name == "AGENTE":
        return node, "orchestrator"
    if name == "AGENTE_DELEGA":
        return node or str(item.get("agente") or "agent"), str(item.get("a") or "")
    if name == "CORRECCION_ASIGNADA":
        return str(item.get("de") or node), str(item.get("a") or item.get("owner") or "")
    if name in {"CORRECCION_RECIBIDA", "RECUPERACION_RESTAURADA"}:
        return str(item.get("de") or item.get("owner") or "agent"), str(
            item.get("reanuda") or item.get("para") or node
        )
    if name.startswith("GATE ") or name in {"GATES_INICIO", "DEFECTO", "EVALUACION"}:
        return "quality_gates", node or str(item.get("unidad") or "")
    if name in {"APROBADO", "ENRUTADO", "PRESUPUESTO", "ESCALATE_HUMAN", "GATE_HUMANO"}:
        return "orchestrator", node or str(item.get("a") or "")
    return node or "orchestrator", ""


def _history_summary(name: str, item: dict[str, Any], actor: str, target: str) -> str:
    task = str(item.get("tarea") or item.get("task_id") or "")
    if name == "AGENTE_INICIO":
        return f"El orquestador delegó {task or 'la siguiente unidad'} a {target}."
    if name == "AGENTE":
        return f"{actor} devolvió su resultado: {item.get('resultado', 'respuesta recibida')}."
    if name == "TAREA_DIVIDIDA":
        return (f"{item.get('agente') or actor} dividió {task or 'su tarea'} en "
                f"{item.get('hijos') or 'subtareas validadas'}.")
    if name == "AGENTE_DELEGA":
        return (f"{item.get('agente') or actor} delegó {item.get('subtarea') or 'una subtarea'} "
                f"a {item.get('agente_hijo') or target}.")
    if name == "APROBADO":
        return f"El orquestador aprobó el trabajo de {target or item.get('nodo', 'la unidad')}."
    if name == "EVALUACION":
        return f"La evaluación de {item.get('unidad', target or 'la unidad')} terminó en {item.get('estado', 'estado desconocido')}."
    if name.startswith("GATE "):
        return f"{name.removeprefix('GATE ')} terminó en {item.get('estado', 'estado desconocido')} con {item.get('hallazgos', 0)} hallazgos."
    if name == "GATES_INICIO":
        return f"Comenzó la validación determinista de {target or 'la unidad activa'}."
    if name == "DEFECTO":
        return f"Un gate rechazó la salida de {target or 'la unidad'}: {item.get('evidencia') or item.get('regla') or 'hallazgo pendiente'}."
    if name == "CORRECCION_ASIGNADA":
        return f"{actor} pidió a {target} corregir {item.get('gate', 'un hallazgo')} antes de continuar."
    if name == "CORRECCION_RECIBIDA":
        return f"{target} recibió la corrección de {actor} y retomó su trabajo."
    if name == "AGENTE_EN_ESPERA":
        return f"{item.get('nodo', actor)} espera una entrega de {item.get('espera', target or 'otro agente')}."
    if name == "ENRUTADO":
        return f"El orquestador redirigió el trabajo a {item.get('a', target or 'otro agente')} para el intento {item.get('intento', '?')}."
    if name in {"PRESUPUESTO", "ESCALATE_HUMAN"}:
        return f"El orquestador detuvo la ejecución: {item.get('motivo', 'se requiere intervención')}."
    if name == "GATE_HUMANO":
        return f"Se tomó una decisión de revisión: {item.get('accion', 'esperando aprobación')}."
    detail = item.get("detalle") or item.get("motivo") or item.get("accion")
    return _excerpt(detail or f"El runtime registró {name}.", 320)


def _history_events(raw_state: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for index, item in enumerate(raw_state.get("history", [])[-MAX_EVENTS:]):
        if not isinstance(item, dict):
            continue
        name = str(item.get("event") or "EVENTO")
        actor, target = _actor_target(name, item)
        output.append(
            {
                "id": f"history:{index}:{name}:{item.get('t', '')}",
                "time": str(item.get("t") or ""),
                "kind": _event_kind(name),
                "event": name,
                "actor": actor,
                "target": target,
                "task": str(item.get("tarea") or item.get("task_id") or item.get("unidad") or ""),
                "summary": _excerpt(_history_summary(name, item, actor, target), 360),
                "detail": _excerpt(
                    " · ".join(
                        f"{key}: {value}"
                        for key, value in item.items()
                        if key not in {"t", "event"} and value not in (None, "", [], {})
                    ),
                    480,
                ),
            }
        )
    return output


def _conversation_events(workdir: Path | None) -> list[dict[str, Any]]:
    if workdir is None:
        return []
    output = []
    manifests = sorted(
        chronicle.all_visits(workdir),
        key=lambda item: float(item.get("at_epoch") or 0),
        reverse=True,
    )[:MAX_VISITS]
    for manifest in manifests:
        visit_id = str(manifest.get("visit_id") or "")
        if not visit_id:
            continue
        visit = chronicle.read_visit(workdir, visit_id)
        if not visit:
            continue
        actor = str(visit.get("node") or "agent")
        user_input = _excerpt(visit.get("user_prompt"))
        response = _excerpt(visit.get("response") or visit.get("agent_stdout"))
        written = (visit.get("files_written") or {}).get("written", [])
        output.append(
            {
                "id": f"conversation:{visit_id}",
                "time": str(visit.get("at") or ""),
                "sort_time": float(visit.get("at_epoch") or 0),
                "kind": "conversation",
                "event": "CONVERSACION",
                "actor": actor,
                "target": "orchestrator",
                "task": str(visit.get("task_id") or ""),
                "summary": f"{actor} recibió contexto, produjo una respuesta y devolvió {len(written)} artefactos.",
                "input": user_input,
                "output": response,
                "files": [str(path) for path in written[:12]],
                "truncated": bool(
                    len(str(visit.get("user_prompt") or "")) > MAX_EXCERPT
                    or len(str(visit.get("response") or "")) > MAX_EXCERPT
                ),
            }
        )
    return output


def _live_event(activity: dict[str, Any]) -> dict[str, Any] | None:
    if not activity or activity.get("phase") in {None, "", "idle"}:
        return None
    updated_at = float(activity.get("updated_at") or 0)
    node = str(activity.get("node") or "orchestrator")
    return {
        "id": f"live:{updated_at}:{activity.get('phase', '')}:{node}",
        "time": updated_at,
        "sort_time": updated_at,
        "kind": "status",
        "event": "ACTIVIDAD_EN_VIVO",
        "actor": node,
        "target": "",
        "task": str(activity.get("task") or ""),
        "summary": _excerpt(activity.get("message") or "Actividad en curso", 360),
        "detail": _excerpt(
            " · ".join(
                f"{key}: {value}"
                for key, value in activity.items()
                if key not in {"updated_at", "message"} and value not in (None, "")
            ),
            420,
        ),
        "live": True,
    }


def _relationships(raw_state: dict[str, Any], activity: dict[str, Any]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(source: Any, target: Any, kind: str, task: Any = "", state: Any = "") -> None:
        edge = (str(source or ""), str(target or ""), kind, str(task or ""))
        if not edge[0] or not edge[1] or edge in seen:
            return
        seen.add(edge)
        links.append(
            {"from": edge[0], "to": edge[1], "kind": kind, "task": edge[3], "state": str(state or "")}
        )

    active_node = activity.get("node")
    if active_node and active_node != "orchestrator":
        add("orchestrator", active_node, "delegates", activity.get("task"), activity.get("phase"))
    for task in raw_state.get("tasks", []):
        if isinstance(task, dict) and task.get("status") not in {"done", "escalated"}:
            add("orchestrator", task.get("node"), "delegates", task.get("id"), task.get("status"))
    for recovery in raw_state.get("recoveries", []):
        if isinstance(recovery, dict) and recovery.get("status") in {"assigned", "corrected", "needs_input"}:
            add(
                recovery.get("failed_node"),
                recovery.get("owner"),
                "asks_help",
                recovery.get("task_id") or recovery.get("id"),
                recovery.get("status"),
            )
    return links


def build(workdir: Path | None, raw_state: dict[str, Any], activity: dict[str, Any]) -> dict[str, Any]:
    """Construye una vista acotada; nunca expone system prompts ni archivos completos."""
    events = _history_events(raw_state) + _conversation_events(workdir)
    live = _live_event(activity)
    if live:
        events.append(live)
    for index, event in enumerate(events):
        event.setdefault("sort_time", index)
    events.sort(key=lambda event: (event.get("sort_time") or 0, str(event.get("time") or "")))
    events = events[-MAX_EVENTS:]
    return {
        "events": events,
        "relationships": _relationships(raw_state, activity),
        "stats": {
            "decisions": sum(event.get("kind") == "decision" for event in events),
            "delegations": sum(event.get("kind") == "delegation" for event in events),
            "conversations": sum(event.get("kind") == "conversation" for event in events),
            "events": len(events),
        },
        "limits": {"events": MAX_EVENTS, "excerpt_chars": MAX_EXCERPT},
    }

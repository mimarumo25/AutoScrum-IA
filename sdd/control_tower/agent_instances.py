"""Proyeccion de identidades de ejecucion para API y panel."""


def project(sprint: list[dict], summaries: list[dict]) -> list[dict]:
    """Une tareas y journals sin confundir rol configurable con instancia."""
    observed = {str(item.get("task_id")): item for item in summaries}
    state_by_status = {
        "pending": "queued",
        "delegated": "delegated",
        "blocked": "waiting",
        "needs_input": "waiting",
        "escalated": "error",
        "done": "completed",
    }
    result = []
    for task in sprint:
        agent = task.get("agent") or {}
        if not agent.get("id"):
            continue
        summary = observed.get(str(task.get("id")), {})
        status = str(task.get("status") or summary.get("status") or "pending")
        result.append({
            "id": str(agent["id"]),
            "name": str(agent.get("name") or agent["id"]),
            "role": str(agent.get("role") or task.get("node") or ""),
            "task_id": str(task.get("id") or ""),
            "task_title": str(task.get("title") or ""),
            "parent_id": agent.get("parent_id"),
            "parent_task_id": task.get("parent_task_id"),
            "depth": int(agent.get("depth", task.get("depth", 0))),
            "lineage": list(agent.get("lineage") or []),
            "state": state_by_status.get(status, status),
            "task_status": status,
            "child_ids": list(task.get("child_ids") or []),
            "provider": summary.get("provider", ""),
            "model": summary.get("model", ""),
            "tier": summary.get("tier", ""),
            "current_task": str(task.get("id") or ""),
        })
    return result

"""Runner concurrente alrededor de los gates inmutables de `gates/`.

G7 conserva prioridad absoluta. Los demas G* son procesos de solo lectura y se
ejecutan concurrentemente; los R* siguen al final y solo sobre verde completo.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import metrics

GATES = Path(__file__).resolve().parent / "gates"
sys.path.insert(0, str(GATES))
from run_gates import gates_for, load_registry  # noqa: E402


def _task(workdir: str) -> dict[str, object]:
    path = Path(workdir) / ".agent/current_task.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _execute_gate(gate: dict[str, object], node_id: str,
                  workdir: str) -> dict[str, object]:
    cmd = str(gate["cmd"]).format(
        py=shlex.quote(sys.executable), workdir=Path(workdir).as_posix(),
        gates=GATES.as_posix(), root=GATES.parent.as_posix(), node=node_id)
    task_id = str(_task(workdir).get("id", ""))
    env = os.environ.copy()
    env.update(SDD_METRICS_WORKDIR=workdir,
               SDD_METRICS_OPERATION="review_llm" if str(gate["id"]).startswith("R") else "gate_tool",
               SDD_METRICS_NODE=node_id, SDD_METRICS_TASK=task_id)
    if str(gate["id"]).startswith("R") and env.get("SDD_REVIEW_MODEL"):
        env["SDD_MODEL"] = env["SDD_REVIEW_MODEL"]
    started = time.perf_counter()
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True, env=env)
    try:
        findings = json.loads(proc.stdout or "{}").get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        findings = [{
            "file": str(gate["cmd"]).split()[1], "line": 0,
            "rule": "gate-roto",
            "evidence": (proc.stderr or proc.stdout)[-300:].strip(),
        }]
    prefix = Path(workdir).as_posix().rstrip("/") + "/"
    for finding in findings:
        finding["file"] = str(finding["file"]).replace("\\", "/").replace(prefix, "")
    report = {
        "gate_id": gate["id"], "name": gate["name"], "node": node_id,
        "status": "pass" if not findings else "fail",
        "default_owner": gate["default_owner"],
        "route_by": gate.get("route_by", "path"), "findings": findings,
    }
    metrics.record(
        workdir, "gate_process",
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        gate=gate["id"], node=node_id, task=task_id,
        returncode=proc.returncode, findings=len(findings), cache_hit=False)
    return report


def _review_digest(gate: dict[str, object], node_id: str,
                   workdir: str, pipeline: dict[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update(str(gate).encode("utf-8"))
    prompt = GATES.parent / "agents/reviewer.md"
    if prompt.exists():
        digest.update(prompt.read_bytes())
    task = _task(workdir)
    digest.update(json.dumps(task, sort_keys=True).encode("utf-8"))
    node = next(item for item in pipeline["node"] if item["id"] == node_id)
    roots = task.get("deliverables") or node.get("writes", [])
    for raw in roots:
        target = Path(workdir) / str(raw)
        paths = list(target.rglob("*")) if target.is_dir() else [target]
        for path in sorted(item for item in paths if item.is_file()):
            digest.update(path.relative_to(workdir).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_cached(gate: dict[str, object], node_id: str, workdir: str,
                pipeline: dict[str, object]) -> dict[str, object]:
    if not str(gate["id"]).startswith("R"):
        return _execute_gate(gate, node_id, workdir)
    digest = _review_digest(gate, node_id, workdir, pipeline)
    task_id = str(_task(workdir).get("id", "linear"))
    path = (Path(workdir) / ".agent/review-cache" /
            f"{node_id}.{task_id}.{gate['id']}.{digest}.json")
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
        metrics.record(workdir, "gate_process", duration_ms=0, gate=gate["id"],
                       node=node_id, task=task_id, cache_hit=True)
        return report
    report = _execute_gate(gate, node_id, workdir)
    if report["status"] == "pass":
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        pending.replace(path)
    return report


def run_node_gates(node_id: str, workdir: str,
                   pipeline: dict[str, object]) -> list[dict[str, object]]:
    """Mismo contrato publico del runner original, con concurrencia acotada."""
    registry = load_registry()
    gate_ids = gates_for(node_id, pipeline)
    reports_dir = Path(workdir) / ".agent/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []

    if "G7" in gate_ids:
        report = _run_cached(registry["G7"], node_id, workdir, pipeline)
        reports.append(report)
        _save_report(reports_dir, node_id, report)
        if report["status"] == "fail":
            return reports

    deterministic = [gate_id for gate_id in gate_ids
                     if gate_id != "G7" and not registry[gate_id].get("skip_if_prior_failed")]
    workers = max(1, int(pipeline["runtime"].get("gate_concurrency", 4)))
    completed: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(deterministic)))) as pool:
        futures = {pool.submit(_run_cached, registry[gate_id], node_id,
                               workdir, pipeline): gate_id
                   for gate_id in deterministic}
        for future in as_completed(futures):
            completed[futures[future]] = future.result()
    for gate_id in deterministic:
        report = completed[gate_id]
        reports.append(report)
        _save_report(reports_dir, node_id, report)

    for gate_id in gate_ids:
        gate = registry[gate_id]
        if not gate.get("skip_if_prior_failed"):
            continue
        if any(report["status"] == "fail" for report in reports):
            continue
        report = _run_cached(gate, node_id, workdir, pipeline)
        reports.append(report)
        _save_report(reports_dir, node_id, report)
    return reports


def _save_report(directory: Path, node_id: str, report: dict[str, object]) -> None:
    path = directory / f"{node_id}.{report['gate_id']}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

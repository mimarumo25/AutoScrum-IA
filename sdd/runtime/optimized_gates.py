"""Runner concurrente alrededor de los gates inmutables de `gates/`.

G7 conserva prioridad absoluta. Los demas G* son procesos de solo lectura y se
ejecutan concurrentemente; los R* siguen al final y solo sobre verde completo.
"""
import hashlib
import json
import os
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sdd.core import metrics, process_control

GATES = Path(__file__).resolve().parents[1] / "gates"
from sdd.gates.run_gates import gates_for, load_registry


def _task(workdir: str) -> dict[str, object]:
    path = Path(workdir) / ".agent/current_task.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _execute_gate(gate: dict[str, object], node_id: str,
                  workdir: str, pipeline: dict[str, object]) -> dict[str, object]:
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
    proc, timed_out = process_control.run_bounded(
        shlex.split(cmd), env=env,
        timeout_seconds_value=float(pipeline["runtime"]["gate_timeout_seconds"]))
    try:
        payload = json.loads(proc.stdout or "")
        if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
            raise ValueError("contrato findings ausente")
        findings = payload["findings"]
    except (json.JSONDecodeError, AttributeError, ValueError):
        findings = [{
            "file": str(gate["cmd"]).split()[1], "line": 0,
            "rule": "gate-timeout" if timed_out else "gate-roto",
            "evidence": ("gate excedio el tiempo configurado" if timed_out else
                         (proc.stderr or proc.stdout)[-300:].strip()),
        }]
    if proc.returncode != 0 and not findings:
        findings = [{
            "file": str(gate["cmd"]).split()[1], "line": 0,
            "rule": "gate-timeout" if timed_out else "gate-roto",
            "evidence": "gate excedio el tiempo configurado" if timed_out else
                        (proc.stderr or proc.stdout or "salida no valida")[-300:].strip(),
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


def _safe_run(gate: dict[str, object], node_id: str, workdir: str,
              pipeline: dict[str, object]) -> dict[str, object]:
    try:
        return _run_cached(gate, node_id, workdir, pipeline)
    except Exception as error:  # noqa: BLE001 - un evaluador roto es fail, no pass
        return {
            "gate_id": gate["id"], "name": gate["name"], "node": node_id,
            "status": "fail", "default_owner": gate["default_owner"],
            "route_by": gate.get("route_by", "path"),
            "findings": [{
                "file": str(gate.get("cmd", "gate")), "line": 0,
                "rule": "gate-excepcion",
                "evidence": f"{type(error).__name__}: {error}"[:300],
            }],
        }


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
        return _execute_gate(gate, node_id, workdir, pipeline)
    digest = _review_digest(gate, node_id, workdir, pipeline)
    task_id = str(_task(workdir).get("id", "linear"))
    path = (Path(workdir) / ".agent/review-cache" /
            f"{node_id}.{task_id}.{gate['id']}.{digest}.json")
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
        metrics.record(workdir, "gate_process", duration_ms=0, gate=gate["id"],
                       node=node_id, task=task_id, cache_hit=True)
        return report
    report = _execute_gate(gate, node_id, workdir, pipeline)
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
        report = _safe_run(registry["G7"], node_id, workdir, pipeline)
        reports.append(report)
        _save_report(reports_dir, node_id, report)
        if report["status"] == "fail":
            return reports

    deterministic = [gate_id for gate_id in gate_ids
                     if gate_id != "G7" and not registry[gate_id].get("skip_if_prior_failed")]
    workers = max(1, int(pipeline["runtime"].get("gate_concurrency", 4)))
    completed: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(deterministic)))) as pool:
        futures = {pool.submit(_safe_run, registry[gate_id], node_id,
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
        report = _safe_run(gate, node_id, workdir, pipeline)
        reports.append(report)
        _save_report(reports_dir, node_id, report)
    return reports


_HISTORY_LOCK = threading.Lock()


def _save_report(directory: Path, node_id: str, report: dict[str, object]) -> None:
    path = directory / f"{node_id}.{report['gate_id']}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _append_history(directory, node_id, report)


def _append_history(directory: Path, node_id: str,
                    report: dict[str, object]) -> None:
    """Anade el intento a un journal append-only junto al reporte canonico.

    El JSON canonico se sobrescribe en cada intento: el intento 1 desaparecia
    cuando corria el 2, asi que no habia forma de medir la tasa de PRIMERA
    pasada de un gate — justo la metrica que dice si un cambio mejora algo.

    Los gates corren en un ThreadPoolExecutor, asi que un write_text concurrente
    intercalaria lineas; se escribe con O_APPEND bajo lock, como en metrics.
    """
    findings = report.get("findings") or []
    line = json.dumps({
        "at": time.time(),
        "gate_id": report.get("gate_id"),
        "status": report.get("status"),
        "rules": [str(item.get("rule", "")) for item in findings],
    }, ensure_ascii=False, separators=(",", ":")) + "\n"
    path = directory / f"{node_id}.{report['gate_id']}.history.jsonl"
    with _HISTORY_LOCK:
        handle = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(handle, line.encode("utf-8"))
        finally:
            os.close(handle)

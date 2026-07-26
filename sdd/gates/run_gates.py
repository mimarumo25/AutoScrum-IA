#!/usr/bin/env python3
"""Ejecuta los gates de un nodo y devuelve reportes normalizados.

Un gate es codigo determinista. No tiene juicio, no negocia umbrales y no
propone soluciones: emite hallazgos con file:line y evidencia.
"""
import argparse
import json
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_registry():
    data = tomllib.loads((ROOT / "gates/registry.toml").read_text())
    return {g["id"]: g for g in data["gate"]}


def gates_for(node_id, pipeline):
    node = next(n for n in pipeline["node"] if n["id"] == node_id)
    return node.get("gates", [])


def run_gate(gate, node_id, workdir):
    # as_posix() en las rutas + shlex.quote en el interprete: shlex.split (modo
    # posix) no destroza backslashes de Windows si no los hay. Portable en win/*nix.
    cmd = gate["cmd"].format(
        py=shlex.quote(sys.executable),
        workdir=Path(workdir).as_posix(),
        gates=(ROOT / "gates").as_posix(),
        root=ROOT.as_posix(),
        node=node_id,
    )
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    try:
        findings = json.loads(proc.stdout or "{}").get("findings", [])
    except json.JSONDecodeError:
        findings = [{"file": gate["cmd"].split()[1], "line": 0, "rule": "gate-roto",
                     "evidence": (proc.stderr or proc.stdout)[-300:].strip()}]
    wd_prefix = Path(workdir).as_posix().rstrip("/") + "/"
    for f in findings:
        f["file"] = str(f["file"]).replace("\\", "/").replace(wd_prefix, "")
    return {
        "gate_id": gate["id"],
        "name": gate["name"],
        "node": node_id,
        "status": "pass" if not findings else "fail",
        "default_owner": gate["default_owner"],
        "route_by": gate.get("route_by", "path"),
        "findings": findings,
    }


def run_node_gates(node_id, workdir, pipeline):
    registry = load_registry()
    reports_dir = Path(workdir) / ".agent/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for gid in gates_for(node_id, pipeline):
        gate = registry[gid]
        # Los gates caros (los R*, que llaman a un modelo) no se ejecutan sobre un
        # artefacto que los deterministas ya reprobaron: el nodo va a reescribirlo
        # de todas formas y la critica se tirara a la basura.
        if gate.get("skip_if_prior_failed") and any(r["status"] == "fail" for r in reports):
            continue
        report = run_gate(gate, node_id, workdir)
        (reports_dir / f"{node_id}.{gid}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        reports.append(report)
        # G7 (integridad) corta el ciclo: no se evalua nada mas hasta revertir.
        if gid == "G7" and report["status"] == "fail":
            break
    return reports


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--workdir", required=True)
    a = p.parse_args()
    pipeline = tomllib.loads((ROOT / "pipeline.toml").read_text())
    reports = run_node_gates(a.node, a.workdir, pipeline)
    print(json.dumps(reports, indent=2, ensure_ascii=False))
    sys.exit(1 if any(r["status"] == "fail" for r in reports) else 0)

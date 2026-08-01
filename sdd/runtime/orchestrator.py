#!/usr/bin/env python3
"""Orquestador SDD.

Modelo: maquina de estados sobre `repo-as-state`. Los agentes no se pasan
contexto: leen y escriben archivos versionados. El orquestador transporta
punteros y decisiones de ruta, nada mas.

Dos fases:
  1. Lineal — product -> architect -> planner -> gate humano. Produce la
     especificacion y el plan.
  2. Bucle de tareas — el plan se ejecuta tarea a tarea, respetando dependencias.
     Un defecto que pertenece a otro nodo no es un reintento: es una tarea nueva
     para su dueno, y la tarea que lo destapo queda bloqueada tras ella.

Reglas de honestidad (el fallo que originó esta version): un agente que sale con
codigo != 0 NO avanza, y un commit que no commitea NO se reporta como aprobado.

Uso:
  python orchestrator.py --workdir /ruta/al/repo --simulate
  python orchestrator.py --workdir /ruta/al/repo --from dev_backend
"""
import argparse
import os
import shlex
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdd.core import chronicle, lifecycle, metrics, process_control, run_lease  # noqa: E402
from sdd.core.execution_journal import invoke_once  # noqa: E402
from sdd.core.run_lease import RunBusyError  # noqa: E402
from sdd.presentation.report import write_run_report  # noqa: E402
from sdd.runtime import graph_runtime, plan_analysis, taskqueue  # noqa: E402
from sdd.runtime.optimized_gates import run_node_gates  # noqa: E402
from sdd.runtime import workflow_contracts  # noqa: E402
from sdd.runtime.workflow_contracts import Evaluation  # noqa: E402
from sdd.runtime.artifact_integrity import allowed_roots, content_hash  # noqa: E402
from sdd.runtime.run_state import load_state, save, token_usage  # noqa: E402
from sdd.runtime.workflow_defects import (  # noqa: E402
    resolve_linear_recoveries as _resolve_linear_recoveries,
)

HumanDecision = workflow_contracts.HumanDecision

ROOT = Path(__file__).resolve().parents[1]
LOOP = "task_loop"

# La evidencia de un defecto en un repo real puede traer caracteres no-cp1252;
# forzar UTF-8 evita que el logging del orquestador reviente en consolas Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# --- Supervisor: router puro, sin juicio propio ----------------------------

def route(reports, pipeline):
    """Devuelve (owner_node, gate_id, findings) del primer fallo enrutable.

    Prioridad 1: violacion de propiedad (G7). Prioridad 2: el gate declara que el
    dueno es quien corrio (route_by=node) o uno fijo (route_by=gate). Prioridad 3:
    el dueno del path del hallazgo. No hay heuristicas semanticas.
    """
    ownership = [(n["id"], w) for n in pipeline["node"] for w in n.get("writes", [])]
    ownership.sort(key=lambda x: -len(x[1]))  # match mas especifico primero
    for report in reports:
        if report["status"] == "pass":
            continue
        if report["gate_id"] == "G7" or report.get("route_by") == "node":
            return report["node"], report["gate_id"], report["findings"]
        if report.get("route_by") == "gate":
            return report["default_owner"], report["gate_id"], report["findings"]
        for f in report["findings"]:
            for node_id, prefix in ownership:
                if prefix and f["file"].startswith(prefix):
                    return node_id, report["gate_id"], report["findings"]
        return report["default_owner"], report["gate_id"], report["findings"]
    return None, None, []


def git(workdir, *args):
    return process_control.run_git(workdir, *args, text=True)


def log(state, event, **kw):
    entry = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    state["history"].append(entry)
    detail = " ".join(f"{k}={v}" for k, v in kw.items())
    print(f"  [{event:<18}] {detail}", flush=True)


def dirty_paths(workdir):
    """Paths con cambios sin commitear, tal como los ve git."""
    out = git(workdir, "status", "--porcelain", "-uall").stdout.splitlines()
    return [l[3:].strip().strip('"') for l in out if l[3:].strip()]


def write_baseline(workdir):
    """Congela lo que ya estaba sucio antes de que el agente escriba nada.

    Lo consume G7 para no imputarle a este nodo el trabajo a medio terminar de
    otra tarea que quedo en rojo y por tanto sin commitear.
    """
    path = Path(workdir) / ".agent/baseline.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(dirty_paths(workdir)), encoding="utf-8")


def commit(workdir, message, allowed):
    """Commitea SOLO lo que este nodo posee. Devuelve (hubo_commit, resumen).

    Dos fallos que arregla de una vez:
      - el orquestador imprimia 'APROBADO accion=commit' sin mirar el resultado de
        git; dev_backend no escribio nada, `git commit` fallo con 'nothing to
        commit', y el reporte final declaro 5 commits y exito;
      - `git add -A` arrastraba al commit de un nodo el trabajo sin terminar de
        otro. Cada commit debe contener lo de su dueno y nada mas.
    """
    if not allowed:
        return False, "el nodo no declara paths de escritura"
    for path in allowed:
        git(workdir, "add", "-A", "--", path)   # falla si el path no existe: da igual
    staged = git(workdir, "diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        return False, "sin cambios propios en el arbol"
    proc = git(workdir, "commit", "-m", message)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[-1][:160] if detail else "git commit fallo")
    return True, message


# --- Ejecucion de un nodo agente -------------------------------------------

def invoke_agent(node, workdir, cfg, simulate, task, visit_id=""):
    """Devuelve (returncode, detalle). rc 3 = el agente se declara bloqueado."""
    if node.get("type") == "human":
        return 0, "human"
    template = cfg["runtime"]["simulate_cmd" if simulate else "agent_cmd"]
    cmd = template.format(py=shlex.quote(sys.executable), root=ROOT.as_posix(),
                          node=node["id"], workdir=Path(workdir).as_posix(),
                          prompt=(ROOT / node["prompt"]).as_posix(), task=task)
    task_id = str(task.get("id", "")) if isinstance(task, dict) else ""
    env = os.environ.copy()
    env.update(SDD_METRICS_WORKDIR=str(workdir),
               SDD_METRICS_OPERATION="agent_llm",
               SDD_METRICS_NODE=str(node["id"]),
               SDD_METRICS_TASK=task_id,
               SDD_VISIT_ID=visit_id)
    started = time.perf_counter()
    proc, timed_out = process_control.run_bounded(
        shlex.split(cmd), cwd=workdir, env=env,
        timeout_seconds_value=float(cfg["runtime"]["agent_timeout_seconds"]))
    metrics.record(workdir, "agent_process",
                   duration_ms=round((time.perf_counter() - started) * 1000, 3),
                   node=node["id"], task=task_id,
                   simulate=bool(simulate), returncode=proc.returncode,
                   timed_out=timed_out)
    stdout_text = (proc.stdout or "").strip()
    stderr_text = (proc.stderr or "").strip()
    err = (stderr_text or ("agente excedio el tiempo configurado" if timed_out else
                           stdout_text) or "").strip().replace("\n", " ")
    if visit_id:
        chronicle.archive_agent_call(
            workdir, visit_id, node["id"], task_id or None,
            system_prompt="", user_prompt="", response_text="",
            stdout_text=stdout_text, stderr_text=stderr_text,
            returncode=proc.returncode,
            files_written=[], files_skipped=[],
        )
    return proc.returncode, err[-220:]


def refund_attempts(state, key, budget):
    """Devuelve el presupuesto de reintentos de un gate que acaba de pasar.

    Un gate que pasa no debe arrastrar rencor: si vuelve a fallar mas tarde por
    algo distinto, merece presupuesto nuevo. Pero un veredicto que oscila
    convertia eso en una via de escape del presupuesto, porque cada `pass`
    intermedio lo devolvia ENTERO y la unidad podia ciclar sin converger. Medido
    en demo-fastapi-fullstack: `attempts` registraba T-003:G9=5 frente a 56
    ejecuciones reales de G9, y solo los topes globales (max_agent_calls,
    max_wall_minutes) acotaron la corrida.

    Solo se CUENTAN los reembolsos que de verdad rescataron a la unidad: los que
    llegan cuando ya estaba al borde de escalar. Un gate que falla una vez y pasa
    no consume cuota, porque nunca estuvo en peligro. Sin esa distincion el tope
    se agotaba en el camino feliz: en el demo `product:G1` llegaba a 2 de 2
    reembolsos solo por fallar y arreglarse dos veces, y un tercer ciclo
    legitimo habria escalado sin motivo.

    Agotada la cuota, el contador deja de refinanciarse y la escalacion normal
    ocurre. El tope sale de `max_retries_per_gate`: no se anade otra perilla que
    pudiera quedar desincronizada de la primera.
    """
    attempts = state.setdefault("attempts", {})
    if key not in attempts:
        return False        # paso a la primera: no hay nada que devolver
    limit = int(budget["max_retries_per_gate"])
    refunds = state.setdefault("gate_refunds", {})
    al_borde = int(attempts.get(key, 0)) >= limit
    if al_borde and int(refunds.get(key, 0)) >= limit:
        return False        # ya se le rescato demasiadas veces; que escale
    if al_borde:
        refunds[key] = int(refunds.get(key, 0)) + 1
    attempts.pop(key, None)
    return True


def agent_failure_report(node_id, rc, detail):
    """Un agente caido se trata como un gate rojo del propio nodo: mismo camino
    de defecto, mismo contador de reintentos, misma escalacion. No hay atajo."""
    rule = "agente-bloqueado" if rc == 3 else "agente-fallido"
    return [{
        "gate_id": "G-AGENT", "name": "ejecucion del agente", "node": node_id,
        "status": "fail", "default_owner": node_id, "route_by": "node",
        "findings": [{"file": f"agents/{node_id}.md", "line": 0, "rule": rule,
                      "evidence": detail or f"exit={rc} sin salida"}],
    }]


# --- Bucle de tareas --------------------------------------------------------

def enter_task_loop(state, workdir, log_fn):
    """Elige la siguiente tarea ejecutable. Devuelve el nodo destino o None."""
    if not state["tasks"]:
        try:
            state["tasks"] = taskqueue.load_plan(workdir)
        except taskqueue.PlanError as e:
            state["status"] = "escalated"
            log_fn(state, "ESCALATE_HUMAN", motivo=str(e))
            return None
        done, total = taskqueue.progress(state["tasks"])
        log_fn(state, "PLAN", tareas=total, nodos=len({t["node"] for t in state["tasks"]}))

    task = taskqueue.next_runnable(state["tasks"])
    if task is None:
        rest = taskqueue.pending(state["tasks"])
        if not rest:
            state["status"] = "done"
            taskqueue.clear_current(workdir)
            return None
        # Ninguna rama puede avanzar y quedan tareas sin cerrar: el sprint esta en
        # interbloqueo, no en pausa. 'waiting_human' saldria con codigo 0 y lo
        # reportaria como exito.
        state["status"] = "escalated"
        taskqueue.clear_current(workdir)
        blocked = ", ".join(f"{t['id']}({t['status']})" for t in rest[:6])
        log_fn(state, "RAMAS_EN_ESPERA", pendientes=len(rest),
               motivo=f"no hay tareas ejecutables; esperan correcciones: {blocked}")
        log_fn(state, "ESCALATE_HUMAN", pendientes=len(rest),
               motivo="ninguna rama puede avanzar sin intervencion")
        return None

    state["current_task"] = task["id"]
    taskqueue.publish_current(workdir, task)
    lifecycle.started(workdir, task["id"], task["node"])
    done, total = taskqueue.progress(state["tasks"])
    log_fn(state, "TAREA", id=task["id"], nodo=task["node"],
           avance=f"{done}/{total}", titulo=task["title"][:60])
    return task["node"]


def approve(state, workdir, node, task, log_fn):
    """Aplica efectos de aprobacion y deja el destino para una arista."""
    changed, detail = commit(workdir, taskqueue.commit_message(node["id"], task),
                             node.get("writes", []))
    log_fn(state, "APROBADO", nodo=node["id"],
           accion="commit" if changed else "sin-commit",
           detalle=detail if not changed else task["id"] if task else node["id"])
    if task is not None:
        taskqueue.mark_done(state["tasks"], task["id"], workdir)
        state["current_task"] = None
        taskqueue.clear_current(workdir)
        state["approval_next"] = LOOP
        return
    return_target = _resolve_linear_recoveries(
        state, workdir, node["id"], node["next"], log_fn)
    if return_target:
        state["approval_next"] = return_target
        return
    nxt = state.pop("resume_at", None) or node["next"]
    if nxt == node["id"]:
        nxt = node["next"]
    state["approval_next"] = nxt


# --- Ciclo principal --------------------------------------------------------

def _active_unit(state, nodes):
    node = nodes[state["cursor"]]
    # La tarea activa manda, venga del plan o de un defecto. Un defecto puede
    # pertenecer a un nodo que no es de tarea (el arquitecto y un toolchain roto,
    # por ejemplo); si aqui se ignorara, esa tarea nunca se cerraria y el bucle
    # volveria a elegirla para siempre.
    task = taskqueue.by_id(state["tasks"], state.get("current_task"))
    if task is not None and task["node"] != node["id"]:
        task = None
    return node, task


def _solution(state, args, node, task):
    allowed = allowed_roots(node, task)
    artifacts = [path for path in dirty_paths(args.workdir)
                 if any(path == str(root).rstrip("/") or
                        path.startswith(str(root).rstrip("/") + "/") or
                        (not str(root).endswith("/") and
                         path.startswith(str(root) + "."))
                        for root in allowed)]
    return {
        "kind": "worktree" if task else "artifacts",
        "worktree": str(args.workdir) if task else None,
        "artifacts": artifacts,
        "node": node["id"],
        "task_id": task["id"] if task else None,
    }


def generate(state, args, cfg, nodes, _auto_human=False):
    """Ejecuta solo al productor y registra la solucion materializada."""
    node, task = _active_unit(state, nodes)
    label = f"{node['id']} · {task['id']}" if task else node["id"]
    print(f"\n>> nodo {label}", flush=True)

    if state["agent_calls"] >= cfg["budget"]["max_agent_calls"]:
        state["status"] = "escalated"
        log(state, "PRESUPUESTO", motivo="max_agent_calls agotado")
        return

    state["agent_calls"] += 1
    log(state, "AGENTE_INICIO", nodo=node["id"],
        tarea=task["id"] if task else "-", llamada=state["agent_calls"])
    write_baseline(args.workdir)
    rc, detail = invoke_once(
        args.workdir,
        getattr(args, "visit_id", None),
        lambda: invoke_agent(node, args.workdir, cfg, args.simulate, args.task,
                             str(getattr(args, "visit_id", ""))),
    )
    log(state, "AGENTE", nodo=node["id"], tarea=task["id"] if task else "-",
        resultado="human" if detail == "human" else f"exit={rc}"
                  + (f" · {detail}" if rc != 0 and detail else ""))
    unit_id = f"{node['id']}:{task['id'] if task else 'linear'}"
    generation = {
        "unit_id": unit_id, "node": node["id"],
        "task_id": task["id"] if task else None,
        "returncode": rc, "detail": detail,
        "solution": _solution(state, args, node, task),
    }
    state["generation"] = generation
    state.setdefault("iterations", []).append({
        "unit_id": unit_id, "stage": "generation",
        "attempt": int(state.get("retry_count", 0)) + 1,
        "solution": generation["solution"],
        "feedback": str(state.get("feedback", "")),
    })


def evaluate(state, args, cfg, nodes, _auto_human=False):
    """Ejecuta gates/revisor y produce una Evaluation Pydantic serializable."""
    node, task = _active_unit(state, nodes)
    generation = dict(state.get("generation") or {})
    rc = int(generation.get("returncode", 1))
    detail = str(generation.get("detail", "generacion ausente"))

    if rc != 0:
        reports = agent_failure_report(node["id"], rc, detail)
    else:
        log(state, "GATES_INICIO", nodo=node["id"],
            tarea=task["id"] if task else "-")
        reports = run_node_gates(node["id"], args.workdir, cfg)
        for r in reports:
            log(state, "GATE " + r["gate_id"], estado=r["status"], hallazgos=len(r["findings"]))
            if r["status"] == "pass":
                key = (f"{task['id']}:{r['gate_id']}" if task else
                       f"{node['id']}:{r['gate_id']}")
                if not refund_attempts(state, key, cfg["budget"]) \
                        and key in state.get("attempts", {}):
                    log(state, "REEMBOLSO_AGOTADO", unidad=key,
                        motivo="el gate ya oscilo demasiadas veces; el contador "
                               "de reintentos deja de refinanciarse")
            if task:
                lifecycle.gate_result(args.workdir, task["id"],
                                      r["gate_id"], r["status"] == "pass",
                                      len(r["findings"]))
            visit_id = str(getattr(args, "visit_id", ""))
            if visit_id:
                chronicle.archive_gate_result(
                    args.workdir, visit_id, r["gate_id"],
                    r["status"], r["findings"])

    owner, gate_id, findings = route(reports, cfg)
    feedback = "\n".join(
        f"{item['rule']}: {item['evidence']}" for item in findings)
    evaluation = Evaluation(
        unit_id=str(generation.get("unit_id") or
                    f"{node['id']}:{task['id'] if task else 'linear'}"),
        node=node["id"], task_id=task["id"] if task else None,
        approved=owner is None, gate_id=gate_id, owner=owner,
        feedback=feedback, findings=findings, reports=reports,
        solution=dict(generation.get("solution") or {}),
        content_roots=allowed_roots(node, task),
        content_hash=content_hash(args.workdir, allowed_roots(node, task)),
    )
    persisted = evaluation.model_dump(mode="json")
    state["evaluation"] = persisted
    state["feedback"] = evaluation.feedback
    state.setdefault("iterations", []).append({
        "unit_id": evaluation.unit_id, "stage": "evaluation",
        "attempt": int(state.get("retry_count", 0)) + 1,
        "approved": evaluation.approved, "feedback": evaluation.feedback,
        "findings": [item.model_dump(mode="json") for item in evaluation.findings],
    })
    log(state, "EVALUACION", unidad=evaluation.unit_id,
        estado="pass" if evaluation.approved else "fail",
        hallazgos=len(evaluation.findings))


def approve_unit(state, args, _cfg, nodes):
    """Efecto de la arista approve_unit; no clasifica ni selecciona rutas."""
    evaluation = Evaluation.model_validate(state.get("evaluation") or {})
    node = nodes[evaluation.node]
    task = taskqueue.by_id(state["tasks"], evaluation.task_id)
    if not evaluation.approved:
        raise ValueError("approve_unit requiere una evaluacion aprobada")
    if node["id"] == "planner":
        plan_analysis.log_plan(args.workdir, state, log)
        node = {**node, "next": nodes["human_gate"]["next"]}
    approve(state, args.workdir, node, task, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--task", default="Ejecutar el plan de spec/30_plan/tasks.yaml")
    ap.add_argument("--from", dest="start", default=None,
                    help="nodo donde empezar o reanudar fuera de una pausa HITL; "
                         "un interrupt humano exige --resume")
    ap.add_argument("--resume", action="store_true",
                    help="reanuda una corrida interrumpida o escalada desde donde "
                         "quedo (cursor guardado), con presupuesto de reintentos "
                         "fresco. No pierde el trabajo ya commiteado.")
    ap.add_argument("--simulate", action="store_true",
                    help="agentes falsos: prueba el plano de control sin gastar tokens")
    ap.add_argument("--auto-approve-human", action="store_true")
    ap.add_argument("--autonomous", action="store_true",
                     help="sin intervencion humana: auto-aprueba el gate y emite reporte final")
    ap.add_argument("--human-decision", choices=("accept", "reject"))
    ap.add_argument("--human-feedback", default="")
    args = ap.parse_args()
    if args.human_decision == "reject" and not args.human_feedback:
        ap.error("--human-feedback es obligatorio con --human-decision reject")
    # En modo autonomo el gate humano se auto-aprueba, pero solo se alcanza tras
    # product, architect y planner en verde: nunca firma sobre un plan invalido.
    auto_human = args.auto_approve_human or args.autonomous

    if args.simulate:
        # Los gates corren como subprocesos y heredan el entorno. Asi el revisor
        # (R1) sabe que debe usar su guion determinista en vez de llamar al modelo.
        os.environ["SDD_SIMULATE"] = "1"

    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
    try:
        run_lease.hold_until_exit(
            args.workdir, float(cfg["runtime"]["lease_wait_seconds"]))
    except RunBusyError as error:
        print(f"corrida rechazada: {error}", flush=True)
        return 2
    nodes = {n["id"]: n for n in cfg["node"]}
    existed = (Path(args.workdir) / ".agent/state.json").exists()
    state, spath = load_state(args.workdir, args.start or "product")
    if (args.resume and state.get("status") == "waiting_human"
            and args.human_decision is None):
        ap.error("--human-decision accept|reject es obligatorio para reanudar HITL")
    if args.start:
        if state.get("status") == "waiting_human":
            print("hay una decision HITL pendiente; usa --resume con "
                  "--human-decision accept|reject", flush=True)
            return 1
        # --from explicito reanuda: antes se ignoraba si ya habia state.json, asi
        # que salir del gate humano era imposible — volvia a pararse en el mismo sitio.
        state["cursor"], state["status"] = args.start, "running"
        state["attempts"] = {}
        state["resume_at"] = None
    elif args.resume:
        # Reanudar: retomar donde quedo sin perder el trabajo ya commiteado. Sirve
        # tanto para una corrida que se corto (proceso muerto, conexion caida:
        # estado 'running' rancio) como para una que escalo o quedo esperando al
        # humano. Se da presupuesto de reintentos fresco para que lo que fallo se
        # reintente en vez de re-escalar de inmediato; el cursor se conserva.
        if not existed:
            print("no hay corrida que reanudar en este proyecto; inicia una nueva.",
                  flush=True)
            return 1
        # graph_runtime reconstruye la intencion sobre el snapshot SQLite. Para
        # interrupt() solo transmite la decision, nunca esta proyeccion.
    elif state["status"] != "running":
        # Relanzar un proyecto ya terminado sin --from/--resume era un no-op que
        # reimprimia 'COMPLETADO' y salia con 0: exactamente el falso exito que este
        # sistema existe para impedir. Se rechaza y se dice como continuar.
        print(f"\nEste proyecto ya está en estado '{state['status']}' "
              f"(run_id {state.get('run_id', '?')}).", flush=True)
        print("No se relanza solo: elige una acción explícita:", flush=True)
        print("  --resume        continuar desde donde quedó (sin perder avances)", flush=True)
        print("  --from <nodo>   reanudar desde un nodo concreto", flush=True)
        print("  sdd clean       borrar el estado .agent/ y empezar de cero", flush=True)
        return 0 if state["status"] in ("done", "waiting_human") else 1

    state = graph_runtime.run_pipeline(
        state, spath, args, cfg, nodes, auto_human,
        generate, evaluate, approve_unit, log, save, token_usage, commit,
        resume_requested=args.resume)
    save(state, spath)
    done, total = taskqueue.progress(state["tasks"])
    print(f"\n== estado final: {state['status']} | llamadas a agente: "
          f"{state['agent_calls']} | tareas: {done}/{total}")
    if args.autonomous:
        write_run_report(state, args.workdir, args.task, git)
    return 0 if state["status"] in ("done", "waiting_human") else 1


if __name__ == "__main__":
    sys.exit(main())

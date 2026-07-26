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
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "gates"))
import taskqueue  # noqa: E402
import graph_runtime  # noqa: E402
import metrics  # noqa: E402
import plan_analysis  # noqa: E402
from execution_journal import invoke_once  # noqa: E402
from report import write_run_report  # noqa: E402
from optimized_gates import run_node_gates  # noqa: E402

ROOT = Path(__file__).resolve().parent
LOOP = "task_loop"

# Fallos que ningun agente puede arreglar escribiendo codigo: falta un binario,
# no hay red, la suite se colgo. Reintentar es quemar llamadas; se escala ya.
ENVIRONMENT_RULES = {"toolchain-no-disponible", "entorno-sin-red", "suite-colgada"}

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


# --- Estado -----------------------------------------------------------------

def load_state(workdir, start):
    path = Path(workdir) / ".agent/state.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), path
    state = {
        "run_id": (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
                   + uuid.uuid4().hex[:8]),
        "cursor": start,
        "status": "running",
        "attempts": {},
        "agent_calls": 0,
        "started_at": time.time(),
        "tasks": [],
        "current_task": None,
        "defect_seq": 0,
        "history": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    return state, path


def save(state, path):
    started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.tmp")
    pending.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)
    metrics.record(path.parent.parent, "state_projection",
                   duration_ms=round((time.perf_counter() - started) * 1000, 3),
                   bytes=path.stat().st_size)


def git(workdir, *args):
    return subprocess.run(["git", *args], cwd=workdir, capture_output=True, text=True)


def token_usage(workdir):
    """Suma .agent/usage.jsonl. Vacio en modo simulado (no hay tokens)."""
    path = Path(workdir) / ".agent/usage.jsonl"
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0,
             "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        for k in total:
            total[k] += int(rec.get(k, 0) or 0)
    return total


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

def invoke_agent(node, workdir, cfg, simulate, task):
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
               SDD_METRICS_TASK=task_id)
    started = time.perf_counter()
    proc = subprocess.run(shlex.split(cmd), cwd=workdir, capture_output=True,
                          text=True, env=env)
    metrics.record(workdir, "agent_process",
                   duration_ms=round((time.perf_counter() - started) * 1000, 3),
                   node=node["id"], task=task_id,
                   simulate=bool(simulate), returncode=proc.returncode)
    err = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
    return proc.returncode, err[-220:]


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
        state["status"] = "escalated"
        blocked = ", ".join(f"{t['id']}({t['status']})" for t in rest[:6])
        log_fn(state, "ESCALATE_HUMAN",
               motivo=f"ninguna tarea ejecutable y quedan pendientes: {blocked}")
        return None

    state["current_task"] = task["id"]
    taskqueue.publish_current(workdir, task)
    done, total = taskqueue.progress(state["tasks"])
    log_fn(state, "TAREA", id=task["id"], nodo=task["node"],
           avance=f"{done}/{total}", titulo=task["title"][:60])
    return task["node"]


def handle_defect(state, workdir, node, task, owner, gate_id, findings, budget, log_fn):
    """Contabiliza el defecto y decide: reintentar, delegar o escalar."""
    for f in findings[:5]:
        log_fn(state, "DEFECTO", gate=gate_id, owner=owner,
               ubicacion=f"{f['file']}:{f['line']}", regla=f["rule"], evidencia=f["evidence"])

    env_hit = next((f for f in findings if f["rule"] in ENVIRONMENT_RULES), None)
    if env_hit:
        state["status"] = "escalated"
        log_fn(state, "ESCALATE_HUMAN",
               motivo=f"{env_hit['rule']} — requiere intervencion en la maquina: "
                      f"{env_hit['evidence'][:160]}")
        return

    if gate_id == "G7":
        for f in findings:
            git(workdir, "checkout", "--", f["file"])
            git(workdir, "clean", "-fd", f["file"])
        log_fn(state, "REVERT", archivos=len(findings), motivo="violacion de propiedad")

    key = f"{task['id']}:{gate_id}" if task else f"{owner}:{gate_id}"
    state["attempts"][key] = state["attempts"].get(key, 0) + 1
    if state["attempts"][key] > budget["max_retries_per_gate"]:
        state["status"] = "escalated"
        log_fn(state, "ESCALATE_HUMAN",
               motivo=f"{key} fallo {state['attempts'][key]} veces")
        return

    if owner == node["id"]:                       # el dueno es quien corrio: reintenta
        state["cursor"] = node["id"]
        log_fn(state, "ENRUTADO", a=owner, intento=state["attempts"][key],
               reanuda_en=task["id"] if task else owner)
        return

    if task is None:                              # fase lineal: visita al dueno y vuelve
        state["resume_at"] = node["id"]
        state["cursor"] = owner
        log_fn(state, "ENRUTADO", a=owner, intento=state["attempts"][key],
               reanuda_en=node["id"])
        return

    # Fase de tareas: el defecto es de otro. Se convierte en trabajo suyo.
    if state["defect_seq"] >= budget.get("max_defect_tasks", 12):
        state["status"] = "escalated"
        log_fn(state, "ESCALATE_HUMAN", motivo="tope de tareas de defecto alcanzado")
        return
    state["defect_seq"] += 1
    defect = taskqueue.make_defect(state["tasks"], owner, gate_id, findings,
                                   task, state["defect_seq"])
    log_fn(state, "DEFECTO_TAREA", id=defect["id"], para=owner,
           bloquea=task["id"], gate=gate_id)
    state["cursor"] = LOOP


def approve(state, workdir, node, task, log_fn):
    """Cierra un nodo o una tarea en verde. Solo declara commit si lo hubo."""
    changed, detail = commit(workdir, taskqueue.commit_message(node["id"], task),
                             node.get("writes", []))
    log_fn(state, "APROBADO", nodo=node["id"],
           accion="commit" if changed else "sin-commit",
           detalle=detail if not changed else task["id"] if task else node["id"])
    if task is not None:
        taskqueue.mark_done(state["tasks"], task["id"])
        state["current_task"] = None
        taskqueue.clear_current(workdir)
        state["cursor"] = LOOP
        return
    nxt = state.pop("resume_at", None) or node["next"]
    if nxt == node["id"]:
        nxt = node["next"]
    if nxt == "done":
        state["status"] = "done"
    else:
        state["cursor"] = nxt


# --- Ciclo principal --------------------------------------------------------

def step(state, args, cfg, nodes, auto_human):
    """Una visita de nodo completa: agente -> gates -> ruta."""
    if state["cursor"] == LOOP:
        target = enter_task_loop(state, args.workdir, log)
        if target is None:
            return
        state["cursor"] = target
        return

    node = nodes[state["cursor"]]
    # La tarea activa manda, venga del plan o de un defecto. Un defecto puede
    # pertenecer a un nodo que no es de tarea (el arquitecto y un toolchain roto,
    # por ejemplo); si aqui se ignorara, esa tarea nunca se cerraria y el bucle
    # volveria a elegirla para siempre.
    task = taskqueue.by_id(state["tasks"], state.get("current_task"))
    if task is not None and task["node"] != node["id"]:
        task = None
    label = f"{node['id']} · {task['id']}" if task else node["id"]
    print(f"\n>> nodo {label}")

    if node.get("type") == "human" and not auto_human:
        state["status"] = "waiting_human"
        log(state, "GATE_HUMANO",
            accion=f"revisar spec/ y spec/30_plan/tasks.yaml, luego "
                   f"reanudar con --from {node['next']}")
        return

    if state["agent_calls"] >= cfg["budget"]["max_agent_calls"]:
        state["status"] = "escalated"
        log(state, "PRESUPUESTO", motivo="max_agent_calls agotado")
        return

    state["agent_calls"] += 1
    write_baseline(args.workdir)
    rc, detail = invoke_once(
        args.workdir,
        getattr(args, "visit_id", None),
        lambda: invoke_agent(node, args.workdir, cfg, args.simulate, args.task),
    )
    log(state, "AGENTE", nodo=node["id"], tarea=task["id"] if task else "-",
        resultado="human" if detail == "human" else f"exit={rc}"
                  + (f" · {detail}" if rc != 0 and detail else ""))

    if rc != 0:
        # Sin esta rama, un agente muerto pasaba por gates vacios y se daba por bueno.
        reports = agent_failure_report(node["id"], rc, detail)
    else:
        reports = run_node_gates(node["id"], args.workdir, cfg)
        for r in reports:
            log(state, "GATE " + r["gate_id"], estado=r["status"], hallazgos=len(r["findings"]))

    owner, gate_id, findings = route(reports, cfg)
    if owner is None:
        if node["id"] == "planner":
            plan_analysis.log_plan(args.workdir, state, log)
        approve(state, args.workdir, node, task, log)
    else:
        handle_defect(state, args.workdir, node, task, owner, gate_id,
                      findings, cfg["budget"], log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--task", default="Ejecutar el plan de spec/30_plan/tasks.yaml")
    ap.add_argument("--from", dest="start", default=None,
                    help="nodo donde empezar o reanudar; sobrescribe el cursor "
                         "guardado (asi se sale del gate humano: --from task_loop)")
    ap.add_argument("--resume", action="store_true",
                    help="reanuda una corrida interrumpida o escalada desde donde "
                         "quedo (cursor guardado), con presupuesto de reintentos "
                         "fresco. No pierde el trabajo ya commiteado.")
    ap.add_argument("--simulate", action="store_true",
                    help="agentes falsos: prueba el plano de control sin gastar tokens")
    ap.add_argument("--auto-approve-human", action="store_true")
    ap.add_argument("--autonomous", action="store_true",
                    help="sin intervencion humana: auto-aprueba el gate y emite reporte final")
    args = ap.parse_args()
    # En modo autonomo el gate humano se auto-aprueba, pero solo se alcanza tras
    # product, architect y planner en verde: nunca firma sobre un plan invalido.
    auto_human = args.auto_approve_human or args.autonomous

    if args.simulate:
        # Los gates corren como subprocesos y heredan el entorno. Asi el revisor
        # (R1) sabe que debe usar su guion determinista en vez de llamar al modelo.
        os.environ["SDD_SIMULATE"] = "1"

    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in cfg["node"]}
    existed = (Path(args.workdir) / ".agent/state.json").exists()
    state, spath = load_state(args.workdir, args.start or "product")
    if args.start:
        # --from explicito reanuda: antes se ignoraba si ya habia state.json, asi
        # que salir del gate humano era imposible — volvia a pararse en el mismo sitio.
        previous = state.get("status")
        state["cursor"], state["status"] = args.start, "running"
        state["attempts"] = {}
        state["resume_at"] = None
        if previous == "waiting_human" and args.start != "human_gate":
            state["human_approval"] = graph_runtime.approval_record(
                args.workdir, "cli --from", mode="explicit-node")
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
        prev = state["status"]
        state["status"] = "running"
        state["attempts"] = {}
        state["resume_at"] = None
        # Si el checkpoint esta en interrupt(), graph_runtime envia Command.resume
        # para que la firma humana quede dentro del historial durable del grafo.
        log(state, "REANUDADO", desde=state.get("cursor"), estado_previo=prev)
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
        step, log, save, token_usage, commit, resume_requested=args.resume)
    save(state, spath)
    done, total = taskqueue.progress(state["tasks"])
    print(f"\n== estado final: {state['status']} | llamadas a agente: "
          f"{state['agent_calls']} | tareas: {done}/{total}")
    if args.autonomous:
        write_run_report(state, args.workdir, args.task, git)
    return 0 if state["status"] in ("done", "waiting_human") else 1


if __name__ == "__main__":
    sys.exit(main())

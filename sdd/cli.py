#!/usr/bin/env python3
"""Runner multiplataforma equivalente al Makefile, para maquinas sin `make`
(p. ej. Windows). Usa el mismo interprete que lo ejecuta (sys.executable),
asi no depende de que exista `python3` en el PATH.

  python run.py demo                         # bucle completo, sin gastar tokens
  python run.py gates --node dev_backend --workdir ../mi-repo
  python run.py run   --workdir ../mi-repo --task "..."   # agentes reales
  python run.py resume --node dev_backend --workdir ../mi-repo
  python run.py clean --workdir ../demo-repo
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
import webbrowser
from pathlib import Path

import config
import providers
import report
import task_worktrees
import metrics
import process_control
import run_lease
import lifecycle
import chronicle

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "SDD_API_KEY"}
KEY_ENV.update({p: c["key_env"] for p, c in providers.OPENAI_PRESETS.items()})

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# La consola de Windows suele ser cp1252; forzamos UTF-8 para los glifos del visor.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


class C:
    """Colores ANSI, solo si stdout es una terminal real."""
    _on = sys.stdout.isatty()
    def _w(code):  # noqa: N805
        return (lambda s: f"\033[{code}m{s}\033[0m" if C._on else str(s))
    gray = _w("90"); green = _w("32"); red = _w("31")
    yellow = _w("33"); cyan = _w("36"); bold = _w("1")


def sh(*args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def git(workdir, *args):
    return process_control.run_git(workdir, *args, check=True)


def demo(a):
    wd = Path(a.workdir).resolve()
    if wd.exists():
        shutil.rmtree(wd, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))
    wd.mkdir(parents=True)
    git(wd, "init", "-q")
    git(wd, "config", "user.email", "sdd@local")
    git(wd, "config", "user.name", "sdd-pipeline")
    (wd / ".gitignore").write_text(config.GITIGNORE, encoding="utf-8")
    git(wd, "add", "-A")
    git(wd, "commit", "-qm", "init")
    return sh(PY, ROOT / "orchestrator.py", "--workdir", wd,
              "--simulate", "--autonomous").returncode


def gates(a):
    try:
        with run_lease.acquire(
                a.workdir, process_control.timeout_seconds("lease_wait_seconds")):
            result, _ = process_control.run_bounded(
                [PY, str(ROOT / "gates/run_gates.py"), "--node", a.node,
                 "--workdir", a.workdir],
                timeout_seconds_value=process_control.timeout_seconds(
                    "gate_timeout_seconds"))
            return result.returncode
    except run_lease.RunBusyError as error:
        print(f"gates rechazados: {error}")
        return 2


def _seed_repo(wd, intake_src):
    """Crea un repo objetivo limpio y siembra la idea en spec/00_intake.yaml."""
    wd.mkdir(parents=True, exist_ok=True)
    if not (wd / ".git").exists():
        git(wd, "init", "-q")
        git(wd, "config", "user.email", "sdd@local")
        git(wd, "config", "user.name", "sdd-pipeline")
        (wd / ".gitignore").write_text(config.GITIGNORE, encoding="utf-8")
    intake_dst = wd / "spec/00_intake.yaml"
    if not intake_dst.exists():
        src = Path(intake_src) if intake_src else (ROOT / "intake.yaml")
        if not src.exists():
            print(f"no encuentro la idea de entrada ({src}). Pasa --intake <archivo>.")
            return None
        intake_dst.parent.mkdir(parents=True, exist_ok=True)
        intake_dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        git(wd, "add", "-A")
        git(wd, "commit", "-qm", "chore: intake inicial")
    return wd


def doctor(a):
    import importlib
    prov = importlib.import_module("providers").describe()
    print("=== proveedor LLM configurado (modo real) ===")
    for k, v in prov.items():
        print(f"  {k:12}: {v}")
    ready = prov.get("key_present") and "error" not in prov
    print("  listo       :", "SI" if ready else "NO — define la API key de arriba")
    print("\ncambia de proveedor con:  SDD_PROVIDER=deepseek|qwen|glm|kimi|anthropic")
    return 0 if ready else 1


def config_cmd(a):
    c = config.masked()
    print("=== configuración (config.json) ===")
    for k in ("output_base", "theme", "provider", "model"):
        print(f"  {k:12}: {c.get(k) or '(vacío)'}")
    print("  keys        :", c.get("keys") or "(ninguna guardada)")
    print(f"  archivo     : {config.CONFIG_PATH}")
    return 0


def _apply_saved_config():
    """Rellena el entorno con la config guardada si no viene por variables."""
    cfg = config.load()
    prov = os.environ.get("SDD_PROVIDER") or cfg.get("provider") or "anthropic"
    os.environ.setdefault("SDD_PROVIDER", prov)
    keyenv = KEY_ENV.get(prov, "ANTHROPIC_API_KEY")
    if not os.environ.get(keyenv) and cfg["keys"].get(prov):
        os.environ[keyenv] = cfg["keys"][prov]
    if not os.environ.get("SDD_MODEL") and cfg.get("model"):
        os.environ["SDD_MODEL"] = cfg["model"]


def run(a):
    _apply_saved_config()
    prov = providers.describe()
    if not prov.get("key_present") or "error" in prov:
        print("no puedo arrancar el modo real: el proveedor no esta listo.\n")
        doctor(a)
        return 1
    if a.workdir:
        wd = Path(a.workdir)
    elif a.project:
        wd = config.resolve_output(a.project)
    else:
        print("indica dónde guardar: --project <nombre>  o  --workdir <ruta>")
        return 1
    print(f"proyecto en: {wd}")
    if _seed_repo(wd, a.intake) is None:
        return 1
    return subprocess.run([PY, ROOT / "orchestrator.py",
                           "--workdir", str(wd), "--task", a.task]).returncode


def resume(a):
    # Sin --node: reanuda desde el cursor guardado (--resume). Con --node: reanuda
    # desde ese nodo concreto (--from). Ambos conservan el trabajo ya commiteado.
    cmd = [PY, ROOT / "orchestrator.py", "--workdir", a.workdir, "--task", a.task]
    cmd += ["--from", a.node] if a.node else ["--resume"]
    if getattr(a, "autonomous", False):
        cmd.append("--autonomous")
    return subprocess.run(cmd).returncode


def clean(a):
    workdir = Path(a.workdir).resolve()
    try:
        with run_lease.acquire(
                workdir, process_control.timeout_seconds("lease_wait_seconds")):
            return _clean_workdir(workdir)
    except run_lease.RunBusyError as error:
        print(f"limpieza rechazada: {error}")
        return 2


def _clean_workdir(workdir: Path):
    agent_dir = workdir / ".agent"
    state_path = agent_dir / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for task in state.get("tasks", []):
                if isinstance(task, dict) and task.get("workspace"):
                    task_worktrees.cleanup(str(workdir), task)
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"no se puede limpiar con seguridad: {exc}")
            return 1
    process_control.run_git(workdir, "worktree", "prune")
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    print(f"limpiado {agent_dir}")
    return 0


def test(a):
    tests_dir = ROOT.parent / "tests"   # tests/ vive en la raíz del repo, no en el paquete
    return subprocess.run([PY, "-m", "unittest", "discover", "-s",
                           str(tests_dir), "-v"]).returncode


def serve(a):
    import server
    url = f"http://127.0.0.1:{a.port}"
    if not getattr(a, "no_open", False):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    server.serve(port=a.port)
    return 0


def _visits(history):
    """Segmenta el historial en visitas de nodo (cada una empieza en un AGENTE)."""
    out, cur = [], None
    for ev in history:
        if ev["event"] == "AGENTE":
            cur = {"nodo": ev.get("nodo", "?"), "tarea": ev.get("tarea", "-"), "events": []}
            out.append(cur)
        elif cur is not None:
            cur["events"].append(ev)
    return out


def view(a):
    wd = Path(a.workdir)
    sp = wd / ".agent/state.json"
    if not sp.exists():
        print(f"no hay corrida en {sp}\ncorre primero:  python run.py demo")
        return 1
    st = json.loads(sp.read_text(encoding="utf-8"))
    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text())
    html = report.render_html(st, [n["id"] for n in cfg["node"]], wd.resolve())
    out = wd / ".agent/report.html"
    out.write_text(html, encoding="utf-8")
    print(f"reporte HTML: {out.resolve()}")
    if not a.no_open:
        webbrowser.open(out.resolve().as_uri())
        print("abriendo en el navegador…")
    return 0


def show(a):
    wd = Path(a.workdir)
    sp = wd / ".agent/state.json"
    if not sp.exists():
        print(f"no hay corrida en {sp}\ncorre primero:  python run.py demo")
        return 1
    st = json.loads(sp.read_text(encoding="utf-8"))
    cfg = tomllib.loads((ROOT / "pipeline.toml").read_text())
    order = [n["id"] for n in cfg["node"]] + ["done"]
    approved = {e.get("nodo") for e in st["history"] if e["event"] == "APROBADO"}

    color = {"done": C.green, "escalated": C.red,
             "waiting_human": C.yellow, "running": C.cyan}.get(st["status"], C.bold)
    retries = sum(st.get("attempts", {}).values())
    engine = st.get("engine", "legacy")
    print(f"\n{C.bold('PIPELINE SDD')}  {C.gray(str(wd))}")
    print(f"runtime: {C.cyan(engine)} · checkpoints: "
          f"{C.gray(st.get('checkpoint_db', 'n/d'))}")
    print(f"estado: {color(st['status'])} · {st['agent_calls']} llamadas · "
          f"{retries} reintento(s)\n")

    # --- grafo lineal con marcas de estado ---
    marks = []
    for nid in order:
        if nid == "done":
            m = C.green("● done") if st["status"] == "done" else C.gray("○ done")
        elif nid in approved:
            m = C.green("✓ " + nid)
        elif nid == st.get("cursor") and st["status"] != "done":
            m = color("▶ " + nid)
        else:
            m = C.gray("○ " + nid)
        marks.append(m)
    print("  " + C.gray("  →  ").join(marks) + "\n")

    # --- linea de tiempo por visita ---
    print(C.bold("  TIMELINE"))
    for i, v in enumerate(_visits(st["history"]), 1):
        evs = v["events"]
        gates = [(e["event"].split()[1], e["estado"]) for e in evs
                 if e["event"].startswith("GATE ") and "estado" in e]
        gstr = " ".join((C.green if s == "pass" else C.red)(f"{g}{'✓' if s=='pass' else '✗'}")
                        for g, s in gates)
        outcome = next((e for e in evs if e["event"] in
                        ("APROBADO", "ENRUTADO", "ESCALATE_HUMAN")), None)
        idx = C.gray("{:02d}".format(i))
        name = C.bold("{:<13}".format(v["nodo"]))
        tail = C.gray("(aprobacion humana)") if v["nodo"] == "human_gate" else gstr
        etiqueta = C.cyan(" " + v["tarea"]) if v.get("tarea", "-") not in ("-", "", None) else ""
        print(f"  {idx} {name}{etiqueta} {tail}")
        for e in evs:
            if e["event"] == "DEFECTO":
                print(f"        {C.red('✗')} {e['gate']} {C.yellow(e['regla'])} "
                      f"{C.gray(e['ubicacion'])} — {e['evidencia']}")
            elif e["event"] == "REVERT":
                detail = "{} archivo(s) — {}".format(e["archivos"], e["motivo"])
                print(f"        {C.red('↩ revert')} {C.gray(detail)}")
            elif e["event"] == "DEFECTO_TAREA":
                detail = "para {} · bloquea {}".format(e["para"], e["bloquea"])
                print(f"        {C.yellow('⇢ tarea de defecto')} {C.bold(e['id'])} "
                      f"{C.gray(detail)}")
        if outcome and outcome["event"] == "ENRUTADO":
            intento = C.gray("(intento {})".format(outcome["intento"]))
            print(f"        {C.yellow('→ reintento')} {outcome['a']} {intento}")
        elif outcome and outcome["event"] == "APROBADO":
            print(f"        {C.green('✓ commit')}")
        elif outcome and outcome["event"] == "ESCALATE_HUMAN":
            print(f"        {C.red('⚠ ESCALATE_HUMAN')} {C.gray(outcome['motivo'])}")
    _print_review_backlog(wd)
    performance = metrics.summarize(wd)
    if performance:
        print(C.bold("  RENDIMIENTO"))
        for operation, values in sorted(
                performance.items(), key=lambda item: -float(item[1]["duration_ms"])):
            print(f"        {operation:<20} {int(values['count']):>3}x · "
                  f"{float(values['duration_ms']):>9.0f} ms")
    print()
    return 0


def _print_review_backlog(wd):
    """Muestra el backlog del revisor R1 (mejoras no bloqueantes) y sus avisos.
    Antes esto solo existia en REPORT.md; en la terminal quedaba invisible."""
    carpeta = Path(wd) / ".agent/review"
    if not carpeta.exists():
        return
    mejoras, notas = [], []
    for f in sorted(carpeta.glob("*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for m in st.get("mejoras", []):
            mejoras.append((f.stem, m))
        if st.get("nota"):
            notas.append((f.stem, st["nota"]))
    if not mejoras and not notas:
        return
    print("\n" + C.bold("  REVISOR R1") + C.gray("  (no bloqueante)"))
    for nodo, m in mejoras:
        print(f"        {C.yellow('◇')} {C.bold(nodo)} {m['rule']} "
              f"{C.gray('— ' + m['evidence'][:90])}")
    for nodo, nota in notas:
        print(f"        {C.gray('· ' + nodo + ': ' + nota)}")


def tasks(a):
    wd = Path(a.workdir)
    if a.task_id:
        _show_task_lifecycle(wd, a.task_id)
        return 0
    _list_task_summaries(wd, getattr(a, "verbose", False))
    return 0


def _list_task_summaries(wd, verbose):
    all_tasks = lifecycle.all_tasks(wd)
    if not all_tasks:
        print(f"{C.gray('no hay tareas registradas en')} {wd / '.agent/tasks/'}")
        return
    print(C.bold(f"\nTAREAS REGISTRADAS ({len(all_tasks)})"))
    print(C.gray("─" * 68))
    for t in sorted(all_tasks, key=lambda x: str(x.get("created_at", "")) or ""):
        tid = str(t.get("task_id", "?"))
        detail = lifecycle.summary(wd, tid)
        status = detail.get("status", "?")
        node = detail.get("node", "?")
        kind = detail.get("kind", "?")
        calls = detail.get("calls", 0)
        gates = detail.get("gates", {})
        gate_str = " ".join(
            (C.green if passed else C.red)(f"{g}{'✓' if passed else '✗'}")
            for g, passed in sorted(gates.items()))
        color_fn = {"done": C.green, "blocked": C.yellow, "escalated": C.red}.get(status, C.bold)
        status_str = color_fn(status)
        print(f"  {C.cyan(tid):<10} {status_str:<10} {C.gray(node):<14} "
              f"{C.gray(kind):<6} llamadas={calls:<2} {gate_str}")
        if verbose:
            events = lifecycle.read(wd, tid)
            for ev in events:
                ts = ev.get("t", "")[-8:]
                name = ev.get("event", "?")
                extra = ""
                if name == "gate_result":
                    extra = f"{ev.get('gate')} {'pass' if ev.get('status') == 'pass' else 'fail'}"
                elif name == "blocked":
                    extra = f"por {ev.get('blocked_by')} ({ev.get('gate')})"
                elif name == "integrated":
                    extra = f"{ev.get('result')} - {ev.get('detail', '')[:60]}"
                elif name == "escalated":
                    extra = str(ev.get("reason", ""))[:60]
                elif name == "retried":
                    extra = f"{ev.get('gate')} intento {ev.get('attempt')}"
                print(f"    {C.gray(ts)} {C.bold(name):<14} {C.gray(extra)}")
    print()


def chronicle_cmd(a):
    wd = Path(a.workdir)
    if a.visit_id:
        _show_visit_detail(wd, a.visit_id, getattr(a, "full", False))
        return 0
    _list_chronicle_visits(wd, a.recent or 20)
    return 0


def _list_chronicle_visits(wd, recent):
    visits = chronicle.all_visits(wd)[:recent]
    if not visits:
        print(f"{C.gray('no hay visitas archivadas en')} {wd / chronicle.CHRONICLE_ROOT}")
        return
    print(C.bold(f"\nCHRONICLE DE AGENTE — {(wd / chronicle.CHRONICLE_ROOT)}"))
    print(C.gray("─" * 80))
    for v in visits:
        vid = str(v.get("visit_id", "?"))
        node = str(v.get("node", "?"))
        task = str(v.get("task_id") or "-")
        rc = v.get("returncode", "?")
        at = str(v.get("at", "")[:19])
        prompt_kb = int(v.get("prompt_chars", 0)) // 1024
        resp_kb = int(v.get("response_chars", 0)) // 1024
        status = C.green("OK") if rc == 0 else C.red(f"exit={rc}")
        print(f"  {C.cyan(at)} {status}  {C.bold(node):<14} "
              f"tarea={C.gray(task):<10} "
              f"prompt={prompt_kb}KB resp={resp_kb}KB  "
              f"{C.gray(vid[:12])}")
    if len(visits) >= recent:
        print(C.gray(f"  ... y mas. Usa --recent N o --visit-id <id> para ver detalles"))
    print()


def _show_visit_detail(wd, visit_id, full=False):
    v = chronicle.read_visit(wd, visit_id)
    if not v:
        print(f"{C.gray('visita no encontrada:')} {visit_id}")
        return
    print(C.bold(f"\nCHRONICLE — {visit_id}"))
    print(f"  nodo: {C.cyan(v.get('node', '?'))}  "
          f"tarea: {C.gray(v.get('task_id') or '-')}  "
          f"returncode: {C.green('0') if v.get('returncode') == 0 else C.red(str(v.get('returncode', '?')))}")
    prompt_kb = int(v.get("prompt_chars", 0)) // 1024
    resp_kb = int(v.get("response_chars", 0)) // 1024
    print(f"  prompt: {prompt_kb}KB  respuesta: {resp_kb}KB")
    tokens = v.get("token_usage")
    if tokens and tokens.get("calls", 0) > 0:
        print(f"  tokens: {tokens['input_tokens']} in + {tokens['output_tokens']} out "
              f"({tokens['calls']} llamadas)")

    files = v.get("files_written", {})
    if isinstance(files, dict):
        written = files.get("written", [])
        skipped = files.get("skipped", [])
        if written:
            print(C.bold("  archivos escritos:"))
            for f in written:
                print(f"    {C.green('+')} {f}")
        if skipped:
            print(C.bold("  archivos omitidos:"))
            for s in skipped:
                path = s.get("path", "?")
                reason = s.get("reason", "?")
                print(f"    {C.yellow('!')} {path} — {C.gray(reason)}")

    if full:
        sys_prompt = v.get("system_prompt", "")
        user_prompt = v.get("user_prompt", "")
        response = v.get("response", "")
        agent_stdout = v.get("agent_stdout", "")
        agent_stderr = v.get("agent_stderr", "")

        if sys_prompt:
            print(C.bold(f"\n  -- SYSTEM PROMPT ({len(sys_prompt)} chars) --"))
            print(C.gray(sys_prompt[:3000]))
        if user_prompt:
            print(C.bold(f"\n  -- USER PROMPT ({len(user_prompt)} chars) --"))
            print(C.gray(user_prompt[:3000]))
        if response:
            print(C.bold(f"\n  -- RESPUESTA LLM ({len(response)} chars) --"))
            print(C.gray(response[:5000]))
        if agent_stdout:
            print(C.bold(f"\n  -- STDOUT ({len(agent_stdout)} chars) --"))
            print(C.gray(agent_stdout[:2000]))
        if agent_stderr:
            print(C.bold(f"\n  -- STDERR ({len(agent_stderr)} chars) --"))
            print(C.gray(agent_stderr[:2000]))
    else:
        print(C.gray("  usa --full para ver los prompts y la respuesta completa del LLM"))
    print()


def _show_task_lifecycle(wd, task_id):
    events = lifecycle.read(wd, task_id)
    if not events:
        print(f"{C.gray('no hay registro para la tarea')} {C.cyan(task_id)}")
        return
    summary = lifecycle.summary(wd, task_id)
    print(C.bold(f"\nCICLO DE VIDA — {task_id}"))
    print(f"  estado final: {C.cyan(summary.get('status', '?'))}")
    print(f"  nodo: {C.gray(summary.get('node', '?'))} "
          f"· kind: {C.gray(summary.get('kind', '?'))}")
    print(f"  eventos: {summary.get('events', 0)} · "
          f"llamadas a agente: {summary.get('calls', 0)}")
    if summary.get("blocked_by"):
        print(f"  bloqueada por: {C.yellow(summary.get('blocked_by'))}")

    gates = summary.get("gates", {})
    if gates:
        print(C.bold("  gates:"))
        gate_str = " ".join(
            (C.green if passed else C.red)(f"{g}{'✓' if passed else '✗'}")
            for g, passed in sorted(gates.items()))
        print(f"    {gate_str}")

    tokens = lifecycle.total_token_usage_by_task(wd, task_id)
    if tokens.get("calls", 0) > 0:
        print(f"  tokens: {tokens['input_tokens']} entrada + "
              f"{tokens['output_tokens']} salida ({tokens['calls']} llamadas)")

    print(C.gray("\n  ── timeline ──"))
    for ev in events:
        ts = ev.get("t", "")
        name = ev.get("event", "?")
        icon = {
            "created": "📋", "started": "▶", "agent_called": "🤖",
            "gate_result": "🔍", "blocked": "🚫", "retried": "🔄",
            "escalated": "⚠", "integrated": "✅", "done": "🏁",
        }.get(name, "·")
        extra = ""
        if name == "gate_result":
            extra = f"{ev.get('gate')} → {'PASS' if ev.get('status') == 'pass' else 'FAIL'} ({ev.get('findings', 0)} hallazgos)"
        elif name == "blocked":
            extra = f"bloqueada por {ev.get('blocked_by')} — gate {ev.get('gate')}"
            findings = ev.get("findings", [])
            for f in findings:
                extra += f"\n      {C.red('  ✗')} {f.get('rule')} {C.gray(f.get('file') + ':' + str(f.get('line',''))) if f.get('file') else ''}"
        elif name == "integrated":
            extra = f"resultado: {ev.get('result')} — {ev.get('detail', '')[:80]}"
        elif name == "escalated":
            extra = str(ev.get("reason", ""))[:100]
        elif name == "retried":
            extra = f"{ev.get('gate')} — intento {ev.get('attempt')}/{ev.get('max_retries')}"
        elif name == "agent_called":
            extra = f"returncode={ev.get('returncode')} status={ev.get('status')}"
        elif name == "started":
            extra = f"nodo={ev.get('node')} workspace={str(ev.get('workspace', ''))[:40]}"
        print(f"  {C.gray(ts)} {icon} {C.bold(name):<14} {C.gray(extra)}")

    if tokens.get("calls", 0) > 0:
        print(C.gray(f"\n  tokens: {tokens['input_tokens']} entrada + "
                      f"{tokens['output_tokens']} salida ({tokens['calls']} llamadas)"))
    print()


def build_parser():
    p = argparse.ArgumentParser(prog="sdd", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("menu", help="interfaz interactiva (menú en la terminal)")
    sub.add_parser("shell", help="consola interactiva (REPL)")
    s = sub.add_parser("demo"); s.add_argument("--workdir", default="../demo-repo")
    s.set_defaults(fn=demo)
    s = sub.add_parser("gates"); s.add_argument("--node", required=True)
    s.add_argument("--workdir", required=True); s.set_defaults(fn=gates)
    s = sub.add_parser("run")
    s.add_argument("--project", default=None, help="nombre → project/<nombre> (o config)")
    s.add_argument("--workdir", default=None, help="ruta explícita (anula --project)")
    s.add_argument("--intake", default=None, help="archivo de idea (default: ./intake.yaml)")
    s.add_argument("--task", default="Ejecutar el plan de spec/30_plan/tasks.yaml"); s.set_defaults(fn=run)
    s = sub.add_parser("doctor"); s.set_defaults(fn=doctor)
    s = sub.add_parser("config"); s.set_defaults(fn=config_cmd)
    for nm in ("web", "serve"):   # 'web' y alias 'serve' — misma acción
        s = sub.add_parser(nm); s.add_argument("--port", type=int, default=8770)
        s.add_argument("--no-open", action="store_true", help="no abrir el navegador")
        s.set_defaults(fn=serve)
    s = sub.add_parser("resume"); s.add_argument("--node", default=None,
        help="nodo concreto; si se omite, continúa desde donde quedó (--resume)")
    s.add_argument("--workdir", required=True)
    s.add_argument("--autonomous", action="store_true", help="reanuda sin intervención humana")
    s.add_argument("--task", default="Ejecutar el plan de spec/30_plan/tasks.yaml"); s.set_defaults(fn=resume)
    s = sub.add_parser("clean"); s.add_argument("--workdir", default="../demo-repo")
    s.set_defaults(fn=clean)
    s = sub.add_parser("test"); s.set_defaults(fn=test)
    s = sub.add_parser("show"); s.add_argument("--workdir", default="../demo-repo")
    s.set_defaults(fn=show)
    s = sub.add_parser("view"); s.add_argument("--workdir", default="../demo-repo")
    s.add_argument("--no-open", action="store_true", help="solo genera el HTML, no abre el navegador")
    s.set_defaults(fn=view)
    s = sub.add_parser("tasks")
    s.add_argument("--workdir", default="../demo-repo")
    s.add_argument("--task-id", default=None, help="id de tarea especifica (si no, lista todas)")
    s.add_argument("--verbose", action="store_true", help="muestra eventos detallados de cada tarea")
    s.set_defaults(fn=tasks)
    s = sub.add_parser("chronicle")
    s.add_argument("--workdir", default="../demo-repo")
    s.add_argument("--visit-id", default=None, help="id de visita especifica")
    s.add_argument("--recent", type=int, default=20, help="cuantas visitas recientes mostrar")
    s.add_argument("--full", action="store_true", help="muestra prompts y respuesta completa del LLM")
    s.set_defaults(fn=chronicle_cmd)
    return p


SHELL_CMDS = ("demo", "gates", "run", "resume", "clean", "test",
              "web", "serve", "doctor", "config", "show", "view", "tasks",
              "chronicle")


def _shell_help(parser):
    print(C.bold("\nCONSOLA SDD — comandos disponibles:"))
    rows = [
        ("demo", "corre el bucle autónomo simulado (0 tokens)"),
        ("run --project <n>", "corrida real con agentes → project/<n>/"),
        ("show --workdir <r>", "estado de una corrida (nodos, gates, llamadas)"),
        ("view --workdir <r>", "genera y abre el reporte HTML"),
        ("gates --node <n> --workdir <r>", "corre los gates de un nodo"),
        ("resume --node <n> --workdir <r>", "reanuda desde un nodo"),
        ("doctor", "verifica proveedor/modelo/API key"),
        ("config", "muestra la config guardada"),
        ("web", "levanta el panel web (Ctrl+C para volver)"),
        ("clean --workdir <r>", "borra el estado .agent/ de un repo"),
        ("test", "corre la batería de pruebas"),
    ]
    for cmd, desc in rows:
        print(f"  {C.green(cmd):<48} {C.gray(desc)}")
    print(f"  {C.green('help'):<48} {C.gray('esta ayuda')}")
    print(f"  {C.green('exit / quit'):<48} {C.gray('salir de la consola')}")
    print(C.gray("\nCada comando acepta sus flags con --flag valor, igual que en la línea de comandos.\n"))


def shell(parser):
    print(C.bold("SDD · consola interactiva"))
    print(C.gray("escribe 'help' para ver los comandos · 'exit' para salir"))
    while True:
        try:
            line = input(C.green("\nsdd> ")).strip()
        except EOFError:
            print(); break
        except KeyboardInterrupt:
            print(C.gray("  (usa 'exit' para salir)")); continue
        if not line:
            continue
        low = line.lower()
        if low in ("exit", "quit", "q", ":q", "salir"):
            break
        if low in ("help", "?", "h", "ayuda"):
            _shell_help(parser); continue
        try:
            tokens = shlex.split(line)
        except ValueError as e:
            print(C.red(f"línea inválida: {e}")); continue
        try:
            a = parser.parse_args(tokens)
        except SystemExit:
            continue  # argparse ya imprimió el uso o el error
        if getattr(a, "cmd", None) in (None, "shell") or getattr(a, "fn", None) is None:
            print(C.gray("comando desconocido — escribe 'help'")); continue
        try:
            rc = a.fn(a)
            if rc:
                print(C.gray(f"[código de salida {rc}]"))
        except KeyboardInterrupt:
            print(C.gray("\n  interrumpido — de vuelta en la consola"))
        except subprocess.CalledProcessError as e:
            print(C.red(f"el comando falló (código {e.returncode})"))
        except Exception as e:  # noqa: BLE001 — la consola no debe morir por un comando
            print(C.red(f"error: {type(e).__name__}: {e}"))
    print(C.gray("hasta luego."))
    return 0


def main():
    parser = build_parser()
    a = parser.parse_args()
    cmd = getattr(a, "cmd", None)
    if cmd in (None, "menu"):
        import cli_menu  # import diferido: rompe el ciclo cli <-> cli_menu
        return cli_menu.home()
    if cmd == "shell":
        return shell(parser)
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()

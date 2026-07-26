#!/usr/bin/env python3
"""Genera un reporte HTML autocontenido de una corrida a partir de state.json.

Sin dependencias, sin CDN: un solo archivo que se abre en cualquier navegador y
reproduce la corrida paso a paso (los mismos datos que alimentan `run.py show`).
El principio es el mismo del pipeline: la evidencia son los artefactos, no el chat.
"""
import json


def build_steps(history):
    """Segmenta el historial en visitas de nodo y las normaliza para la vista."""
    visits, cur = [], None
    for ev in history:
        if ev["event"] == "AGENTE":
            cur = {"node": ev.get("nodo", "?"), "gates": [], "defects": [],
                   "routed": None, "revert": None, "commit": 0,
                   "task": ev.get("tarea") or "",
                   "human": ev.get("resultado") == "human", "escalate": None}
            visits.append(cur)
        elif cur is None:
            continue
        elif ev["event"].startswith("GATE ") and "estado" in ev:
            cur["gates"].append([ev["event"].split()[1], ev["estado"] == "pass"])
        elif ev["event"] == "DEFECTO":
            cur["defects"].append([ev["gate"], ev["regla"], ev["ubicacion"], ev["evidencia"]])
        elif ev["event"] == "REVERT":
            cur["revert"] = [ev["archivos"], ev["motivo"]]
        elif ev["event"] == "ENRUTADO":
            cur["routed"] = [ev["a"], ev["intento"]]
        elif ev["event"] == "APROBADO":
            # 'sin-commit' significa que el arbol no cambio: no se pinta como commit.
            cur["commit"] = 1 if ev.get("accion", "commit") == "commit" else 0
        elif ev["event"] == "ESCALATE_HUMAN":
            cur["escalate"] = ev["motivo"]
    return visits


_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDD pipeline — corrida %(status)s</title>
<style>
:root{--bg:#faf9f5;--surface:#f0efe8;--card:#fff;--text:#1a1a18;--muted:#6b6a64;
--border:#e3e1d9;--ok:#3b6d11;--okbg:#eaf3de;--bad:#a32d2d;--badbg:#fcebeb;
--acc:#185fa5;--accbg:#e6f1fb;--warn:#854f0b;--warnbg:#faeeda}
@media(prefers-color-scheme:dark){:root{--bg:#1c1b19;--surface:#26251f;--card:#26251f;
--text:#ece9e2;--muted:#a8a69d;--border:#3a3833;--ok:#97c459;--okbg:#27500a;
--bad:#f09595;--badbg:#501313;--acc:#85b7eb;--accbg:#042c53;--warn:#ef9f27;--warnbg:#412402}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 -apple-system,
"Segoe UI",Roboto,sans-serif;padding:2rem 1rem}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:20px;font-weight:500;margin:0 0 .25rem}
.sub{color:var(--muted);font-size:14px;margin:0 0 1.5rem}
.sub b{color:var(--text);font-weight:500}
.rail{display:grid;grid-template-columns:repeat(auto-fit,minmax(74px,1fr));gap:5px;margin:1.25rem 0}
.chip{border:1px solid var(--border);border-radius:8px;padding:9px 4px;text-align:center;
font-size:12px;color:var(--muted);transition:all .25s}
.chip .nm{font-weight:500;display:block}
.chip .mk{font-size:11px;margin-top:4px;min-height:15px;display:block}
.ctl{display:flex;align-items:center;gap:10px;margin:1rem 0;flex-wrap:wrap}
button{font:inherit;font-size:14px;background:var(--card);color:var(--text);
border:1px solid var(--border);border-radius:8px;padding:7px 14px;cursor:pointer}
button:hover{background:var(--surface)}
.ct{margin-left:auto;display:flex;gap:16px;font-size:13px;color:var(--muted)}
.ct b{color:var(--text);font-weight:500}
.det{min-height:160px;background:var(--surface);border-radius:12px;padding:1.1rem 1.3rem}
.gp{display:inline-block;font-size:12px;padding:2px 10px;border-radius:20px;margin:0 6px 6px 0}
.df{font-size:13px;margin-top:8px;color:var(--bad)}
.df code{font-size:12px;color:var(--muted)}
.oc{font-size:14px;margin-top:12px;font-weight:500}
.lg{display:flex;gap:16px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.lg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:1px}
</style></head><body><div class="wrap">
<h1>Pipeline SDD — reproduccion de la corrida</h1>
<p class="sub">%(workdir)s · estado <b>%(status)s</b> · %(calls)s llamadas · %(retries)s reintentos</p>
<div class="lg">
<span><i style="background:var(--ok)"></i>gate en verde</span>
<span><i style="background:var(--bad)"></i>gate en rojo / reintento</span>
<span><i style="background:var(--acc)"></i>nodo activo</span>
<span><i style="background:var(--muted)"></i>pendiente</span></div>
<div class="rail" id="rail"></div>
<div class="ctl">
<button id="s">▶ paso</button><button id="p">⏵ reproducir</button><button id="r">↺ reiniciar</button>
<div class="ct"><div>paso <b id="cs">0</b>/%(total)s</div><div>llamadas <b id="cc">0</b></div>
<div>reintentos <b id="cr">0</b></div></div></div>
<div class="det" id="det"></div></div>
<script>
const NODES=%(nodes)s, S=%(data)s, STATUS=%(statusj)s;
let idx=0,timer=null;
const rail=document.getElementById("rail"),det=document.getElementById("det");
NODES.forEach(n=>rail.insertAdjacentHTML("beforeend",
 `<div class="chip" id="nd-${n[0]}"><span class="nm">${n[1]}</span><span class="mk" id="mk-${n[0]}"></span></div>`));
function tint(el,r){if(!r){el.style.cssText="";return}
 el.style.background=`var(--${r}bg)`;el.style.borderColor=`var(--${r})`;el.style.color=`var(--${r})`}
function render(){
 const shown=S.slice(0,idx);
 cs.textContent=idx;cc.textContent=idx;cr.textContent=shown.filter(s=>s.routed).length;
 const cur=idx>0?S[idx-1]:null, done=new Set(shown.filter(s=>s.commit).map(s=>s.node));
 NODES.forEach(n=>{const id=n[0],chip=document.getElementById("nd-"+id),mk=document.getElementById("mk-"+id);
  mk.textContent="";tint(chip,null);
  if(id==="done"){if(idx===S.length&&STATUS==="done"){tint(chip,"ok");mk.textContent="✔"}return}
  if(cur&&cur.node===id){const bad=cur.gates&&cur.gates.some(g=>!g[1]);tint(chip,bad?"bad":"acc");
   mk.innerHTML=cur.gates.length?cur.gates.map(g=>`<span style="color:var(--${g[1]?'ok':'bad'})">${g[0]}${g[1]?'✓':'✗'}</span>`).join(" "):"👤";}
  else if(done.has(id)){tint(chip,"ok");mk.textContent="✓"}});
 if(!cur){det.innerHTML='<p style="color:var(--muted);margin:.2rem 0">Pulsa <b style="color:var(--text)">paso</b> o <b style="color:var(--text)">reproducir</b>. Cada nodo escribe artefactos en git; los gates los verifican; el supervisor enruta el fallo a su dueno y revierte lo que se salga de su path.</p>';return}
 const tlabel=cur.task&&cur.task!=="-"?` · tarea <span style="color:var(--warn)">${cur.task}</span>`:"";
 let h=`<div style="font-size:15px;font-weight:500;margin-bottom:8px">nodo <span style="color:var(--acc)">${cur.node}</span>${tlabel}</div>`;
 if(cur.gates.length)h+=cur.gates.map(g=>`<span class="gp" style="background:var(--${g[1]?'ok':'bad'}bg);color:var(--${g[1]?'ok':'bad'})">${g[0]} ${g[1]?'pasa':'falla'}</span>`).join("");
 if(cur.human)h+=`<div style="font-size:14px;color:var(--muted)">👤 firma humana de la especificacion y el plan</div>`;
 cur.defects.forEach(d=>h+=`<div class="df">⚠ <b style="font-weight:500">${d[0]} · ${d[1]}</b><br><code>${d[2]}</code> — ${d[3]}</div>`);
 if(cur.revert)h+=`<div class="oc" style="color:var(--bad)">↩ revert automatico · ${cur.revert[0]} archivo · ${cur.revert[1]}</div>`;
 if(cur.routed)h+=`<div class="oc" style="color:var(--warn)">→ enrutado a ${cur.routed[0]} (intento ${cur.routed[1]})</div>`;
 else if(cur.escalate)h+=`<div class="oc" style="color:var(--bad)">⚠ ESCALATE_HUMAN · ${cur.escalate}</div>`;
 else if(cur.commit)h+=`<div class="oc" style="color:var(--ok)">✓ gates en verde · commit</div>`;
 if(idx===S.length&&STATUS==="done")h+=`<div class="oc" style="color:var(--ok)">✔ estado final: done</div>`;
 det.innerHTML=h}
function step(){if(idx<S.length){idx++;render()}if(idx>=S.length&&timer){clearInterval(timer);timer=null;pb()}}
function pb(){document.getElementById("p").textContent=timer?"⏸ pausa":"⏵ reproducir"}
document.getElementById("s").onclick=()=>{if(timer){clearInterval(timer);timer=null;pb()}step()};
document.getElementById("p").onclick=()=>{if(timer){clearInterval(timer);timer=null}else{if(idx>=S.length)idx=0;timer=setInterval(step,1100)}pb()};
document.getElementById("r").onclick=()=>{if(timer){clearInterval(timer);timer=null}idx=0;pb();render()};
render();
</script></body></html>"""


def _verdict(state):
    """El veredicto sale del estado real, no de haber llegado al final del grafo.

    Un run puede terminar el recorrido y no haber entregado nada: eso es
    INCOMPLETO, no COMPLETADO. Que el reporte lo diga es la mitad del arreglo.
    """
    tasks = state.get("tasks") or []
    done = sum(1 for t in tasks if t["status"] == "done")
    base = {"done": "COMPLETADO", "escalated": "ESCALADO A HUMANO",
            "waiting_human": "EN ESPERA DE HUMANO"}.get(state["status"], state["status"])
    if state["status"] == "done" and tasks and done < len(tasks):
        return f"INCOMPLETO ({done}/{len(tasks)} tareas)"
    return base


def _review_section(wd):
    """Backlog del revisor R1: lo que vio y no llego a frenar el pipeline.

    Las mejoras no bloquean por diseno, pero desaparecer no es una opcion: si el
    revisor detecto algo y nadie se entera, el gasto de esa llamada fue inutil.
    Aqui tambien salen las revisiones que no se pudieron hacer.
    """
    import json as _json
    from pathlib import Path as _Path

    carpeta = _Path(wd) / ".agent/review"
    if not carpeta.exists():
        return []
    mejoras, notas = [], []
    for f in sorted(carpeta.glob("*.json")):
        try:
            st = _json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        nodo = f.stem
        for m in st.get("mejoras", []):
            mejoras.append(f"- `{nodo}` **{m['rule']}** — {m['evidence']} "
                           f"({m['file']}:{m['line']})")
        if st.get("nota"):
            notas.append(f"- `{nodo}` — {st['nota']}")
    out = []
    if mejoras:
        out += ["", "## Backlog del revisor (no bloqueante)",
                "Hallazgos de criterio que no frenaron la corrida. Decide tu si valen "
                "una iteracion.", ""] + mejoras
    if notas:
        out += ["", "## Avisos de revision", ""] + notas
    return out


def _token_usage(wd):
    """Suma .agent/usage.jsonl. Vacio en modo simulado."""
    import json as _json
    from pathlib import Path as _Path
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    path = _Path(wd) / ".agent/usage.jsonl"
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = _json.loads(line)
        except ValueError:
            continue
        for k in total:
            total[k] += int(rec.get(k, 0) or 0)
    return total


def write_run_report(state, workdir, task, git):
    """Emite .agent/REPORT.md y un bloque en stdout. Sin intervencion humana el
    reporte es la unica salida que el operador revisa: que se hizo, cuanto costo,
    en que quedo. Los datos salen del estado y de git, no de juicio del modelo."""
    import time
    from pathlib import Path

    wd = Path(workdir)
    files = git(wd, "ls-files").stdout.splitlines()
    logline = git(wd, "log", "--oneline", "--no-decorate").stdout.splitlines()
    counts = {}
    for f in files:
        top = (f.split("/", 1)[0] + "/") if "/" in f else f
        counts[top] = counts.get(top, 0) + 1
    elapsed = time.time() - state.get("started_at", time.time())
    tasks = state.get("tasks") or []
    done = sum(1 for t in tasks if t["status"] == "done")
    sin_commit = [e for e in state["history"]
                  if e["event"] == "APROBADO" and e.get("accion") == "sin-commit"]
    usage = _token_usage(wd)
    tok = (f"{usage['input_tokens']} entrada + {usage['output_tokens']} salida "
           f"({usage['calls']} llamada(s) al modelo)"
           if usage["calls"] else "n/d (modo simulado, 0 tokens)")

    lines = [
        f"# Reporte de ejecucion — {_verdict(state)}",
        "",
        f"- **Tarea:** {task}",
        f"- **run_id:** {state.get('run_id', '?')}",
        f"- **Estado final:** {state['status']}",
        f"- **Tareas del plan:** {done}/{len(tasks)} completadas",
        f"- **Llamadas a agente:** {state['agent_calls']}",
        f"- **Tokens:** {tok}",
        f"- **Duracion:** {elapsed:.0f}s",
        f"- **Commits:** {len(logline)}",
        f"- **Archivos versionados:** {len(files)}",
        "",
        "## Artefactos por area",
    ]
    for top in sorted(counts):
        lines.append(f"- `{top}` — {counts[top]} archivo(s)")

    if tasks:
        lines += ["", "## Tareas"]
        for t in tasks:
            mark = {"done": "x", "blocked": "!", "pending": " "}.get(t["status"], "?")
            extra = f" (bloqueada por {t['blocked_by']})" if t.get("blocked_by") else ""
            lines.append(f"- [{mark}] `{t['id']}` {t['node']} — {t['title']}{extra}")

    lines += _review_section(wd)

    if sin_commit:
        lines += ["", "## Nodos que no dejaron cambios en el arbol"]
        lines += [f"- {e.get('nodo')} — {e.get('detalle')}" for e in sin_commit]
    if state.get("attempts"):
        lines += ["", "## Reintentos por gate"]
        for k, v in sorted(state["attempts"].items()):
            lines.append(f"- {k}: {v}")
    lines += ["", "## Historial de commits"]
    lines += [f"- {l}" for l in logline] or ["- (sin commits)"]

    report = "\n".join(lines) + "\n"
    (wd / ".agent").mkdir(parents=True, exist_ok=True)
    (wd / ".agent/REPORT.md").write_text(report, encoding="utf-8")

    print("\n== REPORTE ==", flush=True)
    print(report, flush=True)
    print("== FIN REPORTE ==", flush=True)


def render_html(state, node_order, workdir):
    steps = build_steps(state["history"])
    nodes = [[n, {"human_gate": "humano", "dev_backend": "backend",
                  "dev_frontend": "frontend"}.get(n, n)] for n in node_order] + [["done", "done"]]
    return _HTML % {
        "status": state["status"],
        "statusj": json.dumps(state["status"]),
        "workdir": str(workdir),
        "calls": state.get("agent_calls", 0),
        "retries": sum(state.get("attempts", {}).values()),
        "total": len(steps),
        "nodes": json.dumps(nodes, ensure_ascii=False),
        "data": json.dumps(steps, ensure_ascii=False),
    }

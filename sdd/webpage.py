"""Plantilla HTML del panel web. Extraida de server.py para mantener
ambos modulos por debajo del limite duro de 500 lineas (la misma regla
que el pipeline exige al codigo que genera). Solo es una cadena.
"""
PAGE = r"""<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>SDD Pipeline — panel</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root,:root[data-theme=dark]{
 --bg:#0f172a;--panel:#1e293b;--panel2:#172033;--muted:#334155;--bd:#2b3a52;--bd2:#3f5170;
 --fg:#f8fafc;--fg2:#94a3b8;--fg3:#64748b;
 --ac:#22c55e;--acw:rgba(34,197,94,.14);
 --bad:#f87171;--badw:rgba(248,113,113,.13);--warn:#fbbf24;--warnw:rgba(251,191,36,.13);
 --info:#38bdf8;--infow:rgba(56,189,248,.13);}
:root[data-theme=light]{
 --bg:#f4f5f7;--panel:#fff;--panel2:#f0f2f5;--muted:#e8ebef;--bd:#e3e7ed;--bd2:#cdd5e0;
 --fg:#0f172a;--fg2:#475569;--fg3:#94a3b8;--ac:#16a34a;--acw:rgba(22,163,74,.10);
 --bad:#dc2626;--badw:rgba(220,38,38,.09);--warn:#b45309;--warnw:rgba(180,83,9,.10);
 --info:#0284c7;--infow:rgba(2,132,199,.10);}
@media (prefers-color-scheme:light){:root:not([data-theme]){
 --bg:#f4f5f7;--panel:#fff;--panel2:#f0f2f5;--muted:#e8ebef;--bd:#e3e7ed;--bd2:#cdd5e0;
 --fg:#0f172a;--fg2:#475569;--fg3:#94a3b8;--ac:#16a34a;--acw:rgba(22,163,74,.10);
 --bad:#dc2626;--badw:rgba(220,38,38,.09);--warn:#b45309;--warnw:rgba(180,83,9,.10);
 --info:#0284c7;--infow:rgba(2,132,199,.10);}}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--fg);font-family:Inter,-apple-system,"Segoe UI",Roboto,sans-serif;
 font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.app{max-width:none;margin:0;padding:20px 28px 48px}
.top{display:flex;align-items:center;gap:14px;padding:6px 2px 16px}
.logo{width:34px;height:34px;border-radius:9px;background:var(--acw);display:grid;place-items:center;flex:none}
.logo svg{width:20px;height:20px;stroke:var(--ac)}
.brand h1{font-size:18px;font-weight:600;margin:0}.brand p{margin:0;font-size:12.5px;color:var(--fg3)}
.top .sp{flex:1}
.badge{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:500;padding:6px 12px;
 border-radius:999px;border:1px solid var(--bd);background:var(--panel);color:var(--fg2)}
.badge .dot{width:8px;height:8px;border-radius:50%;background:var(--fg3);flex:none}
.badge.run{background:var(--infow);border-color:transparent;color:var(--info)}
.badge.run .dot{background:var(--info);animation:pulse 1.2s infinite}
.badge.ok{background:var(--acw);border-color:transparent;color:var(--ac)}.badge.ok .dot{background:var(--ac)}
.badge.err{background:var(--badw);border-color:transparent;color:var(--bad)}.badge.err .dot{background:var(--bad)}
.prov{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--fg3)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--bd);margin-bottom:16px;overflow-x:auto}
.tab{font:inherit;font-size:13.5px;font-weight:500;color:var(--fg2);background:none;border:0;
 padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;display:flex;align-items:center;gap:7px;white-space:nowrap}
.tab svg{width:16px;height:16px;stroke:currentColor}
.tab:hover{color:var(--fg)}.tab.on{color:var(--ac);border-bottom-color:var(--ac)}
.tab svg[fill=currentColor]{stroke:none}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
@media (max-width:920px){.grid{grid-template-columns:1fr}}
.card .body>.field,.card .body>.frow{max-width:560px}
.field-idea{max-width:none}
.col{display:flex;flex-direction:column;gap:16px}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:12px}
.card h2{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--fg2);
 margin:0;padding:15px 18px;border-bottom:1px solid var(--bd)}
.body{padding:18px}
.panel{display:none}.panel.on{display:block}
.field{margin-bottom:14px}.field:last-child{margin-bottom:0}
label{display:block;font-size:12.5px;font-weight:500;color:var(--fg2);margin:0 0 6px}
label .opt{color:var(--fg3);font-weight:400}
textarea,input,select{width:100%;font-family:inherit;font-size:14px;color:var(--fg);background:var(--panel2);
 border:1px solid var(--bd);border-radius:9px;padding:10px 12px;outline:none;transition:border-color .15s,box-shadow .15s}
textarea{resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.55}
.addon{min-height:80px}
.frow{display:grid;grid-template-columns:1fr 1fr;gap:12px}.frow .field{margin-bottom:0}
#idea{min-height:220px;font-family:inherit;font-size:15px;line-height:1.6;padding:14px 15px}
#idea::placeholder{color:var(--fg3)}
.field-idea label{font-size:13px;font-weight:600;color:var(--fg)}
.field-idea .count{float:right;font-weight:400;color:var(--fg3);font-size:11.5px}
textarea:focus,input:focus,select:focus{border-color:var(--ac);box-shadow:0 0 0 3px var(--acw)}
select{appearance:none;cursor:pointer}
.selwrap{position:relative}
.selwrap::after{content:"";position:absolute;right:13px;top:50%;width:7px;height:7px;
 border-right:2px solid var(--fg3);border-bottom:2px solid var(--fg3);transform:translateY(-70%) rotate(45deg);pointer-events:none}
.keyrow{position:relative}.keyrow input{padding-right:42px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;border:0;padding:8px;cursor:pointer;color:var(--fg3);display:grid;place-items:center}
.eye:hover{color:var(--fg2)}.eye svg{width:17px;height:17px;stroke:currentColor}
.hint{font-size:11.5px;color:var(--fg3);margin:6px 0 0;display:flex;gap:6px;align-items:flex-start}
.hint svg{width:13px;height:13px;stroke:currentColor;flex:none;margin-top:2px}
.path{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--ac);word-break:break-all}
.btn{width:100%;font:inherit;font-size:14.5px;font-weight:600;border:0;border-radius:10px;padding:12px;cursor:pointer;
 display:flex;align-items:center;justify-content:center;gap:8px;transition:filter .15s,transform .05s}
.btn:active{transform:scale(.99)}.btn svg{width:16px;height:16px}
.btn-primary{color:#052e16;background:var(--ac)}.btn-primary:hover{filter:brightness(1.07)}
.btn-primary:disabled{filter:saturate(.4) brightness(.8);cursor:default}
.btn-ghost{color:var(--fg);background:var(--panel2);border:1px solid var(--bd)}.btn-ghost:hover{border-color:var(--bd2)}
.saved{font-size:12px;color:var(--ac);text-align:center;margin-top:8px;min-height:15px;transition:opacity .3s;opacity:0}
.spin{width:15px;height:15px;border:2px solid rgba(5,46,22,.35);border-top-color:#052e16;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.agent-blk{border:1px solid var(--bd);border-radius:10px;padding:12px;margin-bottom:12px;background:var(--panel2)}
.agent-blk .nm{font-size:13px;font-weight:600;margin:0 0 8px;display:flex;align-items:center;gap:7px}
.agent-blk .dotc{width:8px;height:8px;border-radius:50%;background:var(--ac);flex:none}
.tasklist{display:flex;flex-direction:column;gap:10px}
.taskitem{border:1px solid var(--bd);border-radius:10px;padding:12px 14px;cursor:pointer;background:var(--panel2);transition:border-color .15s}
.taskitem:hover{border-color:var(--bd2)}
.taskitem .row{display:flex;align-items:center;justify-content:space-between;gap:10px}
.taskitem .tn{font-weight:600;font-size:14px}
.mini{font-size:11px;padding:2px 9px;border-radius:999px;font-weight:600;white-space:nowrap}
.mini.ok{background:var(--acw);color:var(--ac)}.mini.err{background:var(--badw);color:var(--bad)}
.mini.run{background:var(--infow);color:var(--info)}.mini.idle{background:var(--muted);color:var(--fg3)}
.bar{height:6px;border-radius:4px;background:var(--muted);margin-top:9px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--ac);border-radius:4px;transition:width .3s}
.taskmeta{font-size:11.5px;color:var(--fg3);margin-top:6px;font-family:ui-monospace,Consolas,monospace}
.empty2{color:var(--fg3);font-size:13px;padding:8px 2px}
.dagwrap{overflow-x:auto;padding:20px 18px}
.dag{display:flex;align-items:center;min-width:max-content}
.sprint{margin-top:14px}
.sprinth{font-size:12.5px;font-weight:600;color:var(--fg2);margin-bottom:6px}
.sprintrow{font-size:12.5px;padding:3px 0;font-family:ui-monospace,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sprintrow b{font-weight:600}
.node{width:118px;flex:none;background:var(--panel2);border:1.5px solid var(--bd);border-radius:11px;padding:12px 8px;text-align:center;transition:border-color .3s,background .3s,box-shadow .3s}
.node .ring{width:26px;height:26px;border-radius:50%;margin:0 auto 8px;border:2px solid var(--bd2);display:grid;place-items:center;transition:all .3s}
.node .ring svg{width:15px;height:15px}
.node .nm{font-size:12.5px;font-weight:600;color:var(--fg2);transition:color .3s}
.node .gt{min-height:16px;margin-top:7px;display:flex;flex-wrap:wrap;gap:3px;justify-content:center}
.pill{font-family:ui-monospace,Consolas,monospace;font-size:10px;font-weight:600;padding:1px 6px;border-radius:5px;background:var(--muted);color:var(--fg3)}
.pill.p{background:var(--acw);color:var(--ac)}.pill.f{background:var(--badw);color:var(--bad)}
.conn{width:22px;height:2px;background:var(--bd);flex:none;transition:background .3s}
.node.active{border-color:var(--info);background:var(--infow);box-shadow:0 0 0 3px var(--infow)}
.node.active .nm{color:var(--info)}.node.active .ring{border-color:var(--info)}
.node.active .ring .d{width:9px;height:9px;border-radius:50%;background:var(--info);animation:pulse 1.1s infinite}
.node.pass,.node.done{border-color:var(--ac)}.node.pass .nm,.node.done .nm{color:var(--ac)}
.node.pass .ring,.node.done .ring{border-color:var(--ac);background:var(--ac)}
.node.pass .ring svg,.node.done .ring svg{stroke:#052e16}
.node.fail{border-color:var(--bad)}.node.fail .nm{color:var(--bad)}
.node.fail .ring{border-color:var(--bad);background:var(--bad)}.node.fail .ring svg{stroke:#450a0a}
.conn.on{background:var(--ac)}
.viewbar{font-size:12px;color:var(--fg3);padding:0 18px 6px;font-family:ui-monospace,Consolas,monospace}
.loghead{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bd);padding-right:12px}
.loghead h2{border-bottom:0;flex:1}
.copylog{display:inline-flex;align-items:center;gap:6px;background:var(--panel2);border:1px solid var(--bd);color:var(--fg2);
 border-radius:7px;padding:6px 9px;font:500 11px/1 system-ui;cursor:pointer;transition:border-color .15s,color .15s}
.copylog:hover:not(:disabled){border-color:var(--ac);color:var(--fg)}.copylog:disabled{opacity:.45;cursor:not-allowed}
.copylog svg{width:14px;height:14px;stroke:currentColor}
.logcard .log{background:#0a0f1c;border-radius:0 0 12px 12px;font-family:ui-monospace,Consolas,monospace;
 font-size:12px;line-height:1.65;padding:14px 16px;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#c7d2e0}
.log .empty{color:#4b5b74}.log .l-err{color:#f87171}.log .l-ok{color:#4ade80}
.log .l-warn{color:#fbbf24}.log .l-info{color:#38bdf8}.log .l-dim{color:#64748b}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body><div class=app>

<div class=top>
 <div class=logo><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M5 6h14M5 12h14M5 18h9"/></svg></div>
 <div class=brand><h1>SDD Pipeline</h1><p>plano de control multi-agente</p></div>
 <div class=sp></div>
 <span id=prov class=prov></span>
 <span id=status class=badge><span class=dot></span><span id=statustxt>en reposo</span></span>
</div>

<div class=tabs>
 <button class="tab on" data-tab=run><svg viewBox="0 0 24 24" fill=currentColor><path d="M8 5v14l11-7z"/></svg>Ejecutar</button>
 <button class=tab data-tab=tasks><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/></svg>Tareas</button>
 <button class=tab data-tab=agents><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=8 r=4/><path d="M4 21v-1a6 6 0 0 1 12 0v1"/></svg>Agentes</button>
 <button class=tab data-tab=cfg><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=3/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>Configuración</button>
</div>

<div class=grid>
 <div class=col>
  <div class="card panel on" data-panel=run>
   <h2>Ejecutar</h2>
   <div class=body>
    <div class="field field-idea"><label for=idea>Idea <span class=count id=ideacount>0</span></label><textarea id=idea placeholder="Describe qué construir. Cuanto más claro el objetivo, los criterios de aceptación y las restricciones, mejor será el resultado autónomo…"></textarea></div>
    <div class=frow>
     <div class=field><label for=project>Proyecto</label><input id=project placeholder="mi-proyecto"></div>
     <div class=field><label for=task>Tarea</label><input id=task placeholder="tarea-1"></div>
    </div>
    <div class=field><label for=model>Modelo</label>
     <div class=selwrap><select id=model></select></div>
     <p class=hint id=provhint><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=9/><path d="M12 8h.01M11 12h1v4h1"/></svg><span></span></p></div>
    <div class=field><label>Se guardará en</label><div class=path id=pathprev>project/mi-proyecto/tarea-1</div></div>
    <button class="btn btn-primary" id=go><svg viewBox="0 0 24 24" fill=currentColor><path d="M8 5v14l11-7z"/></svg><span id=gotxt>Correr pipeline</span></button>
   </div>
  </div>

  <div class="card panel" data-panel=tasks>
   <h2>Tareas por proyecto</h2>
   <div class=body>
    <div class=field><label for=projsel>Proyecto</label><div class=selwrap><select id=projsel></select></div></div>
    <button class="btn btn-ghost" id=refreshtasks style=margin-bottom:14px>Refrescar</button>
    <div class=tasklist id=tasklist></div>
   </div>
  </div>

  <div class="card panel" data-panel=agents>
   <h2>Complementos de prompt</h2>
   <div class=body>
    <p class=hint style=margin-top:0><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=9/><path d="M12 8v8M8 12h8"/></svg>
     Texto extra que se añade al system prompt de cada subagente. Se aplica en cada corrida.</p>
    <div id=agentblocks></div>
    <button class="btn btn-ghost" id=saveagents>Guardar complementos</button>
    <div class=saved id=savedag></div>
   </div>
  </div>

  <div class="card panel" data-panel=cfg>
   <h2>Configuración</h2>
   <div class=body>
    <div class=field><label for=provider>Proveedor</label>
     <div class=selwrap><select id=provider>
      <option value=anthropic>Anthropic (Claude)</option><option value=deepseek>DeepSeek</option>
      <option value=qwen>Qwen</option><option value=glm>GLM (Zhipu)</option>
      <option value=kimi>Kimi (Moonshot)</option><option value=openai>OpenAI-compatible</option></select></div></div>
    <div class=field><label for=key>API key <span class=opt>(del proveedor seleccionado)</span></label>
     <div class=keyrow><input id=key type=password placeholder="sk-…" autocomplete=off>
      <button class=eye id=eye type=button aria-label="ver key"><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx=12 cy=12 r=3/></svg></button></div></div>
    <div class=field><label for=outbase>Ruta base de salida</label><input id=outbase placeholder="project">
     <p class=hint><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2Z"/></svg>Relativa a la raíz del repo, o absoluta.</p></div>
    <div class=field><label for=theme>Tema</label>
     <div class=selwrap><select id=theme><option value=auto>Automático</option><option value=dark>Oscuro</option><option value=light>Claro</option></select></div></div>
    <button class="btn btn-ghost" id=savecfg>Guardar configuración</button>
    <div class=saved id=saved></div>
    <p class=hint><svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><rect x=3 y=11 width=18 height=11 rx=2/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>Config y llaves en config.json (local, no versionado).</p>
   </div>
  </div>
 </div>

 <div class=col>
  <div class=card>
   <h2>Flujo del pipeline</h2>
   <div class=viewbar id=viewbar></div>
   <div class=dagwrap><div class=dag id=dag></div></div>
   <div id=sprint class=sprint></div>
  </div>
  <div class="card logcard">
   <div class=loghead><h2>Registro</h2>
    <button class=copylog id=copylog type=button aria-label="Copiar todo el registro" title="Copiar todo el registro" disabled>
     <svg viewBox="0 0 24 24" fill=none stroke-width=2 stroke-linecap=round stroke-linejoin=round><rect x=9 y=9 width=11 height=11 rx=2/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span id=copylogtxt>Copiar todo</span>
    </button>
   </div>
   <div class=log id=log aria-live=polite><span class=empty>Sin corrida seleccionada.</span></div>
  </div>
 </div>
</div>
</div>

<script>
const NODES=[["product","product"],["architect","architect"],["planner","plan"],["human_gate","humano"],["dev_backend","backend"],["dev_frontend","frontend"],["qa","qa"],["done","done"]];
const AGENTS=[["product","Producto"],["architect","Arquitecto"],["dev_backend","Dev backend"],["dev_frontend","Dev frontend"],["qa","QA"]];
const CHECK='<svg viewBox="0 0 24 24" fill=none stroke-width=3 stroke-linecap=round stroke-linejoin=round><path d="M20 6 9 17l-5-5"/></svg>';
const X='<svg viewBox="0 0 24 24" fill=none stroke-width=3 stroke-linecap=round stroke-linejoin=round><path d="M18 6 6 18M6 6l12 12"/></svg>';
const $=id=>document.getElementById(id);
let CFG={keys:{},agent_addons:{}},polling=null,LOG_LINES=[];
const dag=$("dag");
NODES.forEach((n,i)=>{dag.insertAdjacentHTML("beforeend",`<div class=node id="nd-${n[0]}"><div class=ring id="rg-${n[0]}"></div><div class=nm>${n[1]}</div><div class=gt id="mk-${n[0]}"></div></div>`);
 if(i<NODES.length-1)dag.insertAdjacentHTML("beforeend",`<div class=conn id="cn-${i}"></div>`);});
AGENTS.forEach(a=>$("agentblocks").insertAdjacentHTML("beforeend",
 `<div class=agent-blk><p class=nm><span class=dotc></span>${a[1]} <span style="color:var(--fg3);font-weight:400;font-family:ui-monospace">${a[0]}</span></p><textarea class=addon id="ad-${a[0]}" placeholder="Instrucciones extra para este agente…"></textarea></div>`));
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const slug=s=>(s||"").trim().replace(/[^A-Za-z0-9_.-]+/g,"-").replace(/^[-.]+|[-.]+$/g,"")||"sin-nombre";
function applyTheme(t){if(t==="auto")delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=t}
function pathPrev(){$("pathprev").textContent=($("outbase").value.trim()||"project")+"/"+slug($("project").value)+"/"+slug($("task").value||"tarea-1")}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
 document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");
 document.querySelectorAll(".panel").forEach(p=>p.classList.toggle("on",p.dataset.panel===t.dataset.tab));
 if(t.dataset.tab==="tasks")loadProjects()});
function logClass(l){if(/DEFECTO|ESCALATE|escalated|error/.test(l))return"l-err";
 if(/APROBADO|estado final: done|escrito|REANUDADO/.test(l))return"l-ok";
 if(/ENRUTADO|REVERT|reintento|OMITIDO/.test(l))return"l-warn";
 if(/GATE/.test(l))return"l-info";if(/AGENTE|>> nodo|real_agent/.test(l))return"l-dim";return""}
function paint(s){
 const map={running:["run","ejecutando"],done:["ok","completado"],escalated:["err","escalado"],waiting_human:["run","gate humano"],idle:["","en reposo"]};
 const k=s.status==="running"?"running":(s.final||"idle");const m=map[k]||["","en reposo"];
 $("status").className="badge "+m[0];$("statustxt").textContent=m[1];
 const runtime=s.engine==="langgraph"?"LangGraph":"";
 $("prov").textContent=[s.provider,runtime].filter(Boolean).map(x=>"· "+x).join(" ");
 $("viewbar").textContent=s.project?("proyecto: "+s.project+"   ·   tarea: "+s.task):"";
 const done=new Set(s.steps.filter(x=>x.commit).map(x=>x.node));
 const cur=s.steps.length?s.steps[s.steps.length-1]:null;
 NODES.forEach((n,i)=>{const id=n[0],node=$("nd-"+id),ring=$("rg-"+id),mk=$("mk-"+id);mk.innerHTML="";let st="";
  if(id==="done"){if(s.final==="done"){st="done";ring.innerHTML=CHECK}else ring.innerHTML=""}
  else if(s.status==="running"&&cur&&cur.node===id&&!done.has(id)){const bad=(cur.gates||[]).some(g=>!g[1]);st=bad?"fail":"active";ring.innerHTML=bad?X:'<span class=d></span>';mk.innerHTML=(cur.gates||[]).map(g=>`<span class="pill ${g[1]?'p':'f'}">${g[0]}</span>`).join("")}
  else if(done.has(id)){st="done";ring.innerHTML=CHECK}else ring.innerHTML="";
  node.className="node "+st;
  if(i>0){const cn=$("cn-"+(i-1));if(cn)cn.className="conn"+(done.has(id)||st==="done"?" on":"")}});
 const log=$("log");
 LOG_LINES=Array.isArray(s.log)?s.log:[];$("copylog").disabled=!LOG_LINES.length;
 if(LOG_LINES.length){log.innerHTML=LOG_LINES.map(l=>`<span class="${logClass(l)}">${esc(l)}</span>`).join("\n");log.scrollTop=log.scrollHeight}
 else log.innerHTML='<span class=empty>Sin log.</span>';
 paintSprint(s.sprint||[])}
function paintSprint(t){const el=$("sprint");if(!el)return;
 if(!t.length){el.innerHTML="";return}
 const done=t.filter(x=>x.status==="done").length;
 const ic={done:"✓",blocked:"✕",pending:"○"};
 const col={done:"var(--ok)",blocked:"var(--bad)",pending:"var(--muted)"};
 el.innerHTML=`<div class=sprinth>Sprint · ${done}/${t.length} tareas</div>`+
  t.map(x=>{const c=col[x.status]||"var(--muted)";
   const b=x.blocked_by?` <span style=color:var(--muted)>← ${esc(x.blocked_by)}</span>`:"";
   const k=x.kind==="defect"?" 🔧":"";
   return `<div class=sprintrow><span style="color:${c}">${ic[x.status]||"·"}</span> `+
    `<b>${esc(x.id)}</b>${k} <span style=color:var(--muted)>${esc(x.node)}</span> ${esc(x.title||"")}${b}</div>`}).join("")}
function stopBtn(){$("go").disabled=false;$("gotxt").textContent="Correr pipeline";const sv=$("go").querySelector("svg");if(sv)sv.style.display="";const sp=$("sp");if(sp)sp.remove()}
function poll(){fetch("/state").then(r=>r.json()).then(s=>{paint(s);if(s.status!=="running"){if(polling){clearInterval(polling);polling=null}stopBtn()}})}
const PROVLBL={anthropic:"Anthropic (Claude)",deepseek:"DeepSeek",qwen:"Qwen",glm:"GLM (Zhipu)",kimi:"Kimi (Moonshot)",openai:"OpenAI-compatible"};
function fillModels(provider,selected){const sel=$("model"),list=(CFG.model_choices&&CFG.model_choices[provider])||[];
 sel.innerHTML=list.length?list.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join(""):'<option value="">definido por SDD_MODEL</option>';
 if(selected&&list.includes(selected))sel.value=selected;
 $("provhint").querySelector("span").innerHTML='Proveedor: <b>'+esc(PROVLBL[provider]||provider)+'</b>. Cámbialo y añade su API key en <b>Configuración</b>.'}
function loadCfg(){fetch("/config").then(r=>r.json()).then(c=>{CFG=c;CFG.keys=c.keys||{};CFG.agent_addons=c.agent_addons||{};CFG.model_choices=c.model_choices||{};
 $("outbase").value=c.output_base||"project";$("theme").value=c.theme||"auto";
 $("provider").value=c.provider||"anthropic";fillModels($("provider").value,c.model);
 $("key").value=CFG.keys[$("provider").value]||"";applyTheme($("theme").value);
 AGENTS.forEach(a=>{const el=$("ad-"+a[0]);if(el)el.value=CFG.agent_addons[a[0]]||""});pathPrev()})}
function loadProjects(){fetch("/projects").then(r=>r.json()).then(list=>{const sel=$("projsel");const cur=sel.value;
 sel.innerHTML=list.length?list.map(p=>`<option>${esc(p)}</option>`).join(""):"<option value=''></option>";
 if(list.includes(cur))sel.value=cur;loadTasks()})}
function loadTasks(){const p=$("projsel").value;if(!p){$("tasklist").innerHTML='<div class=empty2>Aún no hay proyectos. Corre una tarea en Ejecutar.</div>';return}
 fetch("/tasks?project="+encodeURIComponent(p)).then(r=>r.json()).then(list=>{
  if(!list.length){$("tasklist").innerHTML='<div class=empty2>Este proyecto no tiene tareas.</div>';return}
  const cls={done:"ok",escalated:"err",running:"run",waiting_human:"run"};
  // Una tarea se puede CONTINUAR si quedo escalada o esperando al humano (o si el
  // proceso murio y quedo 'running' rancio): retoma sin perder lo ya commiteado.
  const resumable=t=>["escalated","waiting_human","running"].includes(t.final);
  $("tasklist").innerHTML=list.map(t=>{const c=cls[t.final]||"idle";const pct=Math.round(100*t.done/(t.total||5));
   const action=t.final==="waiting_human"?"✓ Aprobar y continuar":"▸ Continuar";
   const btn=resumable(t)?`<button class="btn btn-ghost resumebtn" data-t="${esc(t.task)}">${action}</button>`:"";
   return `<div class=taskitem data-t="${esc(t.task)}"><div class=row><span class=tn>${esc(t.task)}</span><span class="mini ${c}">${esc(t.final)}</span></div><div class=bar><i style="width:${pct}%"></i></div><div class=taskmeta>${t.done}/${t.total} nodos · ${t.calls} llamadas</div>${btn?'<div class=row style="margin-top:8px">'+btn+'</div>':''}</div>`}).join("");
  document.querySelectorAll(".taskitem").forEach(el=>el.onclick=()=>viewTask(p,el.dataset.t));
  document.querySelectorAll(".resumebtn").forEach(b=>b.onclick=ev=>{ev.stopPropagation();resumeTask(p,b.dataset.t)})})}
function viewTask(project,task){if(polling){clearInterval(polling);polling=null}stopBtn();
 fetch("/task?project="+encodeURIComponent(project)+"&task="+encodeURIComponent(task)).then(r=>r.json()).then(paint)}
function resumeTask(project,task){
 fetch("/resume",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({project,task})})
  .then(r=>r.json().then(d=>({ok:r.ok,d}))).then(({ok,d})=>{
   if(!ok){alert(d.error||"no se pudo continuar");return}
   document.querySelector('[data-tab=run]').click();  // vuelve a Ejecutar y sigue el log
   if(!polling)polling=setInterval(poll,1000);poll()})}
$("eye").onclick=()=>{const k=$("key");k.type=k.type==="password"?"text":"password"};
$("provider").onchange=()=>{$("key").value=CFG.keys[$("provider").value]||"";fillModels($("provider").value,CFG.model)};
$("theme").onchange=()=>applyTheme($("theme").value);
$("project").oninput=pathPrev;$("task").oninput=pathPrev;$("outbase").oninput=pathPrev;
$("refreshtasks").onclick=loadTasks;$("projsel").onchange=loadTasks;
function toast(id){const s=$(id);s.textContent="✓ guardado";s.style.opacity=1;setTimeout(()=>s.style.opacity=0,2000)}
function legacyCopy(text){const a=document.createElement("textarea");a.value=text;a.setAttribute("readonly","");a.style.position="fixed";a.style.opacity="0";
 document.body.appendChild(a);a.select();try{return document.execCommand("copy")}finally{a.remove()}}
function copyFeedback(text){const el=$("copylogtxt");el.textContent=text;setTimeout(()=>el.textContent="Copiar todo",1600)}
async function copyLog(){const text=LOG_LINES.join("\n");if(!text){copyFeedback("Sin registro");return}
 try{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);else if(!legacyCopy(text))throw new Error("copy");
  copyFeedback("✓ Copiado")}catch(_){copyFeedback("No se pudo copiar")}}
$("copylog").onclick=copyLog;
$("savecfg").onclick=()=>fetch("/config",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({output_base:$("outbase").value,theme:$("theme").value,provider:$("provider").value,keys:{[$("provider").value]:$("key").value}})})
 .then(r=>r.json()).then(c=>{c.model_choices=CFG.model_choices;CFG=c;CFG.keys=c.keys||{};fillModels($("provider").value,$("model").value);toast("saved")});
$("saveagents").onclick=()=>{const ad={};AGENTS.forEach(a=>ad[a[0]]=$("ad-"+a[0]).value);
 fetch("/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent_addons:ad})})
  .then(r=>r.json()).then(c=>{CFG=c;CFG.agent_addons=c.agent_addons||{};toast("savedag")})};
$("go").onclick=()=>{const idea=$("idea").value,key=$("key").value,project=$("project").value;
 if(!project.trim()){alert("Ponle nombre al proyecto");return}
 if(!idea.trim()){alert("Escribe la idea");return}
 if(!key.trim()){alert("Configura el proveedor y su API key en la pestaña Configuración");return}
 const b=$("go");b.disabled=true;$("gotxt").textContent="Ejecutando…";const sv=b.querySelector("svg");if(sv)sv.style.display="none";
 b.insertAdjacentHTML("afterbegin",'<span class=spin id=sp></span>');LOG_LINES=[];$("copylog").disabled=true;$("log").innerHTML='<span class=l-dim>Arrancando…</span>';
 fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idea,provider:$("provider").value,model:$("model").value,key,project,task:$("task").value})})
  .then(r=>r.json()).then(j=>{if(j.error){alert(j.error);stopBtn();return}if(!polling)polling=setInterval(poll,1000);poll()})
  .catch(()=>{alert("no pude contactar el servidor");stopBtn()})};
loadCfg();
const _idc=()=>{const n=$("idea").value.length;$("ideacount").textContent=n?n+" car.":"0"};
$("idea").addEventListener("input",_idc);
fetch("/idea").then(r=>r.text()).then(t=>{if(t&&!$("idea").value)$("idea").value=t;_idc()});
poll();
</script></body></html>"""

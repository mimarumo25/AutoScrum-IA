# Pipeline SDD multi-agente — LangGraph v0.3

Plano de control primero: los gates y la propiedad de paths son lo que hace utilizable
un pipeline de agentes. El orquestador es la pieza barata.

## Que hay aqui

| Archivo | Rol |
|---|---|
| `pipeline.toml` | grafo, propiedad de paths, entregables exigidos, presupuesto |
| `graph_runtime.py` | StateGraph asíncrono, checkpoints SQLite e interrupt humano |
| `orchestrator.py` | supervisor y reglas de dominio (router puro, sin juicio) |
| `taskqueue.py` | la cola del sprint: dependencias, bloqueos y tareas de defecto |
| `parallel_tasks.py` | scheduler, Send workers y colector de resultados |
| `scrum.py` / `plan_analysis.py` | prioridad de la ola y análisis de camino crítico |
| `task_worktrees.py` | aislamiento e integracion Git por tarea |
| `optimized_gates.py` | ejecución concurrente y caché segura de revisiones R1/R2 |
| `metrics.py` | tiempos, llamadas y uso de tokens en JSONL |
| `gates/registry.toml` | registro declarativo de gates y su enrutamiento |
| `gates/check_*.py` | checkers deterministas (`G*`) + la revision critica (`R1`) |
| `agents/*.md` | los seis system prompts + el del revisor |
| `examples/fake_agent.py` | agente simulado, para probar el bucle sin tokens |

## Como corre: dos fases

**Fase lineal** — `product → architect → planner → gate humano`. Produce la
especificacion y, sobre todo, `spec/30_plan/tasks.yaml`: el corte del sistema en
tareas con dueno, entregables, dependencias y criterio de aceptacion.

**Sprint de tareas** — LangGraph despacha las tareas con dependencias cerradas.
Las que no comparten entregables corren en una ola maximal, hasta
`runtime.max_concurrency`, cada una dentro de su propio Git worktree. Cuando hay
más candidatas que cupos, el supervisor Scrum prioriza defectos, camino crítico y
tareas que desbloquean más trabajo; el colector integra sus deltas en orden
determinista. Cada tarea es
una llamada acotada a un agente, sus gates y su propio commit. Un
defecto que pertenece a otro nodo no es un reintento a ciegas: se convierte en una
tarea `D-###` para su dueno, y la tarea que lo destapo queda bloqueada hasta que
cierre. Eso es lo que hace que el sistema se comporte como un equipo y no como una
cadena de montaje de una sola pieza.

### Reglas de honestidad

El pipeline no puede reportar un exito que no ocurrio:

- un agente que sale con codigo != 0 **no avanza** — se contabiliza como defecto
  del nodo, se reintenta y, agotado el presupuesto, se escala;
- **G0** exige que cada nodo deje escritos los entregables que declaro. Sin el, un
  agente que muere sin escribir nada pasa todos los gates: no hay artefacto que
  reprobar;
- **G9** ejecuta de verdad la suite del proyecto con los comandos de
  `spec/20_arch/toolchain.yaml`. Mientras ningun gate ejecute, el verde no
  significa nada;
- cada commit contiene solo lo que su nodo posee, y si no hubo commit el reporte
  lo dice.

### Dos categorias de verificacion

`G*` es **codigo determinista sin juicio**: su veredicto no se discute. `R1` es el
**revisor critico** — un modelo leyendo el trabajo de otro modelo despues de
`product`, `architect` y `planner`, para lo que la forma no alcanza: un PRD puede
pasar G1 entero y seguir siendo inservible.

R1 solo puede **anadir** defectos, nunca relajar un `G*`, y esta acotado para que
el ciclo converja: corre al final y solo si los deterministas estan verdes; solo
los hallazgos `blocking` frenan (los `mejora` van al backlog del reporte); tope de
2 rondas por nodo, y solo la ronda que bloquea consume presupuesto. Si el revisor
se cae, el gate pasa y lo registra — los deterministas siguen sosteniendo la
correccion. Con `SDD_REVIEW_MODEL` corre en otro modelo que el autor.

## Un solo comando: `sdd`

Es **una sola aplicación** (paquete `sdd/`) con **un único comando** que sirve para
todo — CLI y panel web. Instálala una vez y úsala:

    pip install -e .          # deja disponible el comando `sdd` (Python >= 3.11 + git)

    sdd demo                  # bucle simulado completo, 0 tokens
    sdd web                   # PANEL WEB para operar el pipeline (abre el navegador)
    sdd run --project mi-app  # corrida real con agentes (necesita API key)
    sdd doctor                # verifica proveedor / API key
    sdd config                # muestra la configuración guardada (llaves enmascaradas)
    sdd show                  # visor de la última corrida (terminal)
    sdd view                  # reporte HTML de la última corrida
    sdd test                  # suite de pruebas del plano de control
    sdd gates --node dev_backend --workdir <repo>

Sin instalar, lo mismo con `python -m sdd <cmd>`. `sdd demo` termina en
`estado final: done | tareas: 5/5`. El guion no es
decorativo: construye un proyecto Python real, ejercita una violación de propiedad
revertida por G7, y planta un defecto de dominio que **solo una prueba ejecutada
revela** — G9 lo detecta, el supervisor lo atribuye a `src/domain/` y abre una
tarea de defecto para el backend, que la cierra y desbloquea a QA. No consume tokens.

## Estructura de la aplicación

    pyproject.toml           empaquetado + comando `sdd`
    sdd/                      la aplicación (un solo paquete)
      __main__.py            punto de entrada único (sdd / python -m sdd)
      cli.py                 dispatcher de subcomandos (CLI)
      server.py              panel web (stdlib http.server)
      graph_runtime.py       StateGraph async + SQLite + interrupt humano
      parallel_tasks.py      scheduler + Send workers + colector
      scrum.py               prioridad determinista de tareas listas
      plan_analysis.py       camino crítico, ancho y avisos del DAG
      task_worktrees.py      worktree, integración y limpieza por tarea
      optimized_gates.py     gates G* concurrentes + caché R1/R2
      metrics.py             telemetría local append-only
      execution_journal.py   idempotencia de visitas externas completadas
      orchestrator.py        supervisor y reglas deterministas de ruta
      taskqueue.py           cola de tareas: dependencias, bloqueos, defectos
      agent.py               agente real (llama al modelo, escribe archivos)
      providers.py           Anthropic + modelos chinos (compat. OpenAI)
      config.py              configuración persistente (config.json)
      report.py              reporte HTML + reporte final de una corrida
      pipeline.toml          grafo, propiedad de paths, entregables, presupuesto
      gates/                 registry.toml + check_*.py + _lib.py
      agents/                los seis system prompts + reviewer.md (*.md)
      examples/fake_agent.py agente simulado (modo demo)
      intake.yaml            idea de ejemplo
    tests/                   suite (sdd test)

Los proyectos generados van por defecto a `project/<nombre>` en la raíz del repo
(configurable). `config.json` (con las llaves) vive también en la raíz y está en
`.gitignore`.

## Modo real: agentes que llaman a un modelo

El motor real vive en `sdd/providers.py` (capa de proveedores) y `sdd/agent.py` (el
nodo que lee la idea, llama al modelo y escribe archivos en sus paths). Soporta:

| SDD_PROVIDER | Modelo | Variable de key | Cómo |
|---|---|---|---|
| `anthropic` (default) | `claude-opus-5` (o `SDD_MODEL`) | `ANTHROPIC_API_KEY` | SDK oficial |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` | compat. OpenAI |
| `qwen` | `qwen-max` | `DASHSCOPE_API_KEY` | compat. OpenAI |
| `glm` | `glm-4-plus` | `ZHIPUAI_API_KEY` | compat. OpenAI |
| `kimi` | `moonshot-v1-32k` | `MOONSHOT_API_KEY` | compat. OpenAI |
| `openai` | `SDD_MODEL` | `SDD_API_KEY` + `SDD_BASE_URL` | cualquier endpoint compat. |

La forma más simple es el panel: `sdd web`. Desde la CLI, define la key (por
variable de entorno o guardándola con `sdd config` / el panel) y corre:

    # PowerShell:
    $env:ANTHROPIC_API_KEY = "sk-ant-..."      # o DEEPSEEK_API_KEY, etc.
    sdd doctor                                 # confirma que está listo
    sdd run --project mi-app                   # → project/mi-app

Si falta la key, el pipeline **no arranca**: falla rápido con el mensaje correcto
(sin default silencioso, como manda `CLAUDE.md`). `run` siembra un repo git limpio,
copia tu idea a `spec/00_intake.yaml`, y corre la máquina de estados con agentes
reales. Cada nodo escribe artefactos reales; los gates los verifican; el supervisor
enruta y reintenta; se detiene en el gate humano (nodo `human_gate`). Consúmelo después con
`sdd show --workdir project/mi-app` o `sdd view --workdir project/mi-app`.

Con Anthropic, el prompt estable usa caché efímera por defecto; se puede desactivar
con `SDD_PROMPT_CACHE=0`. `SDD_REVIEW_MODEL` permite reservar un modelo más rápido
para R1/R2 y para el desempate Scrum. Toda llamada —incluidas revisiones— registra
latencia, tokens normales y tokens de caché en `.agent/usage.jsonl`; los tiempos de
nodos, gates y checkpoints quedan en `.agent/metrics.jsonl`.

## Contrato de un checker

Imprime en stdout `{"findings":[{"file","line","rule","evidence"}]}` y sale con codigo
1 si hay hallazgos. Nada mas: sin prosa, sin sugerencias de implementacion. Asi puedes
reemplazar `check_file_size.py` por `eslint --format json` con un adaptador de 10 lineas
sin tocar el orquestador.

## Sustituciones para uso real

| Gate | Checker actual | Reemplazo en produccion |
|---|---|---|
| G0 | `check_deliverable.py` | se queda: nada lo sustituye |
| G1 / G8 | `check_traceability.py` | `behave`/`cucumber` con reporte de trazabilidad |
| G2 | `check_arch_spec.py` | `spectral lint openapi.yaml` + `mmdc` + este para NFR/ADR |
| G4 | `check_file_size.py` | `eslint` (max-lines, complexity) / `ruff` / `tsc --noEmit` |
| G5 | `check_hardcoding.py` | `gitleaks detect` + este para el contrato de entorno |
| G6 | `check_imports.py` | `tsc --noEmit` / `mypy`; anadir `semgrep --config p/owasp-top-ten` |
| G7 | `check_test_integrity.py` | se queda: es propiedad de paths, no analisis |
| G9 | `check_suite.py` | ya ejecuta lo real; anadir `--cov` + `diff-cover` |
| G10 | `check_plan.py` | se queda: valida el contrato del bucle de tareas |
| R1 | `check_review.py` | no es un gate: es un modelo. Ajusta la rubrica de `agents/reviewer.md`, no lo sustituyas por una herramienta |
| R2 | `check_review.py` | reseña de código por tarea (dev/qa). Mismo mecanismo que R1, estado por tarea; en modo real cuesta una llamada por tarea |

G9 ejecuta, en orden, los comandos que `spec/20_arch/toolchain.yaml` declare:
`install → lint → typecheck → security → test → coverage`. Cada paso es opcional
salvo `test`; si su binario no está instalado, G9 escala en vez de silenciarlo. Ahí
es donde enchufas eslint/ruff, tsc/mypy, gitleaks/semgrep y `--cov-fail-under`.

## Invocacion real del agente

`pipeline.toml` seccion `[runtime]`, claves `agent_cmd`, `max_concurrency` y
`gate_concurrency`. El runtime usa `ainvoke`, `AsyncSqliteSaver` y durabilidad
asíncrona; los gates deterministas se ejecutan en paralelo después de G7. R1/R2
solo corren cuando todos los G* están verdes y los resultados verdes se reutilizan
mientras el hash de tarea, prompt y artefactos no cambie.

### Protección contra deadlocks y esperas indefinidas

Cada proyecto tiene un lease entre procesos adquirido antes de leer su estado.
Una segunda corrida, `sdd gates` o `sdd clean` sobre el mismo proyecto falla
rápido en vez de competir por SQLite o por el índice Git. En el panel, el estado
`starting` se reserva bajo un lock antes de sembrar o reanudar, así dos POST
simultáneos no pueden arrancar dos procesos.

Los límites `agent_timeout_seconds`, `gate_timeout_seconds`,
`provider_timeout_seconds`, `git_timeout_seconds` y `lease_wait_seconds` viven en
`pipeline.toml [runtime]`. Un timeout de agente se enruta como defecto; uno de
gate produce un gate rojo; Git deshabilita prompts interactivos y devuelve un
fallo acotado. El orden es siempre lease del proyecto → operación Git; el lock de
métricas solo protege una escritura y nunca se conserva mientras se espera un
subproceso.
Verifica los flags contra
https://docs.claude.com/en/docs/claude-code/overview antes de usarlo: cambian entre
versiones. El aislamiento por path se consigue con `git worktree` por tarea mas los
permisos de herramienta del CLI; `pipeline.toml` declara la propiedad, y G7 la verifica
a posteriori con `git status`. Las dos capas son necesarias: la preventiva puede
configurarse mal, la verificadora no depende del agente.

## Limites conocidos

- **SQLite es local.** `.agent/checkpoints.sqlite` es adecuado para esta aplicación
  local asíncrona de un proceso. Un servicio con varios procesos debe usar un checkpointer
  PostgreSQL; `state.json` es solo la proyeccion legible para CLI y panel.
- **La ventana exactamente-una-vez depende del proveedor.** El journal evita
  repetir una visita completada si el proceso cae antes del checkpoint, pero un
  corte entre la respuesta del modelo y el journal solo se cierra con una
  idempotency key soportada por la API externa.
- **El coste está instrumentado, no expresado en USD.** El límite de llamadas
  gobierna los agentes principales y el de tokens incluye también revisiones; el
  precio depende del proveedor y debe calcularse externamente.
- **Sin retroalimentacion entre corridas**: un defecto que se repite en todos los
  proyectos deberia acabar en el prompt del nodo, y hoy no lo hace.
- **El modo real aun necesita medicion.** El runtime, los gates, la recuperacion y
  el paralelismo se verifican con agentes simulados; falta medir calidad y coste
  con modelos reales.

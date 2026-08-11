# Glosario

Este glosario define los términos de dominio de AutoScrum tal como se usan
**en este repositorio**, no su acepción genérica. Cuando un término tiene un
significado preciso en código, se referencia el archivo; cuando se explica
con más detalle en otro documento, se apunta ahí (`README.md`, `CLAUDE.md`,
`HANDOFF.md`, y los documentos que cubren arquitectura y gates en detalle:
`docs/architecture.md` y `docs/gates.md`).

Orden alfabético. Los términos que se prestan a confusión con otro parecido
incluyen una nota **"No confundir con"**.

---

## `apply_decision` (nodo)

Último nodo de la topología evaluador-optimizador de una unidad de trabajo.
Se ejecuta después de `human_review` y materializa la decisión (`accept` o
`reject`): si acepta, comitea e integra el resultado; si rechaza, devuelve
feedback al generador. Vive en `sdd/runtime/work_unit_graph.py` (fase de
tarea) y en `sdd/runtime/graph_runtime.py` (fase lineal de especificación).
Ver también **Evaluador-Optimizador**.

## Autoaprobación / modo autónomo

Modo de ejecución en el que el propio orquestador firma las decisiones de
`human_review` (genera un registro `accept` auditable) en lugar de detener el
grafo a esperar una decisión humana. Se activa con `--autonomous` o
`--auto-approve-human` en la CLI; internamente ambos flags colapsan en la
variable `auto_human` (`sdd/presentation/cli.py`, línea ~471:
`auto_human = args.auto_approve_human or args.autonomous`). **No** significa
saltarse `evaluate` ni `human_review`: esos nodos siempre corren; lo que
cambia es quién firma la decisión. Un gate fallido sin corrección posible, un
error externo o el presupuesto agotado siguen deteniendo la ejecución aunque
el modo esté activo.

## Budget / presupuesto

Sección `[budget]` de `sdd/pipeline.toml`: techos duros para toda la corrida
— `max_retries_per_gate`, `max_agent_calls`, `max_wall_minutes`,
`max_defect_tasks`, `max_output_tokens`, `max_delegation_depth`,
`max_subtasks_per_task`, `max_delegated_tasks`. Agotar cualquiera de estos
límites produce un escalamiento explícito (`ESCALATE_HUMAN`), nunca un
reintento silencioso indefinido. **Prohibido tocar estos valores para que un
cambio propio pase** (regla dura de `CLAUDE.md`).

## Camino crítico (`critical_path`)

Profundidad máxima del DAG de tareas: la cadena de dependencias más larga
entre todas las tareas de `spec/30_plan/tasks.yaml`. Se calcula en
`sdd/runtime/plan_analysis.py::analyze()` (campo `critical_path`, expuesto en
logs como `PLAN_PERFORMANCE camino_critico=...`) como diagnóstico **no
bloqueante** antes del gate humano; no cambia el plan, solo lo reporta.

**No confundir con** "FR crítico" (ver **FR-### / `@critical`**): son dos
usos legítimos pero distintos de la palabra "crítico" en el mismo subsistema.
`camino_critico` es una propiedad estructural del grafo de dependencias;
"FR crítico" es una etiqueta de negocio en un escenario Gherkin que el Scrum
prioritiza. El propio prompt de `scrum.py` los menciona juntos como criterios
separados: *"Prioriza defectos que desbloquean, FR criticos y camino
critico"* (`sdd/runtime/scrum.py`, función `_model_order`).

## Checkpoint

Estado durable del grafo LangGraph persistido en SQLite
(`.agent/checkpoints.sqlite`, constante `CHECKPOINT_PATH` en
`sdd/runtime/graph_runtime.py`) vía `AsyncSqliteSaver`. Es la fuente de
verdad para reanudar una corrida exacta tras un crash, una desconexión o una
espera humana — no `.agent/state.json`, que es solo su **proyección legible**
para humanos y para el panel (ver **`state.json`**). `sdd resume` continúa
desde el checkpoint guardado; en Windows/local es SQLite de un solo proceso,
por lo que un despliegue multiproceso necesitaría migrar a Postgres (ver
`HANDOFF.md`, sección "Lo que NO está hecho").

## Chronicle

Almacenamiento append-only de cada invocación a un modelo: prompt, contexto,
respuesta y metadatos, uno por **visita** (ver **`visit_id`**). Vive en
`.agent/chronicle/<visit-id>/` y se implementa en `sdd/core/chronicle.py`
(`CHRONICLE_ROOT`, `_chronicle_dir`). Se consulta con
`sdd chronicle --workdir ... --recent N` o `--visit-id <id> --full`
(`sdd/presentation/cli.py::chronicle_cmd`). Distinto de **Lifecycle**: el
Chronicle guarda el contenido de la llamada al modelo; el Lifecycle guarda
los eventos de estado de una tarea (sin necesariamente incluir prompts).

## Control Tower

Nombre del panel web de observabilidad (`sdd web` / `sdd serve`, servido en
`http://127.0.0.1:8770`). Incluye la vista **Equipo en vivo** (estado por
SSE vía `/events`), Historial, Resultados y Configuración. Código en
`sdd/control_tower/` (`http.py`, `runtime.py`, `views.py`,
`agent_instances.py`, `observability.py`) y `sdd/presentation/webpage.py`.
No confundir con el **orquestador**: el Control Tower es la interfaz; el
orquestador (`sdd.runtime.orchestrator.main`) es quien de verdad dirige la
ejecución y puede invocarse sin panel (CLI pura).

## `D-###` (tarea de defecto)

Identificador formal de una tarea generada automáticamente cuando un gate
detecta un fallo que **no pertenece al dueño** de la tarea que lo produjo.
El ejemplo canónico de la demo es `D-001`, abierta contra `dev_backend`
cuando `G9` revela un bug de dominio que QA no puede arreglar porque no le
pertenece `src/domain/` (`sdd/examples/fake_agent.py`, `HANDOFF.md`). La
tarea original que detectó el defecto queda en estado `blocked` hasta que la
tarea `D-###` se cierra (`sdd/runtime/taskqueue.py`, docstring de estados).
La lógica de creación vive en `sdd/runtime/workflow_defects.py`
(`classify_defect`, `delegate_defect`, `escalate_defect`, `retry_defect`).

**No confundir con Delegación** (ver más abajo): un `D-###` es la *tarea*
resultante; "delegar" es la *acción* del orquestador de crear esa tarea y
asignarla a otro nodo.

## Defecto vs. hallazgo (`finding`)

Un **hallazgo** (`finding`) es la unidad atómica que reporta un gate:
`{file, line, rule, evidence}` (contrato de salida de todo checker, ver
`sdd/gates/registry.toml`, cabecera). Un **defecto** es la clasificación que
el orquestador hace de uno o más hallazgos para decidir qué pasa después:
reintento (mismo nodo), delegación (`D-###` a otro nodo) o escalamiento
(`sdd/runtime/workflow_defects.py::classify_defect`). Los hallazgos de `R1`/
`R2` solo pueden **añadir** defectos con severidad `blocking`; el resto
(`mejora`) no frena nada (`CLAUDE.md`, regla 0).

## Delegación

Término con **dos sentidos distintos** en este repo — ambos activos, ninguno
más "correcto" que el otro, pero hay que distinguirlos por contexto:

1. **Delegación de defecto**: el orquestador reasigna un hallazgo a su dueño
   real creando una tarea `D-###` (`sdd/runtime/workflow_defects.py::
   delegate_defect`). Es una decisión de enrutamiento de fallos.
2. **Delegación jerárquica de subtareas**: un agente propone dividir su
   propia tarea en hijos con linaje persistente (`sdd/runtime/delegation.py`,
   docstring: *"Los agentes proponen; el orquestador decide"*). Está acotada
   por `max_delegation_depth`, `max_subtasks_per_task` y
   `max_delegated_tasks` (sección `[budget]` de `pipeline.toml`); una
   propuesta que amplía su propio alcance se rechaza (`DelegationError`).

Ambas conviven en el mismo pipeline pero resuelven problemas distintos: la
primera mueve trabajo *entre nodos ya existentes*; la segunda crea
*nuevas tareas hijas* bajo el mismo nodo.

## Escalar / Escalamiento (`ESCALATE_HUMAN`)

Salida explícita del pipeline cuando un defecto no tiene solución dentro del
presupuesto (`max_retries_per_gate` agotado) o cuando ocurre un error externo
irrecuperable. Detiene la ejecución con causa accionable en vez de reintentar
indefinidamente o fingir éxito. Es uno de los tres destinos posibles de
`classify_defect` junto con `retry` (mismo nodo) y `delegate` (`D-###` a otro
dueño). Ver diagrama de flujo de decisión en `README.md`.

## Evaluador-Optimizador (topología `generate → evaluate → human_review → apply_decision`)

Patrón que sigue **toda** unidad de trabajo del pipeline — nodos de
especificación (`product`, `architect`, `planner`) y tareas de sprint
(`dev_backend`, `dev_frontend`, `qa`, incluidas tareas de defecto). Cuatro
pasos:

- `generate`: el agente produce el artefacto.
- `evaluate`: gates deterministas (y R1/R2 cuando aplica) lo califican.
- `human_review`: punto HITL — siempre se alcanza tras una evaluación
  aprobada, incluso en modo autónomo (ahí la firma es automática, no
  ausente).
- `apply_decision`: materializa `accept` (commit/integración) o `reject`
  (feedback de vuelta al generador).

Implementado en `sdd/runtime/graph_runtime.py` (fase lineal) y
`sdd/runtime/work_unit_graph.py::WorkUnitGraph` (fase de sprint). Ver
`README.md`, sección "Evaluador-Optimizador integrado".

## `FR-###` / `@critical`

`FR-###` es el identificador de un requerimiento funcional, referenciado
desde tareas (`fr_refs`) y commits (regla de `CLAUDE.md`: "Commits...
referenciando `FR-###` y `task_id`"). La etiqueta Gherkin `@critical` en un
archivo `.feature` marca ese FR como prioritario para el Scrum: se extrae con
`sdd/runtime/scrum.py::read_critical_frs`, que escanea
`spec/10_product/features/*.feature` buscando líneas con `@critical` y captura
el `FR-###` asociado. Ver **Camino crítico** para la nota de desambiguación.

## `G0`…`G10` — Gates deterministas

Ver el detalle de cada gate en `docs/gates.md`. En resumen: son *código*, no
modelos, y su contrato de salida es fijo — imprimen
`{"findings":[{"file","line","rule","evidence"}]}` y salen con código `1` si
hay hallazgos (`sdd/gates/registry.toml`, cabecera). Definidos como
`[[gate]]` en `sdd/gates/registry.toml`: `G0` (entregable presente), `G1`/`G8`
(trazabilidad), `G2` (spec técnica), `G4` (tamaño/estático), `G5`
(secretos/entorno), `G6` (imports), `G7` (propiedad de paths e integridad de
tests/spec), `G9` (ejecuta el toolchain real), `G10` (plan ejecutable). Un
`G*` **nunca se relaja** desde una tarea (regla dura de `CLAUDE.md`); si `R1`
o `R2` contradice a un `G*`, gana el `G*`.

## `human_gate` (nodo)

Nodo de tipo `human` (`type = "human"` en `sdd/pipeline.toml`) que cierra la
fase lineal de especificación, después de `planner`. Registra una
autoaprobación en modo autónomo o interrumpe el grafo (`interrupt()` de
LangGraph) hasta recibir `sdd resume --decision accept|reject`. La firma
queda en `state.json` con `spec_hash` (ver esa entrada). **No es un gate
`G*`** — es un nodo de flujo distinto, y `CLAUDE.md` prohíbe explícitamente
llamarlo "G3" porque ese identificador no existe en el registro.
Implementado en `sdd/runtime/human_approval.py`.

**No confundir con** `human_review`: `human_gate` es el nodo fijo de la fase
lineal (uno solo, tras `planner`); `human_review` es el paso genérico que
*toda* unidad de trabajo atraviesa dentro de la topología
evaluador-optimizador (puede ocurrir muchas veces, una por tarea o lote).

## Lease (`RunLease`)

Exclusión mutua **entre procesos** sobre el mismo proyecto, implementada con
un lock de archivo (`sdd/core/run_lease.py::RunLease`, identidad derivada de
`hashlib.sha256` sobre la ruta canónica del workdir, archivo en
`.sdd-locks/<hash>.lock`). Impide que dos invocaciones de `sdd run`/`gates`/
`clean` compitan por el mismo SQLite o el mismo working tree. Se adquiere
antes de leer `state.json`.

**No confundir con** la **reserva `starting`** del Control Tower
(`sdd/control_tower/runtime.py::claim_run` / `release_claim`): esa es una
reserva **en memoria, dentro del mismo proceso del servidor web**, que evita
que dos `POST /run` concurrentes disparen dos subprocesos antes de que
cualquiera de ellos llegue a tomar el `RunLease` real. `HANDOFF.md` la llama
"la reserva `starting` del panel es atómica".

## Lifecycle

Journal append-only por tarea: `.agent/tasks/<task_id>/lifecycle.jsonl`.
Cada línea es un evento con timestamp, nodo, resultado, hallazgos y cambios
de estado (`sdd/core/lifecycle.py`, constante `LIFECYCLE_FILE`). Permite
reconstruir la vida completa de una tarea sin parsear `state.json` ni el
historial global. Se consulta con
`sdd tasks --workdir ... --task-id T-003` (ver `README.md`, sección
Observabilidad). No confundir con **Chronicle** (contenido de llamadas al
modelo) ni con el ciclo de vida general del pipeline (fase lineal + sprint).

## Nodo (`[[node]]` en `pipeline.toml`)

Unidad de la partitura del pipeline: `product`, `architect`, `planner`,
`human_gate`, `dev_backend`, `dev_frontend`, `qa`. Cada uno declara `writes`
(paths que posee), `must_produce` (entregables exigidos por `G0`), `gates`
(lista de checks que corre) y `next` (siguiente nodo en la fase lineal).
Los nodos con `task_node = true` no tienen turno fijo: el bucle de tareas
(`ParallelTasks`) los invoca una vez por cada tarea que el plan les asigna.
Definido en `sdd/pipeline.toml`.

**No confundir con Tarea/`task_id`**: un nodo es un *rol* (ej. "el backend");
una tarea (`task_id`) es una *unidad de trabajo concreta* asignada a ese rol
por el plan (`T-004`, `D-001`, etc.). Un nodo ejecuta muchas tareas a lo largo
de una corrida.

## Orquestador (`sdd.runtime.orchestrator.main`)

Código determinista — no un agente de IA — que dirige la ejecución completa:
adquiere el `Lease`, carga configuración y estado, activa el modo autónomo
cuando corresponde y entrega el control al grafo LangGraph
(`sdd/runtime/orchestrator.py`). `product`, `architect` y `planner` son
especialistas subordinados a su flujo, no directores del sistema (ver tabla
"Quién dirige la orquesta" en `README.md`).

## PipelineState

`TypedDict`/estado compartido de LangGraph que fluye por todos los nodos del
grafo (`sdd/runtime/graph_state.py` y `sdd/runtime/workflow_state.py`).
Contiene el cursor de ejecución, la tarea activa, el lote paralelo, el
historial de intentos y toda referencia serializable necesaria para
reanudar. El estado **no** contiene el código ni las specs en sí — esos viven
como archivos en el repo (ver **repo-as-state**); el `PipelineState` solo
guarda punteros, reportes y contadores.

## `R1` / `R2` — Revisión crítica

Categoría de verificación **distinta** de los `G*`: un modelo con criterio
que revisa el trabajo ya aprobado por los gates deterministas. `R1` revisa
`product`/`architect`/`planner` (nivel especificación); `R2` revisa las
tareas de código (`dev_backend`/`dev_frontend`/`qa`). Ambos comparten el
checker `sdd/gates/check_review.py` con `--label R1`/`R2`
(`sdd/gates/registry.toml`). Reglas que los distinguen de un `G*`:

- Solo corren si los `G*` del nodo ya están verdes (`skip_if_prior_failed`).
- Solo pueden **añadir** hallazgos, nunca relajar un gate determinista.
- Solo los hallazgos `blocking` frenan; `mejora` no bloquea nada.
- Si `R1`/`R2` y un `G*` se contradicen, gana el `G*` (`CLAUDE.md`, regla 0).
- Revisiones verdes se cachean por contenido (`HANDOFF.md`), para no
  re-pagar el costo de revisar lo mismo dos veces.

## `repo-as-state`

Principio rector del sistema: los agentes no se pasan contexto por chat,
leen y escriben artefactos versionados en git bajo `spec/`, `src/`,
`tests/`. El orquestador solo transporta punteros (`spec_hash`, `task_id`,
rama) y decisiones de ruta. Auditoría = `git log`; rollback = `git revert`
(`CLAUDE.md`, `HANDOFF.md`, primer párrafo de ambos).

## Scrum (`sdd/runtime/scrum.py`)

**No es el scheduler completo** — es específicamente el módulo de
**prioridad**: dado un conjunto de tareas ya listas (dependencias cerradas,
sin solapamiento de entregables — decisiones ya tomadas por `taskqueue`/`G7`/
worktrees), decide en qué *orden* se ejecutan. Usa un orden determinista
(`_deterministic`: defectos primero, luego FR `@critical`, luego más
desbloqueos, luego id) y, si hay más candidatas que cupos disponibles, puede
delegar el desempate a un modelo rápido (`_model_order`, opcional). El
scheduler real — quién puede correr en paralelo, aislamiento en worktrees,
integración de resultados — es `sdd/runtime/parallel_tasks.py::ParallelTasks`.
El README llama a este conjunto "Scrum scheduler" en el diagrama de
arquitectura, pero en código la responsabilidad está repartida entre
`scrum.py` (orden), `taskqueue.py` (elegibilidad y estado) y
`parallel_tasks.py` (ejecución concurrente).

## `spec_hash`

Hash SHA-256 del contenido completo de `spec/` en el momento de una
aprobación (`sdd/runtime/human_approval.py::spec_hash`). Se persiste junto a
cada decisión de `human_gate`/`human_review` para poder detectar si la
especificación cambió después de haberse firmado. Es uno de los pocos
"punteros" que el orquestador transporta explícitamente, coherente con
**repo-as-state**.

## Sprint durable

Nombre de la **segunda fase** del pipeline (tras la fase lineal
`product → architect → planner → human_gate`). LangGraph despacha con `Send`
la ola máxima de tareas con huellas no superpuestas, acotada por
`max_concurrency`; Scrum prioriza defectos y camino crítico cuando faltan
cupos; el colector integra los deltas en orden determinista
(`HANDOFF.md`, "Arquitectura en dos fases"). "Durable" se refiere a que cada
tarea persiste su propio checkpoint, presupuesto y lifecycle, de modo que una
corrida interrumpida se reanuda exactamente donde quedó sin perder trabajo ya
comiteado.

## `state.json`

Proyección legible en JSON del estado global de la corrida
(`.agent/state.json`), pensada para humanos, CLI y el Control Tower. **No es
la fuente de verdad** — eso es el `Checkpoint` de LangGraph en
`.agent/checkpoints.sqlite`; `state.json` es una vista derivada
(`HANDOFF.md`: *"el estado durable vive en `.agent/checkpoints.sqlite`;
`.agent/state.json` es su proyección legible"*).

## Tarea / `task_id`

Una **tarea** es cualquier unidad de trabajo del plan
(`spec/30_plan/tasks.yaml`), con estados `pending`, `done`, `blocked` o
`needs_input` (`sdd/runtime/taskqueue.py`, docstring). `task_id` es su
identificador formal (`T-004`, `D-001`, …) usado para referenciarla en
commits, en `.agent/tasks/<task_id>/lifecycle.jsonl` y en
`.agent/current_task.json` (el archivo que el agente lee para saber qué le
toca). "Tarea" en prosa suele ser genérico; `task_id` es siempre el
identificador exacto — no son intercambiables al citar evidencia (`CLAUDE.md`
exige commits que referencien el `task_id`, no una descripción libre).

## `taskqueue`

Módulo que materializa "el sprint, en datos": carga y valida
`spec/30_plan/tasks.yaml`, resuelve qué tareas están listas (dependencias en
`done`) y mantiene sus estados (`sdd/runtime/taskqueue.py`). No decide
*orden* de prioridad (eso es **Scrum**) ni *paralelismo real* (eso es
`ParallelTasks`); decide elegibilidad.

## Tier / Routing adaptativo

Clasificación de capacidad de un modelo: `economy`, `balanced`, `frontier`
(`TIERS` en `sdd/integrations/model_router.py`). El modo `adaptive` asigna
un tier por rol (`DEFAULT_ROLE_TIERS`: `product`/`architect`/`planner` piden
`frontier`; backend/frontend arrancan en `economy`; QA en `balanced`) y
permite un único escalado a `frontier` si una tarea falla su propio gate o
`R2`. La función `classify_model` es la única fuente de verdad para el tier
de un modelo — la usa tanto el catálogo de discovery como el runtime, para
que nunca reporten tiers distintos sobre el mismo modelo; un tier fijado a
mano en `config.json` siempre gana sobre el clasificador automático. Ver
`README.md`, sección "Enrutamiento adaptativo de modelos".

## `visit_id`

Identificador de una **visita**: una invocación puntual a un agente/modelo
dentro de una unidad de trabajo. Se deriva de `run_id`, `cursor` y la tarea
activa (`sdd/runtime/graph_state.py::visit_id`). Es la clave de directorio
bajo `.agent/chronicle/<visit-id>/` (ver **Chronicle**) y permite distinguir,
dentro de la misma tarea, cada intento/reintento como una visita distinta.

## Work unit / `WorkUnitGraph`

Subgrafo LangGraph que ejecuta la topología evaluador-optimizador para
**una** unidad de trabajo — una tarea de sprint o, en su variante lineal, un
nodo de especificación (`sdd/runtime/work_unit_graph.py::WorkUnitGraph`,
docstring: *"Nodos de una unidad; el grafo, no Python, gobierna sus
rondas"*). Es el nivel de granularidad en el que corren `generate`,
`evaluate`, `human_review` y `apply_decision` para esa tarea específica.

## Worktree

Working tree de Git aislado por tarea (`git worktree`, no una copia manual).
Cada tarea lista para ejecutarse en el sprint corre en su propio worktree
para evitar colisiones entre tareas paralelas; la integración de vuelta a la
rama principal ocurre en orden determinista tras pasar los gates
(`sdd/runtime/task_worktrees.py`). Distinto de un working tree genérico de
Git: aquí el término implica específicamente el aislamiento por tarea que
habilita el paralelismo seguro del **Sprint durable**.

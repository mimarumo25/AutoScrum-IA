# Diseño — Optimización de agentes, paralelismo nativo LangGraph y supervisor scrum

- **Fecha:** 2026-07-26
- **Rama:** `feat/langgraph-migration`
- **Estado:** propuesta para aprobación
- **Autor:** sesión Claude Code (goal `/goal`)

## 1. Problema y objetivo

El pipeline SDD ya migró a LangGraph: el *sprint* de tareas usa `Send` para
fan-out y worktrees aislados por tarea. Pero la delegación paralela está
limitada por decisiones conservadoras del scheduler, la fase lineal no usa
aristas nativas, y no hay una capa de "atención" que priorice qué tareas
independientes correr primero.

El objetivo del operador (verbatim del `/goal`):

> optimizar los agentes y cada uno de los procesos; todos los agentes tienen
> todos los permisos; usar tecnología LangGraph nativa para correr el flujo; el
> agente de producto / agente scrum debe estar atento y poder **delegar tareas en
> paralelo que no estén en el mismo archivo ni tengan dependencia**.

Traducción a requisitos verificables:

- **R1** — Operar el pipeline bajo Claude Code sin fricción de permisos, **sin
  relajar** la propiedad de paths por nodo (G7 intacto).
- **R2** — Delegar en paralelo el **máximo** conjunto de tareas cuyas huellas de
  archivo no se solapan y cuyas dependencias están cerradas.
- **R3** — Una capa de supervisión ("scrum") que **priorice** el orden de
  ejecución sin decidir jamás la seguridad de solapamiento.
- **R4** — Todo se ejecuta con LangGraph nativo (`Send`, checkpoints, supersteps).

## 2. Principios que NO se tocan

Estas invariantes del sistema son la razón de su valor y quedan intactas:

- **Dos categorías de verificación.** Los gates `G*` son deterministas e
  incuestionables; el juicio LLM (`R1`/`R2`/scrum) solo puede **añadir** o
  **ordenar**, nunca relajar un gate ni la seguridad de solapamiento.
- **Propiedad de paths por nodo** (declarada en `pipeline.toml`, verificada por
  G7). El scheduler puede paralelizar, pero cada worker sigue escribiendo solo en
  sus paths.
- **Repo-as-state.** Los agentes no se pasan contexto por chat; el scheduler solo
  transporta punteros y decisiones de ruta.
- **Reglas de honestidad.** Un agente `exit != 0` no avanza; verde vacío no es
  verde; cada commit contiene solo lo de su dueño.

## 3. Diagnóstico (estado actual)

| # | Límite | Ubicación | Efecto |
|---|--------|-----------|--------|
| L1 | Barrera por lote: `collect` espera a todos los workers antes de re-planificar | `parallel_tasks.py::collect` | una tarea lenta ociosa los slots liberados |
| L2 | `safe_batch` greedy con tope `max_concurrency=3` | `task_worktrees.py::safe_batch` | se paraleliza menos de lo posible |
| L3 | Los defectos corren solos | `safe_batch` (`kind == "defect"`) | dos defectos disjuntos se serializan |
| L4 | Footprint grueso: sin `deliverables` cae al prefijo del nodo | `task_worktrees.py::_footprint` | falsos solapamientos → serialización |
| L5 | Sin prioridad/ruta crítica entre tareas de plan | `taskqueue.py::runnable` | retrasa el camino crítico |
| L6 | Fase lineal sobre bucle `cursor`/`bootstrap`, no aristas nativas | `graph_runtime.py` | menos idiomático (fuera de alcance, ver §7) |
| L7 | Sin `.claude/settings.json` | — | fricción de permisos al operar bajo Claude Code |

## 4. Diseño de la solución

### P1 — Permisos del harness, G7 intacto (R1)

Los agentes reales (`agent.py`) son llamadas LLM crudas que emiten bloques de
archivo; lo que acota dónde escriben es el filtro de `write_files` + G7, **no** el
sistema de permisos de Claude Code. Por tanto "todos los permisos" se resuelve en
la capa del harness sin diluir la integridad.

- Crear `.claude/settings.json` en la raíz del plano de control con
  `permissions.allow` de lo que el pipeline ejecuta: `git`, `python`/`python -m
  sdd`, los gates, operaciones de worktree.
- **Antes de escribirlo**, verificar el esquema real (`permissions`, `allow`,
  formato de las reglas) **citando las URLs de la documentación oficial de Claude
  Code**, tal como exige `CLAUDE.md` ("Primera tarea al iniciar en este repo").
- Documentar explícitamente en el propio archivo que la propiedad de paths por
  nodo **no** se toca aquí: vive en `pipeline.toml` + G7.

**Aceptación:** `.claude/settings.json` existe, parsea, su esquema está
respaldado por URLs oficiales citadas, y G7 sigue revirtiendo cualquier escritura
fuera de propiedad (probado por el test de propiedad existente).

### P2 — Conjunto independiente máximo, con defectos y footprint fino (R2)

Reemplazar `safe_batch` por una selección que, dado el conjunto `ready`
(ordenado por prioridad, ver P4), elija el **máximo** subconjunto cuyas huellas
sean dos-a-dos disjuntas, hasta `max_concurrency`.

- **Footprint a nivel de archivo.** Cuando la tarea declara `deliverables`
  (el planner lo exige), la huella son esos archivos concretos, no el prefijo del
  nodo. Solo cae al prefijo del nodo cuando no hay `deliverables` (caso raro, se
  registra en el log para visibilidad).
- **Defectos en el lote.** Se elimina la regla "defecto corre solo". Los defectos
  siguen priorizados (van primero en `ready`), pero un defecto que no solapa con
  otra tarea lista entra en la misma ola.
- **Selección determinista.** Algoritmo: recorrer `ready` en orden de prioridad;
  añadir una tarea si su huella no solapa ninguna ya seleccionada; parar en
  `max_concurrency`. Es un conjunto independiente maximal en orden de prioridad —
  determinista y estable. (No se busca el máximo NP-duro global: el maximal en
  orden de prioridad es suficiente y predecible.)

> Nota de honestidad: "máximo" en la práctica = **maximal en orden de
> prioridad**, no el óptimo NP-duro. Se documenta así en el código.

**Aceptación:** dado un `ready` con N tareas de huellas disjuntas y
`max_concurrency ≥ N`, la ola contiene las N. Dos defectos de archivos distintos
entran juntos. Dos tareas que tocan el mismo archivo nunca entran juntas.

### P3 — Olas anchas configurables (reduce L1) (R4)

Cada superstep de `schedule` despacha la ola independiente máxima de **todas** las
tareas runnable (plan + defectos), no un lote fijo de 3.

- La barrera de LangGraph por superstep se mantiene: es su modelo BSP y lo que da
  durabilidad de checkpoint. El coste se reduce de "la tarea más lenta de un lote
  de 3" a "la más lenta de una ola tan ancha como la seguridad permita".
- `max_concurrency` sube a un default configurable más alto (propuesto: 6) en
  `pipeline.toml`, ajustable a 1 sin cambiar la semántica.
- Un pool totalmente sin barrera queda **fuera de alcance** (mayor riesgo, cambia
  el modelo de durabilidad); se documenta como límite conocido.

**Aceptación:** con `SDD_FAKE_PARALLEL` extendido a ≥3 tareas disjuntas listas a
la vez, el log `BATCH` muestra una sola ola con todas ellas (no tres lotes).

### P4 — Supervisor scrum (LLM, solo orden) (R3)

Nueva capa de priorización invocada en `schedule` **solo cuando `|ready| >
slots`** (una decisión de scheduling real; si caben todas, no hay nada que
priorizar y se salta).

- **Entrada:** el set `ready` (id, node, deliverables/huella, fr_refs, kind), qué
  FR son `@critical` (de los `.feature`), y el DAG de dependencias.
- **Salida:** un **orden de prioridad** sobre `ready`. Nada más.
- **Cotas duras:**
  - No cambia la seguridad de solapamiento (eso lo decide P2, determinista).
  - No cambia dependencias ni estados de tarea.
  - En `--simulate` es un **stub determinista**: prioridad = defectos → tareas que
    cubren `@critical` → orden topológico → id. Sin llamada al modelo.
  - En modo real puede correr en `SDD_REVIEW_MODEL` (mismo patrón que R1).
  - Si el modelo se cae o responde ilegible, **degrada al stub determinista** y lo
    registra (nunca tumba la corrida).
- **Ubicación:** un módulo `scrum.py` con una función pura
  `prioritize(ready, context) -> ordered_ready`. `parallel_tasks.schedule` la
  llama antes de `safe_batch`.

**Aceptación:** con `|ready| ≤ slots`, el scrum no se invoca (0 tokens). Con
`|ready| > slots` en `--simulate`, el orden resultante pone defectos y `@critical`
primero de forma determinista y reproducible. El fallback del modelo real está
cubierto por un test que simula respuesta ilegible.

## 5. Cambios por archivo (mapa de implementación)

| Archivo | Cambio | Propiedad |
|---------|--------|-----------|
| `.claude/settings.json` | **nuevo** — permisos del harness (P1) | plano de control |
| `sdd/task_worktrees.py` | `_footprint` a nivel de archivo; `safe_batch` → maximal por prioridad, defectos incluidos (P2) | plano de control |
| `sdd/scrum.py` | **nuevo** — `prioritize()` determinista + gancho LLM (P4) | plano de control |
| `sdd/parallel_tasks.py` | `schedule` llama a `scrum.prioritize` y despacha ola ancha (P3, P4) | plano de control |
| `sdd/taskqueue.py` | `runnable` expone huella/criticidad para el ordenador (P4) | plano de control |
| `sdd/pipeline.toml` | `max_concurrency` default 6 (P3); comentario | **⚠ sección `runtime`, NO `budget`/`gates`** |
| `sdd/examples/fake_agent.py` | `SDD_FAKE_PARALLEL` ≥3 tareas disjuntas + 2 defectos disjuntos (pruebas P2/P3) | plano de control |
| `tests/test_langgraph_runtime.py` | casos de ola ancha, defectos concurrentes, scrum stub y fallback | plano de control |

> `pipeline.toml`: solo se toca `max_concurrency` en `[runtime]`. **No** se
> modifica `[budget]` ni `[gates]` ni umbrales — lo prohíbe `CLAUDE.md`.

## 6. Plan de verificación

- `python -m sdd test` verde (incluye los nuevos casos).
- `python -m sdd demo` → `done | tareas: 5/5` (sin regresión).
- `SDD_FAKE_PARALLEL` extendido demuestra una ola ancha real (log `BATCH` con
  ≥3 ids) y defectos concurrentes integrados en orden.
- Test de propiedad G7 sigue revirtiendo escrituras fuera de path (P1 no lo
  debilita).
- Test de scrum: (a) no se invoca si caben todas; (b) orden determinista en
  simulado; (c) fallback ante respuesta ilegible.

## 7. Fuera de alcance (explícito)

- **P5 — Fase lineal en aristas nativas** (product→architect→planner→human_gate).
  Mayor riesgo: reescribe un plano de control ya probado. Se puede retomar en una
  tanda posterior si se quiere maximizar la nativez. Se deja documentado, no
  implementado.
- **Pool sin barrera** (streaming puro sin superstep). Cambia el modelo de
  durabilidad; se descarta por riesgo.
- **Aprendizaje entre corridas** (defectos recurrentes que alimentan prompts).
  No relacionado con el objetivo de esta tanda.

## 8. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Una ola ancha satura el proveedor LLM en modo real | `max_concurrency` configurable, bajable a 1; documentado |
| El scrum LLM introduce no-determinismo en el enrutado | por diseño solo ordena; la seguridad la decide P2 determinista; stub en simulado |
| P1 malinterpretado como "quitar G7" | el settings.json documenta que la propiedad no se toca; el test de G7 lo garantiza |
| Footprint fino falla si el planner no declara `deliverables` | fallback al prefijo del nodo + log de aviso |

# CLAUDE.md — reglas duras del pipeline SDD

Este archivo va en la **raíz del repo objetivo**. Claude Code lo hereda en cada
sesión; no repitas estas reglas en los prompts. Los identificadores, rutas y
artefactos permanecen en inglés; la prosa en español.

## Calidad obligatoria para todo cambio

Antes de crear o modificar codigo, lee y aplica `ENGINEERING_QUALITY.md`. Es la
fuente canonica para pruebas unitarias y de regresion, SOLID, seguridad,
mantenibilidad, escalabilidad y quality gates fail-closed. Estas reglas son
obligatorias para Claude y no pueden relajarse desde un prompt de tarea.

Toda entrega debe incluir evidencia de las pruebas y gates ejecutados. Si algo
falla o no pudo ejecutarse, el cambio no puede presentarse como validado.

## Principio rector: repo-as-state

Los agentes **no se pasan contexto por chat**. Cada nodo lee y escribe archivos
versionados en git. Lo que importa son los artefactos bajo `spec/`, `src/`,
`tests/`. El orquestador solo transporta punteros (`spec_hash`, `task_id`,
`branch`) y decisiones de ruta. Auditoría = git log. Rollback = git revert.

## Prohibiciones absolutas (violarlas rompe el propósito del sistema)

0. **Dos categorías de verificación, y no se mezclan.** Los gates `G*` son código
   determinista sin juicio: su veredicto no se discute ni se negocia. El revisor
   `R1` es un modelo con criterio que corre después de `product`, `architect` y
   `planner`; solo puede **añadir** defectos, jamás relajar un `G*`, y sus
   hallazgos `mejora` no frenan nada. Si un `R1` y un `G*` se contradicen, manda
   el `G*`.
1. **No modifiques umbrales de linters, configuración de CI, ni reglas de gates.**
   Ni `gates/*.py`, ni `gates/registry.toml`, ni `pipeline.toml` en sus secciones
   de `budget`/`gates`, ni ningún `.eslintrc`/`ruff.toml`/`tsconfig` de umbrales.
   Si un gate te bloquea, el problema está en tu código o en la especificación:
   corrige eso, nunca el gate. Relajar un gate es un fallo de integridad, no un fix.
2. **Los agentes Dev no escriben, editan ni eliminan nada bajo `/tests` ni `/spec`.**
   Si una prueba falla y crees que la prueba está mal, emites un defecto contra QA
   con `file:line` y evidencia. No tocas la prueba. (`gates/check_test_integrity.py`
   — G7 — revierte cualquier diff que viole esto, exista o no la capa preventiva.)
3. **QA es el único propietario de `/tests` y `/spec/40_qa`.** No ajusta una
   aserción para que pase: emite defecto contra el Dev responsable.
4. **No reabras decisiones firmadas en el gate humano** (nodo `human_gate`; la
   firma queda en `state.json`). No es un gate `G*` del registro: es un nodo de
   tipo `human`. No lo llames "G3": ese identificador no existe.
5. **No simules un entregable ausente.** Si tu tarea depende de algo que no existe
   y no te corresponde crear —el módulo que debes consumir, el endpoint que debes
   llamar— responde `<<<BLOCKED: qué falta y quién debe producirlo>>>`. Prohibido
   taparlo con un mock, un stub vacío o un TODO para dar la tarea por terminada:
   eso convierte un fallo visible en uno invisible y es como una interfaz sin
   backend llega a reportarse como aplicación funcionando.
6. **Implementas tu `task_id` y nada más.** Tu tarea vive en
   `.agent/current_task.json` (entregables, criterio de aceptación y, si es tarea
   de defecto, los hallazgos exactos del gate). Trabajo fuera de esa tarea no lo
   pidió nadie.

## Propiedad de paths por nodo (declarada en `pipeline.toml`, verificada por G7)

| Nodo          | Escribe en                                                            |
|---------------|-----------------------------------------------------------------------|
| product       | `spec/10_product/`                                                    |
| architect     | `spec/20_arch/` + esqueleto de build en la raíz (`package.json`, `tsconfig`, `.eslintrc`, config del runner, `pyproject.toml`, …) |
| planner       | `spec/30_plan/`                                                       |
| dev_backend   | `src/api/`, `src/domain/`, `src/infra/`, `migrations/`, `.env.example`, `spec/20_arch/env-contract.yaml` |
| dev_frontend  | `src/web/`, `.env.example`                                            |
| qa            | `tests/`, `spec/40_qa/`                                               |

Escribir fuera de tus paths = violación de propiedad → revert automático.

## Reglas de código (todo símbolo que produzcas)

- Objetivo ≤300 líneas por archivo; **límite duro 500** (lo verifica el linter, no
  tú). Al dividir, divide por responsabilidad de dominio, no por conteo de líneas.
  Prohibido crear barrel files / re-exportadores solo para bajar el conteo.
- Dirección de dependencias hacia adentro: **dominio no importa infraestructura ni
  framework**. Un import que viole esta dirección es un fallo de build.
- **Cero secretos y cero valores dependientes del entorno en código**: sin URLs,
  hosts, puertos, credenciales, llaves, timeouts ni rutas absolutas literales. Las
  constantes de dominio y enums **sí** viven en código.
- Configuración: esquema tipado y validado al arranque (`zod` / `pydantic-settings`)
  que lee variables de entorno. Ausencia de variable requerida = **fallo inmediato al
  arrancar**, nunca default silencioso. Toda variable nueva va a
  `spec/20_arch/env-contract.yaml` y a `.env.example` (con valor de ejemplo, no real).
- Tipado explícito en todo símbolo exportado. **Prohibido `any` / `Any` / `interface{}`.**
- Errores tipados y logging estructurado. **Prohibido `console.log` y `print`.**
- Consultas parametrizadas, sin concatenación de SQL. Autorización por recurso y por
  tenant en cada handler del backend, nunca solo en el frontend.
- Commits Conventional Commits referenciando `FR-###` y `task_id`.

## Primera tarea al iniciar en este repo

**No generes configuración de memoria.** Verifica el esquema real de Claude Code
(`settings.json`, `hooks`, subagents, `permissions.deny`) **citando las URLs de la
documentación oficial** — no lo escribas de memoria. El paquete se diseñó con un
corte de conocimiento anterior; los nombres de campo cambian entre versiones.

## Descubrimiento de código: primero el grafo, no `grep`

Este repo está indexado en `codebase-memory-mcp` bajo el nombre de proyecto
**`D-Miguel-auto_scrum`**. Ese es el valor del parámetro `project` en todas las
llamadas. El grafo guarda firmas, tipos de retorno, docstrings, aristas `CALLS`
y métricas de complejidad; también un ADR con la arquitectura del sistema.

**Empieza por el ADR.** `manage_adr(project, mode="get")` devuelve propósito,
stack, arquitectura, patrones, trade-offs y filosofía sin leer un solo archivo.
Es la vía más corta para entender el proyecto al abrir una sesión.

Qué herramienta usar:

| Necesitas | Usa |
|---|---|
| Entender el sistema de golpe | `manage_adr(mode="get")` |
| Estructura, clusters, capas, hotspots | `get_architecture` |
| Encontrar una función/clase por concepto | `search_graph(query=...)` (BM25) |
| Encontrarla por nombre exacto o patrón | `search_graph(name_pattern=...)` |
| Encontrarla sin saber el vocabulario del repo | `search_graph(semantic_query=["a","b"])` |
| Quién llama a esto / qué rompo si lo cambio | `trace_path(mode="calls")` |
| Cómo se propaga un valor | `trace_path(mode="data_flow")` |
| Leer el código exacto de un símbolo | `get_code_snippet(qualified_name)` |
| Patrones multi-salto o agregaciones | `query_graph` (Cypher) |
| Impacto de lo que cambió en la rama | `detect_changes` |

`search_graph` trunca en `limit` (200 por defecto): si `has_more` es `true`,
pagina con `offset` en lugar de asumir que viste todo.

`Grep`/`Glob`/`Read` siguen siendo correctos para prosa, configuración, TOML,
YAML, literales de cadena y archivos no-código — y **siempre** hay que `Read` un
archivo antes de editarlo. Lo que no se hace es descubrir símbolos a ciegas con
`grep` cuando el grafo ya los tiene resueltos por tipo.

### Mantener el índice honesto

- El indexador **respeta `.gitignore`**. Esa lista define el alcance del índice,
  así que un directorio de artefactos sin ignorar mete código de fixture
  (`src/domain/matricula.py`, worktrees de prueba) en el grafo como si fuera
  código del proyecto. Si añades un directorio generado, ignóralo.
- `index_repository` es incremental y **no purga** nodos de archivos que salieron
  del alcance. Para eliminarlos hace falta `delete_project` y reindexar.
- Usa `mode="full"`: es el que calcula las aristas de similitud y los embeddings
  que habilitan `semantic_query`.
- Reindexa tras un cambio estructural; el grafo se ancla al `head_sha` y no ve el
  árbol de trabajo sucio hasta que lo reconstruyes.

## Cómo se corre (una sola app, un solo comando)

Es un paquete `sdd/` instalable. Tras `pip install -e .`:

    sdd demo                                   # bucle simulado, 0 tokens
    sdd web                                     # panel web (CLI + web, mismo comando)
    sdd run  --project <nombre>                # agentes reales → project/<nombre>
    sdd gates --node dev_backend --workdir <repo>

Sin instalar: `python -m sdd <cmd>`. El plano de control es Python + git + stdlib
(el modo real añade el SDK `anthropic`). El intérprete se resuelve con
`sys.executable`; no se depende de que exista `python3` en el PATH.

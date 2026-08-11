# Gates — referencia completa

El `README.md` resume cada gate en una fila. Este documento va un nivel más
abajo: para cada `G*`/`R*` describe la condición exacta que verifica el
código, el archivo donde vive, el formato de hallazgo que produce y qué lo
dispara en la práctica — con cita `archivo:línea` para cada afirmación, de
forma que sea verificable sin tener que confiar en la prosa. Cierra con cómo
correr un gate aislado y cómo se propondría un gate nuevo sin tocar los que
ya existen.

Está pensado para quien va a contribuir código a este repo (no a un proyecto
generado por el pipeline) y necesita saber exactamente qué puede bloquear su
PR y por qué.

## Dos categorías, y no se mezclan

El proyecto distingue dos tipos de verificación con reglas distintas, y esa
distinción es una regla dura de `CLAUDE.md`, no un detalle de implementación:

> "Dos categorías de verificación, y no se mezclan. Los gates `G*` son código
> determinista sin juicio: su veredicto no se discute ni se negocia. El
> revisor `R1` es un modelo con criterio que corre después de `product`,
> `architect` y `planner`; solo puede **añadir** defectos, jamás relajar un
> `G*`, y sus hallazgos `mejora` no frenan nada. Si un `R1` y un `G*` se
> contradicen, manda el `G*`." (`CLAUDE.md`, "Prohibiciones absolutas", punto 0)

En código esa jerarquía se ve en `sdd/gates/registry.toml:82-88`: los gates
`R1`/`R2` llevan el comentario "CATEGORIA DISTINTA. Los G* son codigo
determinista sin juicio. Los R* son un modelo juzgando el trabajo de otro
modelo […] Solo pueden ANADIR defectos; nunca relajan un gate determinista."
Y en `sdd/gates/check_review.py:16-17`: "El gate es fail-closed: proveedor
ausente, excepcion o salida ilegible producen un hallazgo bloqueante. Nunca
fabrica un pass cuando no pudo evaluar."

Por qué importa para quien contribuye:

- Un `G*` que te bloquea nunca se "negocia" desde un prompt de tarea ni desde
  un PR: si el gate está mal, el cambio va contra `sdd/gates/registry.toml` o
  `sdd/gates/check_*.py`, revisado y aceptado como cambio de mantenimiento —
  nunca como parche puntual para destrabar una tarea concreta.
- Un `R1`/`R2` en rojo por un hallazgo `blocking` sí se puede — y se debe —
  discutir con criterio (el revisor puede estar mal calibrado), pero nunca se
  "arregla" bajándole la severidad desde fuera de `sdd/agents/reviewer.md`;
  eso movería la vara para todo el pipeline, no solo para tu tarea.
- Contrato de salida único para ambas categorías (`sdd/gates/_lib.py:26-40`):
  imprimir `{"findings": [...], "meta"?: {...}}` en stdout y salir con código
  `1` si `findings` no está vacío, `0` si lo está. `meta` es telemetría — no
  puede alterar el veredicto (`_lib.py:31-34`).

## Contrato común de cada hallazgo

Cada elemento de `findings` es `{"file", "line", "rule", "evidence"}`
(`sdd/gates/_lib.py:22-23`, función `finding()`). `rule` es un identificador
corto y estable entre corridas; `evidence` es la prueba textual del defecto,
no una sugerencia de arreglo.

## Cómo se orquesta la ejecución de un nodo

`sdd/pipeline.toml` asocia una lista de gates a cada nodo con la clave
`gates = [...]` (por ejemplo, `dev_backend` en `pipeline.toml:105`:
`gates = ["G7", "G0", "G4", "G5", "G6", "R2"]`). El runtime real invoca
`run_node_gates()` de `sdd/runtime/optimized_gates.py` (referenciado desde
`sdd/runtime/orchestrator.py:37,387`), que ejecuta así:

1. **`G7` corre primero y en solitario, de forma serial**
   (`optimized_gates.py:255-260`). Si falla, la función retorna de inmediato
   sin evaluar nada más — el docstring del módulo lo llama "prioridad
   absoluta" (`optimized_gates.py:1-4`). Es el único punto donde el
   comportamiento real se aparta un poco de lo que sugiere el nombre del
   gate en el registro (ver sección G7 más abajo).
2. **El resto de los gates deterministas** (los que no tienen
   `skip_if_prior_failed = true` en `registry.toml`) corren **concurrentemente**
   en un `ThreadPoolExecutor` acotado por `runtime.gate_concurrency`
   (`pipeline.toml:18` → `gate_concurrency = 4`; uso en
   `optimized_gates.py:264-271`).
3. **Los `R*` corren al final**, y solo si ningún gate previo del nodo falló
   (`optimized_gates.py:277-285`, respetando `skip_if_prior_failed` de
   `registry.toml:93,103`). Además, un `R*` cachea su resultado por huella de
   contenido (`_review_digest`, `optimized_gates.py:107-124`): si el
   artefacto, la tarea activa y el prompt del revisor no cambiaron desde la
   última corrida en verde, no se vuelve a llamar al modelo
   (`optimized_gates.py:127-146`).

Esto confirma con precisión la frase del README ("G7 gate serial como
barrera de propiedad, el resto corre concurrente") y añade el detalle que el
README no menciona: el paralelismo tiene techo (`gate_concurrency`, no
ilimitado) y los `R*` tienen caché por contenido.

Herramienta de comando local (`sdd gates --node X --workdir Y`, ver más
abajo) usa en cambio `sdd/gates/run_gates.py`, una versión **puramente
serial** sin caché: recorre `gates_for(node_id, pipeline)` en el orden
declarado en `pipeline.toml` (`run_gates.py:58-77`), respeta el corte de `G7`
(`run_gates.py:74-76`) y el `skip_if_prior_failed` de los `R*`
(`run_gates.py:66-69`), pero no paraleliza ni cachea. Para depurar un gate
puntual el resultado final es el mismo; para medir tiempos de ejecución real
del pipeline, la referencia es `optimized_gates.py`, no `run_gates.py`.

---

## G0 — entregable declarado presente

- **Archivo:** `sdd/gates/check_deliverable.py`
- **Qué verifica exactamente:** que cada patrón glob declarado en dos fuentes
  tenga al menos un archivo que exista **y no esté vacío**:
  1. `must_produce` del nodo en `pipeline.toml` (contrato fijo del rol).
  2. `deliverables` de la tarea activa en `.agent/current_task.json`
     (contrato variable de la tarea en curso).
  (`check_deliverable.py:35-46`)
- Un archivo se considera "vacío" si su contenido, tras `strip()`, pesa menos
  de `--min-bytes` (default `1`) en UTF-8 (`check_deliverable.py:49-60`).
- **Por qué existe (motivo documentado en el propio archivo,
  `check_deliverable.py:2-7`):** hubo una corrida real donde `dev_backend`
  murió con `IncompleteRead`, no escribió un solo archivo, y todos los demás
  gates (`G7`, `G4`, `G5`) dieron verde porque no había nada que reprobar.
  "Verde vacío no es verde."
- **Reglas que emite:** `entregable-vacio` (el patrón matcheó pero el
  contenido es insuficiente) y `entregable-ausente` (el patrón no matcheó
  nada) (`check_deliverable.py:69-74`).
- **Ejemplo de disparo:** el arquitecto declara `must_produce` con
  `spec/20_arch/nfr.yaml` (`pipeline.toml:72`) pero el agente no llega a
  escribirlo, o lo escribe como archivo de 0 bytes.
- **Dueño por defecto:** el nodo que acaba de correr (`route_by = "node"`,
  `registry.toml:16`); no hay "gate owner" fijo distinto del propio agente.

## G1 / G8 — trazabilidad de requerimientos y cobertura de escenarios

- **Archivo:** `sdd/gates/check_traceability.py` — un único script con dos
  modos (`--mode product` = G1, `--mode qa` = G8).
- **G1 (`--mode product`, `check_traceability.py:16-27`):**
  - Falla si no existe ningún archivo `.feature` bajo
    `spec/10_product/features/` → regla `sin-escenarios`.
  - Para cada `FR-###` encontrado en `spec/10_product/prd.md`, exige que ese
    identificador aparezca literalmente en el texto combinado de los
    `.feature` → regla `fr-sin-escenario`.
  - Para cada `@SCN-###` repetido en más de un `.feature`, emite
    `id-duplicado`.
- **G8 (`--mode qa`, `check_traceability.py:28-48`):**
  - Para cada línea de un `.feature` con la etiqueta `@critical` **y** una
    etiqueta `@SCN-###`, exige que ese `SCN-###` (normalizado sin `-`/`_`)
    aparezca en el texto combinado de `tests/` → regla
    `escenario-critico-sin-prueba`. Nótese que la exigencia de G8 es solo
    sobre escenarios marcados `@critical`, no sobre todos los escenarios.
  - Detecta pruebas desactivadas con
    `pytest.mark.xfail|skip|skipif` o `unittest.skip*` → regla
    `prueba-desactivada`, con evidencia explícita: "QA no puede ocultar
    fallos con skip/skipif/xfail; debe corregir el producto o mantener la
    suite roja" (`check_traceability.py:44-48`).
- **Ejemplo de disparo de G8:** un escenario Gherkin marcado
  `@SCN-014 @critical` sin ninguna prueba cuyo texto contenga `SCN014` (o
  `SCN-014`, `SCN_014` — la normalización quita separadores).

## G2 — coherencia de especificación técnica

- **Archivo:** `sdd/gates/check_arch_spec.py`
- **Qué verifica exactamente:**
  1. Presencia de cuatro artefactos fijos bajo `spec/20_arch/`: `nfr.yaml`,
     `api/openapi.yaml`, `env-contract.yaml`, `threat-model.md`
     (`check_arch_spec.py:26-28`).
  2. Cada bloque de `nfr.yaml` (separado por `\n- `) debe tener las claves
     `threshold`/`umbral`, `metric`/`metrica`/`métrica` y `gate_id`
     (`check_arch_spec.py:34-45,112-122`) → regla `nfr-no-medible`.
  3. Si un NFR declara `gate_id`, ese identificador debe existir en
     `gates/registry.toml` o ser literalmente `"manual"`
     (`check_arch_spec.py:20-24,123-127`) → regla `nfr-gate-inexistente`. El
     propio comentario documenta el motivo: antes se aceptaba cualquier
     cadena y un demo llegó a declarar `gate_id: G11`, un gate que no existe
     — un NFR podía afirmar que algo lo verificaba sin que nada lo hiciera.
  4. Cada ADR bajo `spec/20_arch/adr/*.md` debe tener **al menos 2**
     alternativas descartadas, contadas por estructura (encabezado, negrita,
     tabla o prosa enumerada — no por vocabulario fijo,
     `check_arch_spec.py:94-109`) → regla `adr-sin-alternativas`.
  5. Cada ADR debe mencionar un coste (`usd`, `cost`/`coste`/`costo`, o un
     `$` seguido de una cifra) → regla `adr-sin-coste`
     (`check_arch_spec.py:138-139`).
- **Detalle no obvio:** los comentarios en el propio archivo documentan dos
  bugs corregidos que vale la pena conocer si algo "no matchea" — la
  detección de campos NFR anclaba la clave (`^\s*{alias}\s*:`) para no contar
  la palabra "metrica" como subcadena de un valor cualquiera
  (`check_arch_spec.py:30-33`), y el conteo de alternativas se rehizo para no
  exigir vocabulario en español cuando el propio `CLAUDE.md` pide artefactos
  en inglés (`check_arch_spec.py:133-137`).

## G4 — tamaño y estructura estática

- **Archivo:** `sdd/gates/check_file_size.py`
- **Qué verifica exactamente:** cuenta líneas de cada archivo fuente bajo
  `<workdir>/src` (extensiones en `SOURCE_EXT` de `_lib.py:6-7`: `.py .ts
  .tsx .js .jsx .mjs .cjs .go .java .kt .rb .php .cs`, excluyendo
  `node_modules .venv dist build __pycache__ .git`) y falla si supera
  `--hard` (default `500`, línea de comando en `registry.toml:38`:
  `--hard 500 --warn 300`) → regla `max-lines`.
- **Detalle importante:** el flag `--warn 300` se pasa pero **no se usa en
  ningún lado del script** — `check_file_size.py` solo lee `a.hard`
  (línea 16); no hay lógica de warning, ni distinta severidad por debajo del
  límite duro. Es una discrepancia menor entre lo que el comando sugiere
  (`--warn`) y lo que el código hace (lo ignora); el límite real y único es
  `--hard`.
- **Ejemplo de disparo:** un archivo en `src/domain/` de 501 líneas o más.
- **Nota de alcance:** solo mira `src/`, no `tests/` — coherente con la regla
  de `CLAUDE.md` de que dominio/infra tienen el límite duro de 500 líneas.

## G5 — secretos y contrato de entorno

- **Archivo:** `sdd/gates/check_hardcoding.py`
- **Qué verifica exactamente**, línea por línea de todo archivo fuente bajo
  `src/` (`check_hardcoding.py:30-51`):
  1. Cuatro patrones regex fijos (`RULES`, líneas 8-13):
     `hardcoded-url` (URLs `http(s)://` que no sean `localhost`,
     `example.`, `schemas?.` o `www.w3.org`), `hardcoded-secret`
     (`api_key|secret|password|token|private_key` seguido de `=`/`:` y un
     literal de 8+ caracteres entre comillas), `hardcoded-port` (`port`/
     `puerto` seguido de un número de 2-5 dígitos) y `hardcoded-dsn`
     (cadenas de conexión `postgres/mysql/mongodb/redis/amqp://`).
  2. Todo uso de `process.env.X` u `os.environ[...]`/`os.environ.get(...)`
     con una variable en mayúsculas: si `X` no aparece declarada en
     `spec/20_arch/env-contract.yaml` → `env-no-declarada`; si está
     declarada pero no en `.env.example` → `env-sin-ejemplo`.
  3. Una línea que contenga literalmente `gate-ignore` se salta por completo
     (`check_hardcoding.py:32`) — es el único gate determinista con un
     escape explícito por comentario.
- **Efecto colateral documentado en el propio código
  (`check_hardcoding.py:43-49`):** este gate también actúa como guardián
  contra que `dev_frontend` sobrescriba `.env.example` y borre una variable
  que `dev_backend` necesita en `src/api` — si eso ocurre, la corrida de
  frontend dispara `env-sin-ejemplo` sobre esa variable.
- **Ejemplo de disparo:** `const timeout = 30` no dispara nada (no matchea
  ningún patrón), pero `const dbUrl = "postgres://user:pass@host/db"` sí
  (`hardcoded-dsn`).

## G6 — imports locales resueltos

- **Archivo:** `sdd/gates/check_imports.py`
- **Qué verifica exactamente:** que todo import **local** (JS/TS que empieza
  por `./`, `../` o `/`; Python relativo `from .x` o absoluto cuyo primer
  segmento es un directorio de primer nivel del repo) resuelva a un archivo
  real, probando el archivo tal cual, con cada extensión de `JS_EXT`/`PY_EXT`,
  `index.<ext>` o `__init__.<ext>` (`check_imports.py:44-53`).
- **Alcance deliberadamente estrecho** (documentado en
  `check_imports.py:9-15`): los imports "desnudos" (`import react`) quedan
  fuera porque son paquetes del gestor, no código del repo; y "ante la duda
  no se reporta" — un falso positivo aquí bloquearía el pipeline entero.
- **Motivo documentado (`check_imports.py:2-8`):** nació porque QA escribió
  seis pruebas que importaban `src/calculator.js`, `src/parser.js` y
  `src/evaluator.js` — ninguno de los tres existía — y el pipeline dio
  verde. Es determinista y no necesita toolchain instalado.
- **Regla que emite:** `import-no-resuelve`.
- **Rango analizado:** `--roots src,tests` por defecto (`registry.toml:52`).

## G7 — propiedad de paths e integridad Git

- **Archivo:** `sdd/gates/check_test_integrity.py` (el nombre del archivo no
  coincide con el nombre del gate en el registro — ver discrepancia abajo).
- **Qué verifica exactamente:** ejecuta `git status --porcelain -uall` en el
  workdir y, para cada ruta modificada o nueva, falla si esa ruta **no**
  empieza por ninguno de los prefijos de `writes` del nodo en `pipeline.toml`
  **y no** estaba ya en `.agent/baseline.txt` (`check_test_integrity.py:22-38`).
  Regla emitida: `violacion-de-propiedad`.
- **Por qué existe `baseline.txt` (documentado en
  `check_test_integrity.py:2-9`):** el orquestador vuelca ahí lo que ya
  estaba sucio *antes* de invocar al agente actual. Sin esa línea base, G7 le
  atribuiría al nodo en curso trabajo ajeno que quedó pendiente de una tarea
  previa que se puso en rojo (y cuyos archivos siguen en el árbol mientras
  otro nodo trabaja) — y lo revertiría, borrando justo la evidencia que
  destapó el defecto anterior.
- **Discrepancia frente al nombre del gate:** en `registry.toml:56-57` el
  gate se llama "integridad de pruebas y especificacion", lo que sugiere que
  protege específicamente `tests/` y `spec/`. La implementación real es más
  general: es un chequeo de propiedad de paths sobre **cualquier** ruta
  declarada en `writes`, para **cualquier** nodo (`check_test_integrity.py`
  se invoca también para `dev_backend`/`dev_frontend`, cuyo `writes` no
  incluye `tests/` ni `spec/`, ver `pipeline.toml:104,112`). La protección de
  `/tests` y `/spec` frente a los agentes Dev que menciona `CLAUDE.md`
  ("Prohibiciones absolutas", punto 2) es una **consecuencia** de que esos
  directorios no aparecen en el `writes` de ningún nodo Dev, no una regla
  especial distinta escrita en el gate. El README (`Gates`, fila `G7`:
  "Propiedad de paths e integridad Git") describe correctamente el
  comportamiento real; es el nombre en `registry.toml` el que queda
  estrecho.
- **Comportamiento de refuerzo:** cuando `G7` falla, el orquestador
  (`sdd/runtime/workflow_defects.py:149-154`, función `_record_defect`) hace
  `git checkout --` y `git clean -fd` sobre cada archivo listado en los
  hallazgos — es decir, **revierte automáticamente** el cambio fuera de
  propiedad, no solo lo reporta.
- **Corta el resto de la evaluación:** tanto en `run_gates.py:74-76` como en
  `optimized_gates.py:255-260`, si `G7` falla, ningún otro gate del nodo se
  ejecuta en esa pasada.
- **Ejemplo de disparo:** `dev_backend` (writes:
  `README.md, src/__init__.py, src/api/, src/domain/, src/infra/,
  migrations/, .env.example, spec/20_arch/env-contract.yaml`,
  `pipeline.toml:104`) edita un archivo bajo `tests/` para hacer pasar su
  propia prueba: `tests/` no está en su `writes`, así que G7 lo revierte.

## G8 — ver "G1 / G8" arriba

## G9 — suite ejecutada en verde

- **Archivo:** `sdd/gates/check_suite.py`
- **Qué verifica exactamente:** es el único gate que **ejecuta** comandos
  reales en vez de solo leer texto. Lee `spec/20_arch/toolchain.yaml` (que
  declara el arquitecto, no el gate) con claves opcionales `install`, `lint`,
  `typecheck`, `security`, `coverage` y la obligatoria `test`
  (`check_suite.py:9-17,145-147`), y corre cada paso declarado, en el orden
  `install,lint,typecheck,security,test,coverage` (`--steps`, default en
  `check_suite.py:49-51`).
- **Por qué existe (`check_suite.py:2-7`):** antes G8 solo comprobaba que la
  cadena `@SCN-003` apareciera en algún archivo de `tests/` — correlación de
  texto, no verificación. En una corrida real, 544 líneas de pruebas que ni
  siquiera podían importar sus módulos pasaron ese gate. "Mientras ningún
  gate EJECUTE, el verde del pipeline no significa nada."
- **No corta en el primer fallo:** a diferencia de versiones anteriores, se
  evalúan *todos* los pasos independientes salvo que el fallo sea de los que
  no tiene sentido seguir después de (`HARD_STOP`,
  `check_suite.py:89-90,181-186`): `instalacion-fallida`,
  `toolchain-no-disponible`, `entorno-sin-red`, `suite-colgada`. El
  comentario documenta el motivo con una corrida real donde cortar en el
  primer fallo produjo 13 defectos en secuencia porque cada vuelta destapaba
  una capa distinta que ya estaba rota — 4 llamadas al modelo por la misma
  corrección en vez de 1 (`check_suite.py:174-180`).
- **Clasificación de fallos y a quién se enrutan** (documentado en
  `check_suite.py:18-24` y en el diccionario de reglas,
  `check_suite.py:119-121`):
  - `toolchain-no-declarado` → falta `toolchain.yaml` → arquitecto.
  - `toolchain-no-disponible` → el binario del comando no está en `PATH`;
    ningún agente lo arregla escribiendo código → escala a humano.
  - `entorno-sin-red` → detectado por patrones de red en la salida
    (`ENOTFOUND`, `ECONNREFUSED`, `ETIMEDOUT`, etc., `check_suite.py:40-42`)
    → escala a humano.
  - `instalacion-fallida` / `typecheck-rojo` / `lint-rojo` /
    `seguridad-rojo` / `cobertura-insuficiente` / `suite-roja` → se atribuyen
    al archivo del repo que aparece en la salida del runner, con preferencia
    por código de producción sobre archivo de prueba (`blame()`,
    `check_suite.py:60-83`) — así una prueba roja normalmente delata un
    defecto de producción y se enruta a su dueño, no siempre a QA.
- **Caché por huella de árbol:** calcula un hash SHA-256 de
  `toolchain.yaml` + todo `src/` + todo `tests/`
  (`tree_hash()`, `check_suite.py:150-164`) y, si coincide con el último
  verde guardado en `.agent/g9_last_pass.txt`, **no vuelve a ejecutar la
  suite** (`check_suite.py:167-172`), salvo que `SDD_G9_CACHE=0`. La huella
  viaja en `meta.tree_hash` de la salida (`check_suite.py:196`) — no afecta
  el veredicto, pero permite a `optimized_gates.diagnose_oscillation()`
  distinguir no-determinismo real (misma huella, veredictos distintos) de
  regresión genuina (huella distinta).

## G10 — plan de tareas ejecutable

- **Archivo:** `sdd/gates/check_plan.py`
- **Qué verifica exactamente** sobre `spec/30_plan/tasks.yaml`:
  1. Formato: cada tarea es un mapa con `id` (patrón `T-\d{3}`), `title`,
     `node`, `fr_refs`, `deliverables`, `acceptance` presentes
     (`REQUIRED`, `check_plan.py:34,67-69`); ids únicos
     (`id-duplicado`); `fr_refs` con formato `FR-\d{3}` (`fr-invalido`).
  2. `node` debe ser uno de los nodos marcados `task_node = true` en
     `pipeline.toml` (`dev_backend`, `dev_frontend`, `qa` —
     `nodo-invalido` si no).
  3. Cada `deliverable` de una tarea debe caer bajo el `writes` del nodo
     asignado (`entregable-fuera-de-propiedad`,
     `check_plan.py:82-87`) — el mismo concepto de propiedad que aplica G7,
     pero verificado sobre el *plan*, antes de que se ejecute ninguna tarea.
  4. Dependencias (`depends_on`) deben existir (`dependencia-inexistente`) y
     no formar ciclos, detectado con DFS de marcado tricolor
     (`cycle_from`, `check_plan.py:102-118`) → `ciclo-de-dependencias`.
  5. Cobertura: todo `FR-###` que aparece en `spec/10_product/prd.md` debe
     estar referenciado por al menos una tarea → `fr-sin-tarea`
     (`check_plan.py:129-134`).
  6. Debe existir exactamente **una** tarea con `node: qa`, ni cero
     (`plan-sin-qa`) ni más de una (`qa-dividida`,
     `check_plan.py:136-148`) — el comentario explica por qué: `G8` y `G9`
     verifican el proyecto *entero* en cada tarea de QA, así que si QA se
     divide en varias tareas la primera no puede cubrir escenarios que
     pertenecen a la última, y G8 la bloquearía sin que el agente pueda
     arreglarlo con los gates actuales.
- **Por qué existe (`check_plan.py:2-9`):** los prompts de Dev ya asumían un
  bucle de tareas por `task_id` antes de que el orquestador tuviera uno; este
  gate valida el contrato mínimo que hace posible ese bucle.

## R1 — revisión crítica de especificación

- **Archivo:** `sdd/gates/check_review.py` (mismo script para R1 y R2; el
  parámetro `--label` solo cambia el prefijo del log,
  `check_review.py:215`).
- **Qué hace exactamente:** llama a un modelo LLM con el system prompt
  `sdd/agents/reviewer.md` y como contexto de usuario los artefactos del nodo
  bajo revisión, seleccionados por glob en `CONTEXT` (`check_review.py:37-48`;
  para `product`: `spec/00_intake.yaml` + `spec/10_product/**/*`; para
  `architect`: eso más `spec/20_arch/**/*`; para `planner`: eso más
  `spec/30_plan/**/*` y los artefactos de arquitectura relevantes). El
  contexto se acota a `SDD_REVIEW_CTX_CHARS` (default `120000`,
  `check_review.py:51`); si se excede, el resto se omite explícitamente con
  un marcador de texto (`check_review.py:93-95`).
- **Formato de respuesta esperado del modelo:** un bloque
  `<<<REVIEW>>> {"findings":[...]} <<<END>>>` (documentado y exigido en
  `sdd/agents/reviewer.md:12-27`). El parser tolera variaciones (bloque de
  código ```` ```json ```` , u objeto `{...}` suelto con clave `findings`,
  `check_review.py:150-182`) porque, según el comentario, "los modelos no
  siempre respetan el envoltorio (DeepSeek en particular)".
- **`severity` es la decisión central del revisor:** solo `blocking` frena el
  pipeline; `mejora` se acumula en `.agent/review/<key>.json` y sale en el
  reporte final como backlog, sin bloquear nada (`check_review.py:254-260`,
  y la rúbrica en `sdd/agents/reviewer.md:29-40`). Un `severity` fuera de
  `("blocking", "mejora")` es un error de parseo, no una tercera categoría
  (`check_review.py:33,197-199`).
- **Fail-closed, explícitamente:** si el proveedor no está configurado, la
  llamada lanza excepción, o la respuesta no es parseable, el resultado es un
  hallazgo bloqueante con `rule = "revision-no-disponible"`
  (`check_review.py:240-252`) — nunca un pase silencioso. Esto contradice una
  frase del `README.md` (línea "El fallo del revisor conserva el
  comportamiento fail-open documentado"): el código, tanto en el docstring
  del gate (`check_review.py:16-17`) como en el comentario de
  `sdd/runtime/workflow_defects.py:221` ("Finaliza una rama o el proyecto
  **sin rutas fail-open**"), es consistentemente **fail-closed**. Léase
  "fail-open" del README como una imprecisión heredada — probablemente de
  una nota de auditoría anterior (`docs/auditoria/2026-07-29-auditoria-integral.md:126`
  también lo etiqueta "fail-open") que no se actualizó cuando el gate se
  endureció.
- **Efecto de `revision-no-disponible` en el enrutamiento:** el orquestador
  clasifica esa regla como "ambiental" (`ENVIRONMENT_RULES`,
  `sdd/runtime/workflow_defects.py:7-10`), lo que fuerza la ruta `escalate`
  (para a un humano) en vez de reintentar indefinidamente contra un proveedor
  caído (`sdd/runtime/workflow_defects.py:111-114`).
- **Se salta si el nodo ya está en rojo:** `skip_if_prior_failed = true`
  (`registry.toml:93`) — no tiene sentido gastar una llamada al modelo
  criticando un artefacto que ya se sabe roto y que el nodo va a reescribir.
- **Presupuesto de reintentos:** vive únicamente en el orquestador
  (`budget.max_retries_per_gate` en `pipeline.toml:27`, consumido por
  `classify_defect()` en `sdd/runtime/workflow_defects.py:99-136`), nunca en
  el propio gate — el gate solo emite hallazgos, no decide cuántas veces se
  reintenta.
- **Selección de modelo:** por defecto usa un proveedor `frontier` distinto
  al autor (`prefer_different_provider`, `sdd/integrations/model_router.py:273`
  y ss.), configurable con `SDD_REVIEW_MODEL`/`SDD_REVIEW_PROVIDER`.

## R2 — revisión crítica de código

- Mismo script (`check_review.py`) y mismo mecanismo que R1, pero el objeto
  de revisión son los entregables de la **tarea activa** (leídos de
  `.agent/current_task.json`, no todo el repo — `check_review.py:78-84`) y el
  estado de convergencia se lleva por combinación nodo+tarea
  (`key = f"{node}.{task_id}"`, `check_review.py:223`), no solo por nodo: las
  rondas de revisión de una tarea no consumen presupuesto de otra.
- Rúbrica específica para `dev_backend`/`dev_frontend` y para `qa` en
  `sdd/agents/reviewer.md:77-99` — cubre cosas que los gates deterministas
  no miden: dirección de dependencias invertida (dominio importando
  infraestructura), manejo de error tragado, autorización solo en cliente,
  concatenación de SQL, pruebas con aserciones vacías o que mockean la
  propia unidad bajo prueba. El prompt es explícito en no repetir lo que ya
  cazan G4/G5/G6 (`sdd/agents/reviewer.md:90-91`).

---

## Cómo correr un gate localmente

```powershell
sdd gates --node dev_backend --workdir .\project\mi-app\primera-ejecucion
```

Esto invoca `sdd/presentation/cli.py::gates()` (`cli.py:73-85`), que bajo un
lease del proyecto ejecuta `python sdd/gates/run_gates.py --node <node>
--workdir <workdir>` (serial, sin caché — ver la sección de orquestación más
arriba) y devuelve su código de salida.

Salida a leer:

- `run_gates.py` imprime en stdout la lista completa de reportes (uno por
  gate corrido), cada uno `{"gate_id", "name", "node", "status", "findings",
  "default_owner", "route_by"}` (`run_gates.py:47-55`), y termina con código
  `1` si algún reporte tiene `status: "fail"` (`run_gates.py:88`).
- Cada gate individual también deja su reporte en
  `<workdir>/.agent/reports/<node>.<gate_id>.json` (`run_gates.py:70-73`), y
  en la ruta real con `optimized_gates.py` además un journal append-only
  `<node>.<gate_id>.history.jsonl` con un intento por línea
  (`optimized_gates.py:298-328`) — útil para ver la tasa de primera pasada de
  un gate, o para alimentar `optimized_gates.diagnose_oscillation()` cuando
  un gate cambia de veredicto sobre el mismo árbol.
- Para depurar un fallo puntual: abrir el `.json` del gate que falló, mirar
  `findings[].file:line` y `evidence`. Si el `rule` es `gate-roto` o
  `gate-timeout`, el problema es el propio comando del gate (no encontró el
  intérprete, el script lanzó una excepción, o excedió
  `gate_timeout_seconds`), no el código bajo revisión —
  (`run_gates.py:41-43`, `optimized_gates.py:55-68`).
- Correr un solo gate sin pasar por el CLI: cada `check_*.py` es invocable
  directamente, por ejemplo
  `python sdd/gates/check_hardcoding.py --workdir <path>` — útil cuando ya se
  sabe qué gate falló y no se quiere esperar al resto.

## Cómo proponer un gate nuevo

`CLAUDE.md` prohíbe **relajar** un gate existente desde un prompt de tarea —
no prohíbe añadir uno nuevo con criterio propio. La distinción importa: esto
es una guía para *sumar* verificación, nunca para *quitarle* autoridad a la
que ya existe.

1. **Archivo del checker:** un script nuevo bajo `sdd/gates/check_<nombre>.py`
   que importe `finding`/`emit`/`source_files` de `sdd/gates/_lib.py` — ese
   módulo ya resuelve el contrato de salida y el filtro de extensiones/
   directorios ignorados, no hay que reinventarlo.
2. **Contrato de entrada/salida no negociable** (`_lib.py:26-40`):
   - Recibe argumentos por `argparse`, típicamente al menos `--workdir`.
   - Imprime en stdout **exactamente** `{"findings": [...]}`, opcionalmente
     con `"meta"` como telemetría que no debe influir en el veredicto.
   - Sale con código `1` si `findings` no está vacío, `0` si lo está. Nada de
     prosa adicional en stdout: `run_gates.py` y `optimized_gates.py`
     parsean ese stdout como JSON estricto, y cualquier otra cosa se
     convierte en un hallazgo `gate-roto` que oscurece el fallo real.
   - Cada finding es `{"file", "line", "rule", "evidence"}` — `evidence`
     describe el problema, no la solución.
3. **Registrarlo** en `sdd/gates/registry.toml` con un bloque `[[gate]]`
   nuevo: `id` (siguiente `G` libre, o `R3` si es un revisor con juicio),
   `name`, `cmd` (usando los placeholders `{py} {workdir} {gates} {root}
   {node}` ya soportados por `run_gates.py:31-37` /
   `optimized_gates.py:33-35`), y `default_owner` (a quién se le atribuye el
   defecto si no hay una ruta más específica). Si el gate solo tiene sentido
   después de que el resto del nodo esté en verde (por ejemplo, porque llama
   a un modelo y sería caro repetirlo), añadir
   `skip_if_prior_failed = true`, igual que R1/R2.
4. **Asociarlo a los nodos que corresponda** en `sdd/pipeline.toml`, añadiendo
   su `id` a la lista `gates = [...]` del nodo. Un gate que no está en la
   lista de ningún nodo no corre nunca — `gates_for()`
   (`run_gates.py:23-25`) resuelve estrictamente por esa lista.
5. **Decidir `route_by`** (comentado en `registry.toml:6-9`): `"path"` enruta
   el defecto al dueño declarado del path del hallazgo (`writes` en
   `pipeline.toml`); `"gate"` siempre al `default_owner` fijo del gate;
   `"node"` al nodo que acaba de correr, porque el hallazgo es sobre su
   propia ejecución (así lo usan G0, G2, G10, R1, R2).
6. **El límite real:** quien mantiene `sdd/gates/registry.toml` es el único
   que puede tocar umbrales o reglas de gates *existentes*; un gate nuevo se
   añade con su propio criterio explícito (y, si es un `G*`, sin margen de
   juicio: si no se puede expresar como código determinista, el candidato
   correcto es un revisor `R*` acotado como R1/R2, nunca un `G*` con
   heurística difusa disfrazada de determinismo).
7. **Evidencia esperada en el PR que lo introduce:** el gate nuevo corriendo
   contra al menos un caso que debe fallar y uno que debe pasar (test bajo
   `tests/`, siguiendo el patrón de `tests/test_gates_verificacion.py`), y la
   entrada correspondiente en la tabla de gates del `README.md` si el gate
   queda operativo por defecto.

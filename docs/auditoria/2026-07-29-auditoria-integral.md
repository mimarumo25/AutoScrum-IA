# Auditoría integral — AutoScrum · SDD Multi-Agent Control Tower

- **Fecha:** 2026-07-29
- **Rama:** `feat/langgraph-migration` · HEAD `1bfd3fb` + 26 archivos modificados sin commitear
- **Alcance:** paquete `sdd/`, `tests/`, Control Tower web, protocolo entre agentes
- **Excluido por instrucción del usuario:** `project/`
- **Método:** lectura de código, suite completa (188 pruebas), servidor local en `127.0.0.1:8771`, inspección del DOM y del grafo `codebase-memory`

---

## Estado de corrección (actualizado 2026-07-29, tras aplicar los arreglos)

Los 14 hallazgos están corregidos salvo la rotación de la credencial, que solo puede
hacer el operador. Suite: **208 pasan, 0 fallan** (al abrir la auditoría: 187 pasan,
1 falla). Se añadieron 21 pruebas, incluida la de contrato de payload que cubre el
hueco donde vivían tres de los defectos.

| ID | Estado | Verificación |
|---|---|---|
| E-01 | **Acción pendiente del operador** | `config.save()` ya no duplica el secreto si la variable de entorno existe; `sdd doctor` avisa. **La rotación de la key sigue siendo tuya.** |
| E-02 | Resuelto | `test_provider_contract.py::TestParametrosDeMuestreo` (5 pruebas) |
| E-03 | Resuelto | `TestContinuacionSinPrefill` (3 pruebas) |
| E-04 | Resuelto | `test_control_plane.py` en verde; `estado final: escalated`, exit 1 |
| E-05 | Resuelto | 4 llamadas al agente en vez de 6; `attempts` no se reinicia |
| E-06 | Resuelto | `TestNegativaYRespuestaVacia` (2 pruebas) |
| E-07 | Resuelto | Presupuesto repartido por worker, delta reportado a `collect` |
| E-08 | Resuelto | `TestBackoff::test_sin_retry_after_aplica_jitter` |
| E-09 | Resuelto | `TestBackoff::test_respeta_retry_after_del_proveedor` |
| E-10 | Resuelto | `try/except` en `worker()`; degrada a `escalated` sin tumbar el lote |
| E-11 | Resuelto | Tier derivado con `classify_model` en catálogo y runtime |
| E-12 | Resuelto | `TestClasificacionDeErrores::test_409_no_es_transitorio` |
| E-13 | Resuelto | Docstring corregido |
| E-14 | Resuelto | JS y CSS extraídos a `sdd/static/`; UI verificada en vivo, 0 errores de consola |

**Decisiones de diseño tomadas al corregir, por si conviene revisarlas:**

- **No se introdujo un estado nuevo** para el no-progreso. Se reutiliza `escalated`,
  que ya existía, ya devuelve código 1 y ya es el vocabulario del sistema para
  "esto necesita un humano". `waiting_human` queda reservado exclusivamente para la
  pausa firmada del gate humano.
- **Las listas de capacidades de modelo son de lo que SÍ se acepta**, no de lo que se
  rechaza: un modelo desconocido cae en el camino seguro, porque omitir un parámetro
  es válido en toda generación mientras enviarlo no lo es.
- **El presupuesto por worker se reparte, no se comparte.** Un contador global exacto
  entre ramas de `Send` exigiría estado mutable compartido que el modelo de reducers
  de LangGraph no da barato; repartir el remanente garantiza que la ola no supere el
  techo, a costa de que un worker pueda quedarse corto si sus hermanos gastan poco.
- **`web_script.py` sigue exponiendo `SCRIPT`.** El contenido vive en
  `static/app.js` y el módulo solo lo carga, así que `webpage.py` y las pruebas no
  cambian. Servirlo como estático aparte añadiría una ruta nueva y una superficie de
  path traversal sin resolver nada del hallazgo.
- **Una prueba que se ajustó, no se "arregló":** `test_control_plane` medía
  `stdout.count("AGENTE") <= 5`, que hoy coincide con `AGENTE_INICIO`, `AGENTE` y
  `AGENTE_EN_ESPERA` a la vez — medía vocabulario de log, no llamadas. Ahora lee el
  contador explícito del orquestador.

**Veredicto revisado: `APROBADO CON RIESGOS`**, condicionado a rotar la credencial de
E-01. Riesgos aceptados: sin autenticación en el servidor local (documentado, escucha
solo en `127.0.0.1`), sin medición con modelos reales, y la continuación sin prefill
es menos hermética que el prefill (el modelo podría repetir texto al empalmar; la
primera defensa es el `max_tokens` holgado).

---

## A. Resumen ejecutivo

**Nivel de riesgo: ALTO.** No por fragilidad general —el plano de control es sólido y
está bien probado— sino por tres cosas concretas: una credencial real en texto claro,
la ruta del proveedor Anthropic rota de raíz, y una regresión que hace que una
ejecución que **no** convergió se reporte con código de salida **0**.

Lo que está bien, y conviene decirlo con evidencia: **187 de 188 pruebas pasan**. El
protocolo de defectos entre agentes (detectar → asignar al dueño → bloquear al
detector → revalidar con otro agente) **existe y funciona**, y la regla de "nadie
aprueba su propia corrección" está implementada de verdad vía `mark_done`. Los ciclos
de dependencia entre agentes son **estructuralmente imposibles** (razonamiento en §F).
La accesibilidad básica del panel es correcta: cero elementos enfocables ocultos y
`prefers-reduced-motion` cubierto con una regla global.

Los tres riesgos principales:

1. **Credencial DeepSeek activa en `config.json` en texto claro** (§E-01). No está en
   git y nunca lo estuvo, pero durante esta auditoría quedó expuesta en el
   transcript. **Debe rotarse.**
2. **El proveedor Anthropic no puede funcionar** (§E-02, §E-03). Cada llamada envía
   `temperature`, que `claude-opus-5` rechaza con HTTP 400; y la continuación por
   truncamiento usa prefill de asistente, también 400. Hoy no muerde porque la
   configuración activa es DeepSeek, pero `anthropic` es el proveedor por defecto
   documentado y está en el desplegable de la UI.
3. **Falso éxito en no convergencia** (§E-04). Una rama que agota correcciones
   termina en `waiting_human`, y `main()` devuelve 0 para ese estado. Esto contradice
   literalmente el propósito declarado en el encabezado de `orchestrator.py`
   ("un commit que no commitea NO se reporta como aprobado").

**Recomendación de liberación:** `NO APROBADO` para el modo real con Anthropic y para
cualquier automatización que interprete el código de salida. Ver §I.

---

## B. Mapa de arquitectura y flujo

```
Usuario ──▶ Control Tower (webpage/web_script)
              │  POST /run
              ▼
        server.py (http.server, 127.0.0.1, sin auth)
              │  subproceso
              ▼
        orchestrator.main() ──▶ run_lease (exclusión mutua por proyecto)
              │
              ▼
        graph_runtime.run_pipeline  ──── checkpoints ───▶ .agent/checkpoints.sqlite
              │  StateGraph
              ├── bootstrap (presupuesto: wall-time, output tokens)
              ├── product → architect → planner   [fase lineal]
              ├── human_gate  (interrupt() de LangGraph)
              └── task_loop → parallel_dispatch → N×parallel_worker → parallel_collect
                                   │                    │
                                   │                    ├─ worktree Git aislado
                                   │                    ├─ step(): agente → gates → ruta
                                   │                    └─ .agent/current_task.json
                                   ▼
                            scrum.prioritize / task_worktrees.safe_batch
              │
              ▼
        gates/*.py (deterministas) + check_review.py (R1/R2, fail-open)
              │
              ▼
        .agent/state.json  ◀── proyección legible
              │
              ▼
        server.py /state · /events (SSE) ──▶ Control Tower
```

**Punto de desincronización principal:** `state.json` es una *proyección*, no la
fuente de verdad (esa es el checkpoint SQLite). El panel lee la proyección. Si
`save()` falla o se retrasa, la UI muestra estado viejo sin señalarlo.

---

## C. Matriz de vistas y estados (verificado en vivo)

| Vista | Estado | Evento | Resultado esperado | Resultado actual | Auditoría |
|---|---|---|---|---|---|
| Vista en vivo | inicial/idle | carga | "Sin ejecución activa" | correcto | ✅ |
| Alerta "Proceso bloqueado" | oculto en idle | ninguno | no anunciado | `display:none`, fuera del orden de foco | ✅ |
| Tarjetas de agente | idle | carga | 6 agentes "En reposo" | correcto | ✅ |
| Tarjetas de agente | tier | carga | tier del modelo | `frontier` (coincide con `classify_model`) | ⚠️ ver E-09 |
| Paneles ocultos (tabs) | oculto | ninguno | no enfocables | 0 enfocables ocultos | ✅ |
| Animaciones | reduced-motion | preferencia OS | todas desactivadas | regla `*` global, cubre 21 anim + 9 trans | ✅ |
| Historial / Resultados / Configuración | presentes en DOM | — | ocultos por `display:none` | correcto | ✅ |

**No verificado en vivo:** estados de carga lenta, error recuperable, sesión
expirada, sin conexión, reconectando, conflicto de concurrencia. Requieren una
ejecución real (bloqueada por E-02) o inyección de fallos en el SSE. Ver §"Cobertura
no alcanzada".

---

## E. Inventario de hallazgos

| ID | Hallazgo | Tipo | Severidad | Módulo | Responsable | Estado |
|---|---|---|---|---|---|---|
| E-01 | API key DeepSeek activa en texto claro | Seguridad | **BLOCKER** | `config.json` | operador | Confirmado |
| E-02 | `temperature` → HTTP 400 en `claude-opus-5` | Integración | **BLOCKER** | `providers.py:278` | dev_backend | Confirmado |
| E-03 | Prefill de asistente → HTTP 400 en Opus 5 | Integración | **HIGH** | `providers.py:272-273` | dev_backend | Confirmado |
| E-04 | No convergencia ⇒ `waiting_human` ⇒ exit 0 (falso éxito) | Funcional | **HIGH** | `orchestrator.py:579` | dev_backend | Confirmado |
| E-05 | Escalado de modelo reinicia el presupuesto de reintentos | Funcional | **HIGH** | `orchestrator.py:571` | dev_backend | Confirmado |
| E-06 | `refusal` no manejado ⇒ entregable vacío silencioso | Funcional | **HIGH** | `providers.py:291-292` | dev_backend | Confirmado |
| E-07 | `max_agent_calls` no se aplica dentro de los workers | Arquitectura | MEDIUM | `parallel_tasks.py:146` | dev_backend | Confirmado |
| E-08 | Backoff sin jitter + 6 workers ⇒ reintentos sincronizados | Reintentos | MEDIUM | `providers.py:123` | dev_backend | Confirmado |
| E-09 | `Retry-After` ignorado en 429 | Reintentos | MEDIUM | `providers.py:111-128` | dev_backend | Confirmado |
| E-10 | `worker()` sin guarda de excepción ⇒ una excepción mata el lote | Arquitectura | MEDIUM | `parallel_tasks.py:132` | dev_backend | Confirmado |
| E-11 | Tier del catálogo (`unclassified`) ≠ tier mostrado (`frontier`) | Observabilidad | LOW | `model_router.py` / UI | dev_frontend | Confirmado |
| E-12 | HTTP 409 clasificado como transitorio | Reintentos | LOW | `providers.py:93` | dev_backend | Confirmado |
| E-13 | `invoke_agent` docstring promete 4-tupla, devuelve 2-tupla | Documentación | LOW | `orchestrator.py:332` | dev_backend | Confirmado |
| E-14 | `web_script.py` 53 KB en 158 líneas: G4 no lo ve | Arquitectura | LOW | `web_script.py` | architect | Confirmado |

---

## Hallazgos detallados (los seis primeros)

### [E-01] API key DeepSeek activa en texto claro en `config.json`

- **Tipo:** Seguridad · **Severidad:** BLOCKER · **Prioridad:** inmediata
- **Módulo:** `config.json` (raíz) · **Estado:** Confirmado

**Descripción.** `config.json` contiene `keys.deepseek` con una credencial activa en
texto claro. El `README` ya documenta esto como decisión de diseño, pero aquí hay una
llave real, no un ejemplo.

**Evidencia.**
- `config.json` → `"keys": {"deepseek": "sk-66dd…"}` (valor completo omitido aquí a propósito)
- `git ls-files --error-unmatch config.json` → no rastreado
- `git log --all -S 'sk-66dd93ce'` → sin resultados ⇒ **nunca entró a git**

**Agravante introducido por esta auditoría.** Al inspeccionar el archivo apliqué una
máscara por nombre de campo (`key|token|secret`). La estructura real es
`keys.deepseek`, cuyo nombre interno es `deepseek` y no coincidió con el patrón: **la
credencial quedó impresa completa en el transcript de la sesión.** El error es mío.

**Corrección recomendada.**
1. **Rotar la credencial DeepSeek ahora.** Está en el transcript y en cualquier log de sesión.
2. Migrar a variable de entorno (`DEEPSEEK_API_KEY`), que `providers.py` ya soporta.
3. En `config.py`, persistir solo una referencia al nombre de la variable, nunca el valor.

**Criterio de aceptación.** La llave vieja revocada; `config.json` sin material
secreto; `sdd doctor` sigue reportando `key_present: true` leyendo el entorno.

---

### [E-02] `temperature` provoca HTTP 400 en el modelo Anthropic por defecto

- **Tipo:** Integración · **Severidad:** BLOCKER (latente) · **Prioridad:** alta
- **Módulo:** `sdd/providers.py:276-281` · **Estado:** Confirmado

**Descripción.** `_anthropic()` incluye `temperature` en todas las llamadas:

```python
kwargs = {"model": model, "max_tokens": _max_tokens(),
          "temperature": _temperature(),        # ← siempre presente
          "system": system, "messages": messages}
```

`_temperature()` nunca devuelve `None` (default `0.2`). El modelo por defecto es
`ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"`. En Claude Opus 5 / Opus 4.8 / 4.7,
Sonnet 5 y Fable 5, los parámetros de muestreo `temperature`, `top_p` y `top_k`
**fueron eliminados y devuelven HTTP 400**.

**Precondición.** `SDD_PROVIDER=anthropic` (el default) con cualquier modelo del
desplegable salvo `claude-haiku-4-5`.

**Pasos para reproducir.**
1. `$env:SDD_PROVIDER="anthropic"; $env:ANTHROPIC_API_KEY="…"`
2. `sdd run --project x --task y`
3. Primer nodo (`product`) → primera llamada al proveedor.

**Resultado actual.** HTTP 400. `_is_transient()` no incluye 400 ⇒ sin reintento ⇒
`ProviderError` ⇒ `agent.py` sale ≠ 0 ⇒ `agent_failure_report()` lo trata como gate
rojo del propio nodo ⇒ agota reintentos ⇒ escala. **El pipeline no puede completar
un solo nodo.**

**Resultado esperado.** La llamada procede.

**Causa raíz probable.** El código se escribió contra una generación de modelos que
aceptaba `temperature`; los IDs se actualizaron a `claude-opus-5` sin retirar el
parámetro. Los IDs en `MODEL_CHOICES` son todos válidos y actuales — el defecto está
solo en el parámetro.

**Impacto para el usuario.** El proveedor documentado como principal no arranca. Se
enmascara porque la configuración activa es DeepSeek (OpenAI-compatible, que sí
acepta `temperature`).

**Corrección recomendada.** Omitir los parámetros de muestreo cuando el modelo no los
admite. Dado que la temperatura es configurable por perfil de agente en la UI
(`0.0–2.0`), la corrección debe además señalar en la interfaz que el control no
aplica a esos modelos, en vez de ofrecer un ajuste inerte.

**Pruebas recomendadas.** Un test que construya el payload para cada entrada de
`MODEL_CHOICES["anthropic"]` y afirme que `temperature` está ausente en los modelos
que lo rechazan.

---

### [E-03] La continuación por truncamiento usa prefill, rechazado en Opus 5

- **Tipo:** Integración · **Severidad:** HIGH · **Módulo:** `sdd/providers.py:270-273`

```python
def call(prefill: str):
    messages = [{"role": "user", "content": user}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})   # ← prefill final
```

El prefill en el último turno de asistente devuelve **HTTP 400** en Opus 5, Opus 4.8,
Opus 4.7, Sonnet 5 y Fable 5. Es el mecanismo central de
`_continue_until_complete()`, del que depende el protocolo `<<<FILE:>>>` para
respuestas largas — exactamente el caso que el comentario del módulo dice que existe
para resolver (`IncompleteRead` a media respuesta).

**Consecuencia combinada con E-02.** Aunque se corrija `temperature`, la primera
respuesta truncada vuelve a fallar con 400. Ambos deben corregirse juntos para que el
proveedor Anthropic funcione.

**Corrección recomendada.** Sustituir el prefill por salidas estructuradas
(`output_config.format`) o por instrucción de sistema, y subir `max_tokens` para
reducir la frecuencia de truncamiento.

---

### [E-04] Una ejecución que no convergió reporta éxito (exit 0)

- **Tipo:** Funcional · **Severidad:** HIGH · **Módulo:** `sdd/orchestrator.py:577-583`
- **Relación de causalidad: `CONFIRMED`**

**Evidencia — prueba que falla en la suite:**

```
FAILED tests/test_control_plane.py::TestBudgetEscalation::test_agente_atascado_escala_a_humano
AssertionError: 0 != 1
== estado final: waiting_human | llamadas a agente: 6 | tareas: 0/0
1 failed, 187 passed in 96.78s
```

La prueba (cuyo docstring dice *"Con un agente que nunca corrige, el pipeline debe
escalar, no girar sin fin"*) exige `returncode == 1` y `estado final: escalated`.

**Causa raíz.** En `handle_defect`, agotadas las escalaciones:

```python
recovery["status"] = "needs_input"
state["status"] = "waiting_human"        # ← no "escalated"
```

Y en `main()`:

```python
return 0 if state["status"] in ("done", "waiting_human") else 1
```

`waiting_human` es legítimo para el gate humano (la corrida se pausa a propósito),
pero aquí se reutiliza para "la corrección automática no convergió". Se conflacionan
una pausa esperada y un fallo, y ambos salen con 0.

**Causalidad verificada** (no por proximidad temporal):
- `git show HEAD:sdd/orchestrator.py | grep -c model_escalation` → **0**
- `git show HEAD:sdd/orchestrator.py | grep '\["attempts"\]\[key\] = 0'` → **ausente en HEAD**

Todas las líneas implicadas son adiciones sin commitear del trabajo de routing de
modelos (`sdd/model_router.py`, sin rastrear). No es un defecto preexistente.

**Alcance mayor al del test.** El mismo patrón está en `parallel_tasks.schedule()`
(líneas 79-85): un sprint sin tareas ejecutables pero con tareas pendientes también
queda en `waiting_human` ⇒ exit 0. Un sprint en interbloqueo se reporta como éxito.

**Impacto.** Cualquier CI, script o automatización que ramifique según el código de
salida tratará una corrida fallida como buena. Es el falso éxito que el encabezado de
`orchestrator.py` declara como la razón de ser del módulo.

**Corrección recomendada.** Distinguir la pausa deliberada del no-progreso: un estado
propio (p. ej. `stalled`) que devuelva ≠ 0, reservando `waiting_human` para el gate
firmado. Aplicar en los dos sitios (`handle_defect` y `schedule`).

---

### [E-05] El escalado de modelo reinicia el presupuesto de reintentos

- **Tipo:** Funcional · **Severidad:** HIGH · **Módulo:** `sdd/orchestrator.py:571`
- **Relación de causalidad: `CONFIRMED`** (línea ausente en HEAD)

```python
recovery["model_escalation_count"] += 1
recovery["model_escalated"] = True
state["attempts"][key] = 0        # ← reinicia el contador de intentos
```

`max_retries_per_gate = 2` deja de ser el techo real: con un escalado permitido
(`max_frontier_escalations_per_task: 1`), el mismo gate obtiene **2 + 2 = 4** intentos.

**Evidencia.** La misma prueba de E-04 afirma `stdout.count("AGENTE") <= 5` con el
comentario *"Techo de reintentos = 2 ⇒ escala en el 3er intento"*. La corrida
observada hizo **6 llamadas** (`llamada=4`, `5`, `6` visibles en el log).

**Por qué importa más allá del test.** `CLAUDE.md` prohíbe modificar umbrales de
gates y presupuestos. Reiniciar el contador en código consigue el mismo efecto que
subir `max_retries_per_gate` en `pipeline.toml`, sin tocar el archivo — elude la
prohibición sin declararlo.

**Corrección recomendada.** No reiniciar `attempts`. Si el escalado de modelo merece
intentos extra, hacerlo explícito con su propio contador y su propio techo, visible
en el log y en el estado.

---

### [E-06] Una negativa del modelo produce un entregable vacío en silencio

- **Tipo:** Funcional · **Severidad:** HIGH · **Módulo:** `sdd/providers.py:286-292`

```python
text = "".join(b.text for b in msg.content if b.type == "text")
return text, msg.stop_reason == "max_tokens"
```

Solo se inspecciona `stop_reason` para detectar truncamiento. Claude Opus 5 y Fable 5
ejecutan clasificadores de seguridad que devuelven **HTTP 200** con
`stop_reason: "refusal"` y `content` vacío o parcial. Con `content` vacío, `text` es
`""`, `truncated` es `False`, y `_continue_until_complete` devuelve la cadena vacía
como **respuesta completa y válida**.

**Consecuencia.** El agente no escribe archivos, sale con 0, y el gate `G0`
("entregables presentes y no vacíos") es el único que lo caza — si el nodo declara
`must_produce`. Los nodos de tarea no lo declaran (sus entregables los fija
`tasks.yaml`), así que la cobertura es parcial.

Esto choca de frente con la prohibición 5 de `CLAUDE.md`: *"No simules un entregable
ausente… eso convierte un fallo visible en uno invisible"*. Aquí el sistema lo hace
por su cuenta, sin que ningún agente lo decida.

**Corrección recomendada.** Comprobar `stop_reason == "refusal"` antes de leer
`content` y lanzar `ProviderError` con la categoría de `stop_details`. Opcionalmente
declarar `fallbacks` para que la negativa se reintente en otro modelo en la misma
llamada.

---

## F. Matriz de agentes y protocolo de corrección

**Lo que ya está implementado y funciona** (187 pruebas verdes lo respaldan):

| Requisito del protocolo | Implementación | Estado |
|---|---|---|
| Detectar el fallo | `run_node_gates` + `route()` | ✅ |
| Confirmar el dueño del cambio | `registry.toml:route_by` (`path`/`gate`/`node`) | ✅ |
| Evidencia estructurada | `findings[{file,line,rule,evidence}]` | ✅ |
| Solicitud automática de corrección | `taskqueue.make_defect` → tarea `D-###` | ✅ |
| Estado del detector: bloqueado | `blocked_by` + `status="blocked"` | ✅ |
| Bloquear dependientes, no cancelar | `mark_needs_input` pausa solo la rama | ✅ |
| Tareas independientes continúan | `runnable()` + `safe_batch` | ✅ |
| Revalidación por otro agente | `mark_done` devuelve el detector a `pending` | ✅ |
| Nadie aprueba su propia corrección | idem: QA revalida sobre código integrado | ✅ |
| Trazabilidad de cada transferencia | `lifecycle.jsonl` + `chronicle/` | ✅ |
| Techo de reintentos | `max_defect_tasks`, `max_retries_per_gate` | ⚠️ E-05 |
| Escalar al agotarse | `RECUPERACION_EN_ESPERA` | ⚠️ E-04 |

**Detección de ciclos.** El prompt pide impedir `A espera B → B espera C → C espera A`.
En este diseño **el ciclo es estructuralmente imposible**, y conviene registrar por qué:
`make_defect` siempre crea una tarea **nueva** y apunta `blocked_by` hacia ella. Un
`blocked_by` nunca puede señalar a un ancestro existente, así que el grafo de bloqueos
es un bosque de cadenas, no un grafo general. Además `max_defect_tasks` (12) acota la
profundidad. Para el plan declarado, `gates/check_plan.py:cycle_from` valida el DAG
en G10. No hace falta un detector de ciclos en runtime.

**Brecha respecto a la máquina de 14 estados del prompt.** El proyecto usa
`pending / done / blocked / needs_input` para tareas y `assigned / corrected /
needs_input` para recuperaciones. Cubre semánticamente `PENDING`, `IN_PROGRESS`,
`BLOCKED_BY_AGENT`, `FIX_REQUESTED`, `FIX_IN_PROGRESS`, `REVALIDATING`, `COMPLETED`
y `ESCALATED`. No distingue `ANALYZING`, `REOPENED` ni `CANCELLED`.

**No recomiendo adoptar los 14 estados.** El diseño actual es `repo-as-state`: el
estado observable son los archivos en git y los `lifecycle.jsonl`. Añadir estados que
ningún gate consume agregaría superficie sin cambiar decisiones. Si se quiere más
granularidad, el lugar correcto es un campo en el evento de lifecycle, no un estado
nuevo en la cola.

---

## G. Cobertura de pruebas

| Módulo | Unit | Integración | E2E simulado | Negativas | Estado |
|---|---|---|---|---|---|
| `orchestrator` / router | ✅ | ✅ | ✅ | ✅ | 1 fallo (E-04/E-05) |
| `taskqueue` / defectos | ✅ | ✅ | ✅ | ✅ | verde |
| `graph_runtime` / checkpoints | ✅ | ✅ | ✅ | parcial | verde |
| `parallel_tasks` / worktrees | ✅ | ✅ | ✅ | parcial | verde |
| `gates/*` | ✅ | ✅ | — | ✅ | verde |
| `model_router` | ✅ | parcial | — | ✅ | verde |
| `providers` | parcial | ❌ | ❌ | ❌ | **hueco (E-02/03/06)** |
| Control Tower UI | ✅ | ✅ | parcial | parcial | verde |
| Accesibilidad | — | — | verificado en vivo | — | verde |

**El hueco decisivo es `providers.py`.** Ninguna prueba construye el payload real de
Anthropic ni lo valida contra el contrato del modelo. Los tres defectos BLOCKER/HIGH
de integración viven exactamente ahí. Una prueba de contrato de payload —sin llamar a
la API— los habría atrapado los tres.

---

## H. Plan de corrección

**Inmediato (antes de cualquier otra cosa)**
1. E-01 — Rotar la credencial DeepSeek. Está en el transcript de esta sesión.

**Antes de liberar**
2. E-04 — Separar `waiting_human` (pausa firmada) de no-progreso; exit ≠ 0. Dos sitios.
3. E-05 — Quitar el reinicio de `attempts`; contador propio para el escalado.
4. E-02 + E-03 — Omitir `temperature` y sustituir el prefill. Corregir juntos.
5. E-06 — Manejar `stop_reason == "refusal"`.
6. Prueba de contrato de payload por modelo en `providers.py`.

**Corto plazo**
7. E-07 — Aplicar `max_agent_calls` con un contador compartido en `collect`.
8. E-08 + E-09 — Jitter aleatorio y respeto de `Retry-After`.
9. E-10 — `try/except` en `worker()` que degrade a `escalated` sin matar el lote.
10. E-12 — Sacar 409 de `TRANSIENT_STATUS`.

**Mejoras arquitectónicas**
11. E-14 — `web_script.py`/`web_styles.py` como archivos `.js`/`.css` reales servidos
    como estáticos. Hoy G4 mide líneas y no ve 53 KB en 158 líneas: el gate está
    ciego a estos archivos, no relajado.
12. E-11 — Una sola fuente de verdad para el tier de un modelo.

---

## Cobertura no alcanzada (declarado explícitamente)

Para no dar por auditado lo que no lo está:

- **Sin ejecución en modo real.** Consume tokens y la ruta Anthropic está rota (E-02).
  Todo lo funcional se verificó en modo simulado.
- **Sin pruebas de carga, rendimiento ni fugas de memoria.**
- **Sin matriz completa de fallos HTTP** (401/403/422/…) contra la API local: el
  servidor no tiene autenticación ni autorización, así que la mayoría no aplica. La
  ausencia de auth está documentada y es deliberada (escucha solo en `127.0.0.1`).
- **Sin pruebas de sincronización entre pestañas** ni de reconexión SSE con pérdida
  de red.
- **Sin auditoría de `project/`** (excluido por instrucción).
- **Estados de UI no ejercitados:** carga lenta, error recuperable, sesión expirada,
  sin conexión, conflicto de concurrencia.

---

## I. Veredicto final

### `NO APROBADO`

Justificación con evidencia concreta:

1. **`config.json` contiene una credencial activa en texto claro**, ahora también en
   el transcript de esta sesión. Requiere rotación antes que cualquier otra acción.
2. **La ruta del proveedor Anthropic no puede completar un solo nodo**
   (`providers.py:278` envía `temperature` a `claude-opus-5` → HTTP 400; el prefill de
   continuación en `providers.py:272` → HTTP 400). Es el proveedor por defecto
   documentado.
3. **Una ejecución que no convergió sale con código 0**
   (`tests/test_control_plane.py::test_agente_atascado_escala_a_humano`, `0 != 1`),
   por una regresión sin commitear cuya causalidad se verificó contra HEAD.

Los tres son corregibles sin rediseño. El plano de control —protocolo de defectos,
aislamiento por worktree, autoridad de los gates, imposibilidad estructural de ciclos,
accesibilidad del panel— está sano y bien probado: 187 de 188 pruebas en verde. El
veredicto responde a tres defectos localizados, no a la arquitectura.

Con E-01 a E-06 corregidos y la prueba de contrato de payload añadida, la evaluación
esperable es `APROBADO CON RIESGOS`, quedando como riesgo aceptado la ausencia de
autenticación en el servidor local y la falta de medición con modelos reales.

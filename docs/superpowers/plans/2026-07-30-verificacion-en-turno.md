# Verificación en turno: darle los gates al agente — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Este plan se ejecuta sobre el repo del pipeline (`D:\Miguel\auto_scrum`), no sobre un repo objetivo.** Las prohibiciones de `CLAUDE.md` que impiden a un agente Dev tocar `/tests` o los gates aplican a los agentes SDD dentro de un proyecto generado. Aquí estamos construyendo la herramienta. Ver [Frontera de integridad](#frontera-de-integridad) antes de tocar cualquier gate.

**Goal:** Invertir el bucle de verificación. Hoy el agente escribe a ciegas, sale, y el orquestador descubre el fallo; el agente nunca ve el veredicto en el mismo turno y la corrección cuesta una llamada entera con todo el contexto reconstruido. Después: el agente ejecuta los gates deterministas *dentro* de su turno, ve los hallazgos y corrige antes de salir. Los gates siguen siendo la autoridad, y el orquestador los vuelve a correr fuera del turno sin cambio alguno.

**Tesis:** Los gates ya son ejecutables y ya son deterministas. El único motivo por el que el agente no los usa es que nadie le pasó el ejecutable. *Verificación dentro del turno; auditoría fuera.*

**Architecture:** Plano de control en Python + git + LangGraph. `agent.py` pasa de una llamada `providers.complete(system, user)` a un bucle acotado de herramientas (`write_files`, `run_gates`, `finish`) sobre `providers.complete_with_tools`. `optimized_gates.run_node_gates` gana dos parámetros aditivos para poder correrse en modo asesor sin contaminar el rastro de auditoría. El código de contrato de exit (`0/1/2/3`) y la FSM de defectos del orquestador **no cambian**.

**Tech Stack:** Python 3.12, `unittest` (stdlib, sin pytest ni conftest en este repo), `anthropic` SDK (Messages API, tool use GA — no beta), `tomllib`, LangGraph.

---

## Riesgos, antes de la solución

Enumerados primero a propósito. Si alguno resulta inaceptable, el plan se detiene en la Tarea 2 y aun así entrega el 60 % del valor.

| Riesgo | Magnitud | Mitigación en este plan |
|---|---|---|
| **Goodhart: el agente optimiza la métrica que lo juzga.** Ve `G4 archivo>500 líneas` y crea barrel files; ve `adr-sin-alternativas` y repite la palabra. | **Alto y real.** Es el riesgo estructural de todo el plan. | (a) Tarea 2 convierte las reglas de conteo-de-palabras en reglas estructurales; (b) addendum explícito al prompt prohibiendo barrel files, relleno de vocabulario y stubs; (c) R1/R2 siguen corriendo **fuera** del bucle, con juicio, y pueden añadir defectos por exactamente esto. Residual: no eliminado. |
| **Coste de tokens.** Cada iteración reenvía el historial, que incluye el contenido de los archivos escritos. | 2 iteraciones ≈ 1,8× tokens de entrada de esa llamada, mitigado a ~0,3× para el prefijo cacheado. [Suposición] — no medido; la Tarea 5 instrumenta la medición antes de la Tarea 6 para que deje de ser suposición. | Presupuesto duro `SDD_AGENT_GATE_BUDGET=3`, `SDD_AGENT_MAX_ITERATIONS=8`, caching en `system` (Tarea 4) y breakpoint móvil sobre el historial. |
| **Latencia por nodo.** Cada `run_gates` in-turn corre subprocesos reales. | +1 a +3 ejecuciones de gates por nodo. Para `architect` son gates de lectura de archivos (rápidos). Para `qa` incluye G9, que **ejecuta la suite completa**. | `qa` es la **última** etapa del despliegue (Tarea 8) precisamente por esto, y depende del caché de árbol de G9 que ya existe. |
| **Deuda técnica: dos caminos de generación.** El de una sola llamada y el de bucle, ambos vivos. | Media. `SDD_AGENT_LOOP=0` obliga el camino antiguo; ambos deben mantenerse verdes. | Aceptada deliberadamente: es el mecanismo de rollback. Se consolida en un solo camino solo cuando los 6 nodos estén en bucle y estable. |
| **Degradación silenciosa por proveedor.** `self_verify=true` con un proveedor sin bucle de herramientas. | Alta si no se maneja: sería exactamente el fallo invisible que `CLAUDE.md` prohíbe. | Fallback a una sola llamada, pero con aviso en stderr **y** `metrics.record(operation="agent_loop_unavailable")`, para que sea visible en telemetría y no una silenciosa vuelta atrás. |
| **Tarea 1 (arnés) sin la Tarea 2 no aporta nada, y la Tarea 2 sin la Tarea 1 es una relajación de gate sin prueba.** | Bloqueante. | Son inseparables y van primero. No se toca ningún gate sin un fixture golden-fail que demuestre que la regla sigue atrapando la violación real. |

---

## Frontera de integridad

`CLAUDE.md` prohibición #1: *"No modifiques umbrales de linters, configuración de CI, ni reglas de gates… Si un gate te bloquea, el problema está en tu código o en la especificación."*

La Tarea 2 modifica `sdd/gates/check_arch_spec.py`. Esa prohibición **no** se está relajando, y la distinción es verificable, no retórica:

- La prohibición existe para impedir que un agente **al que un gate bloquea legítimamente** debilite el gate para pasar. Aquí el gate rechaza artefactos que **sí cumplen** la especificación del prompt. Es un defecto del oráculo, no un umbral incómodo.
- **Evidencia medida, no inferida.** `project/acortador-v3/tarea-1/spec/20_arch/nfr.yaml` declara `metric:`, `threshold:`, `method_of_measurement:` en inglés — tal como `CLAUDE.md` exige ("los identificadores, rutas y artefactos permanecen en inglés"). `check_arch_spec.py:36` comprueba `"umbral" in block` y `"metrica" in block`. Cada entrada NFR conforme genera **dos hallazgos espurios**.
- `project/acortador-min/tarea-1/spec/20_arch/adr/ADR-002-almacenamiento-map.md` tiene `## Alternativas consideradas` con dos alternativas numeradas, coste en USD, consecuencias y condición de reversión: cumple `agents/architect.md:17-18` al pie de la letra. `check_arch_spec.py:48` hace `t.count("alternativa") < 2` → cuenta 1 → reprobado.
- **La regla que hace esto seguro:** ningún cambio a un checker se acepta sin un fixture *golden-fail* en `tests/test_gate_conformance.py` que demuestre que la regla sigue reprobando la violación real. La Tarea 1 construye ese arnés **antes** de que la Tarea 2 toque una línea del gate. Sin arnés no hay cambio de gate.
- No se toca `sdd/gates/registry.toml`, ni `[budget]`, ni `[gates]` de `pipeline.toml`, ni ningún umbral (`500` líneas de G4, `2` alternativas, etc.). Los umbrales quedan idénticos: solo se corrige **cómo se mide**.

---

## Global Constraints

- **No se toca** `sdd/gates/registry.toml` ni las secciones `[budget]`/`[gates]` de `pipeline.toml`. El único añadido en `pipeline.toml` es el campo `self_verify` dentro de `[[node]]`.
- **Ningún umbral cambia.** Solo cambia la medición, y siempre con fixture golden-fail que lo prueba.
- **El contrato de exit del agente no cambia:** `0` = escribió ≥1 archivo, `1` = fallo, `2` = invocación mala, `3` = bloqueado. En particular: el agente **no** sale con 0 porque los gates in-turn dieron verde, ni con 1 porque dieron rojo. El exit sigue significando exclusivamente "produjo artefactos". Esto es lo que preserva intacta la FSM de defectos.
- **La ejecución de gates del orquestador sigue siendo la autoridad.** `orchestrator.evaluate` → `optimized_gates.run_node_gates` no cambia de comportamiento. La corrida in-turn es **asesora**: no puede convertir un rojo en verde.
- **Los R\* nunca entran al bucle.** Un revisor LLM dentro del turno del agente es el agente juzgándose a sí mismo. Se filtran por prefijo `R`.
- Los tests son `unittest` (`python -m unittest discover -s tests`). **No hay `conftest.py`, `pytest.ini` ni sección `[tool.pytest]`**: no escribas pruebas parametrizadas de pytest. Para parametrizar usa `subTest`.
- Import path de test: `ROOT = Path(__file__).resolve().parent.parent / "sdd"`, y los checkers se ejercen **como subproceso** (patrón `run_checker` de `tests/test_gates.py:24-33`).
- Tipado explícito en símbolos exportados; sin `print` de depuración en producción; objetivo ≤300 líneas por archivo.
- Todo cambio mantiene verde `python -m unittest discover -s tests` y `python -m sdd demo`.

---

## Hechos verificados en Fase 0 (no los vuelvas a descubrir)

Esta tabla existe para que los contextos siguientes no gasten una ronda entera redescubriendo firmas. Cada fila se verificó leyendo el archivo indicado.

### Integración en el repo

| Hecho | Ubicación |
|---|---|
| **El runner en producción es `optimized_gates`, no `gates/run_gates`.** `orchestrator.py:37` importa `from sdd.runtime.optimized_gates import run_node_gates`. `gates/run_gates.py` solo aporta `gates_for` y `load_registry` (importados en `optimized_gates.py:18`) y su CLI. | `sdd/runtime/orchestrator.py:37` |
| Firma: `run_node_gates(node_id: str, workdir: str, pipeline: dict) -> list[dict]`. `pipeline` es el `cfg` completo de `pipeline.toml`; necesita `pipeline["runtime"]["gate_timeout_seconds"]` y `gate_concurrency`. | `sdd/runtime/optimized_gates.py:141-181` |
| Reporte normalizado: `{gate_id, name, node, status, default_owner, route_by, findings}`. Se persiste en `.agent/reports/{node}.{gate_id}.json` — **se sobrescribe cada intento**, no hay historial por intento. | `optimized_gates.py:184-186` |
| G7 corre primero y en solitario; si falla, `return reports` inmediato. Los `skip_if_prior_failed` (R1/R2) corren al final y solo sobre verde total. | `optimized_gates.py:150-180` |
| `run_node_gates` lee la tarea activa de `.agent/current_task.json` vía `_task(workdir)`; el orquestador ya la publicó (`taskqueue.publish_current`). | `optimized_gates.py:21-27` |
| `write_files(workdir: Path, allowed, files) -> (written, skipped)`. **`files` es `[(rel: str, body: str)]`, tuplas, no dicts.** Ya aplica `_safe_target` (anti-traversal) y el prefijo `allowed`. Reutilizar tal cual: es la única puerta de escritura. | `sdd/runtime/agent.py:130-143` |
| Punto de integración único del bucle: `agent.py:330`, `text = providers.complete(system_prompt, user)`, dentro de `with model_router.selection_environment(selection, runtime_cfg):`. | `sdd/runtime/agent.py:324-330` |
| `providers.complete(system, user) -> str` envuelve la llamada en un `try/finally` que hace `metrics.record` + `metrics.record_usage` con `provider/model/tier/selection_reason/escalated/node/task` desde variables de entorno. **Reutilizar ese envoltorio, no duplicarlo.** | `sdd/integrations/providers.py:339-377` |
| `_anthropic` usa `client.messages.stream(**kwargs)` + `stream.get_final_message()`, trata `stop_reason == "refusal"` como error explícito, y acumula uso con `_add_usage(input, output, cache_read, cache_creation)`. | `providers.py:380-438` |
| **Bug de caché confirmado:** `providers.py:413-414` pone `cache_control` como **parámetro top-level de la request**, no como campo de un bloque de contenido. No existe tal parámetro en la Messages API: nunca se crea un breakpoint. Medido: 0 tokens de `cache_read` en 310 registros de `usage.jsonl`. | `providers.py:413-414` |
| `chronicle.archive_agent_call(workdir, visit_id, node, task_id, system_prompt, user_prompt, response_text, stdout_text, stderr_text, returncode, files_written, files_skipped, token_usage=None, model_selection=None)`. Ya persiste `model_selection` con `provider/model/tier/requested_tier/...`. `agent._archive_call` ya lo pasa. | `sdd/core/chronicle.py:50-97`, `agent.py:373-394` |
| `metrics.record(workdir, operation, **fields)` y `metrics.record_usage(workdir, **fields)`: append-only JSONL bajo `.agent/`, un `os.write` por línea bajo lock. Sin esquema fijo — los campos son libres. | `sdd/core/metrics.py:20-73` |
| `process_control.run_bounded(argv, cwd=..., env=..., timeout_seconds_value=...) -> (proc, timed_out)`; `process_control.timeout_seconds(key)`; `process_control.run_git(workdir, *args, text=True)`. | usados en `optimized_gates.py:43`, `providers.py:389`, `orchestrator.py:86` |
| **No existe una sola línea de tool calling en `sdd/`.** `grep -rn "tool_use\|tool_calls\|tools=\|function_call\|tool_choice" sdd/ --include=*.py` → 0 resultados. Se construye desde cero. | verificado |
| Contrato de todo checker: stdout `{"findings":[...]}`, `sys.exit(1 if findings else 0)`, vía `_lib.emit`. | `sdd/gates/_lib.py:26-29` |
| Patrón copy-ready para ejercer un checker en test: `run_checker(script, *args)` — subproceso + parseo de stdout + `AssertionError` si el JSON no es válido. | `tests/test_gates.py:24-33` |

### API de Anthropic (tool use GA, sin cabecera beta)

Fuente autoritativa: skill empaquetada `claude-api`, `python/claude-api/tool-use.md` y `shared/prompt-caching.md`.

| Hecho | Detalle |
|---|---|
| `client.messages.stream()` **acepta `tools=`**. El patrón documentado es: consumir el stream, `stream.get_final_message()`, y continuar si `response.stop_reason == "tool_use"`. | `python/claude-api/streaming.md:74-90` |
| Bucle manual (elegido aquí sobre el `tool_runner`, que es **beta**): `while True` → `create/stream` → romper si `stop_reason == "end_turn"` → extraer bloques `b.type == "tool_use"` → `append({"role":"assistant","content": response.content})` → ejecutar → `append({"role":"user","content": tool_results})`. | `tool-use.md:174-223` |
| Bloque `tool_use`: atributos `.name`, `.input`, `.id`. | `tool-use.md:238-241` |
| Resultado: `{"type": "tool_result", "tool_use_id": <id del bloque>, "content": <str>}`, y `"is_error": True` para error. Va en un mensaje de **rol `user`**. | `tool-use.md:211-216`, `296-305` |
| `strict: True` va en el **nivel superior de la definición de herramienta**, hermano de `input_schema`. El schema requiere `required` y `additionalProperties: false`. | `tool-use.md:568-591` |
| Orden de render: **`tools` → `system` → `messages`**. Un breakpoint en el último bloque de `system` cachea `tools` + `system` **juntos**. | `shared/prompt-caching.md:11`, `:37` |
| Multi-turno: breakpoint en el último bloque de contenido del turno recién añadido. Los breakpoints anteriores siguen siendo puntos de lectura válidos. El límite de la API es 4 → hay que **mover** el breakpoint del historial, no acumularlos. | `shared/prompt-caching.md:45-52` |
| El `tool_runner` de Python **no reanuda `pause_turn`** (anthropic 0.116.0): un turno pausado termina el bucle en silencio. Razón adicional para el bucle manual, que sí lo maneja. | `tool-use.md:54` |

**No verificado / fuera de alcance de este plan:** las formas de payload de tool calling de los proveedores OpenAI-compatibles (DeepSeek `/beta`, GLM, Kimi, DashScope) — si `function.arguments` llega como string JSON o parseado, `finish_reason`, interacción de `tools` con el campo `prefix` de continuación de DeepSeek, y los nombres exactos de los campos de caché en `usage`. Eso es **prerrequisito declarado de la Tarea 8**, no parte de este plan.

---

## File Structure

| Archivo | Responsabilidad | Acción |
|---------|-----------------|--------|
| `tests/test_gate_conformance.py` | Arnés: cada regla de cada checker con un fixture golden-pass y un golden-fail, sembrados de artefactos reales. | Crear |
| `tests/fixtures/gate_conformance/` | Artefactos reales copiados de `project/` que G2 rechazó siendo correctos. | Crear |
| `sdd/gates/check_arch_spec.py` | 4 correcciones de medición. Umbrales intactos. | Modificar |
| `sdd/integrations/providers.py` | `complete_with_tools`, `_anthropic_tools`, `_move_turn_breakpoint`; caché en el bloque `system`; campos de caché de DeepSeek; extracción del envoltorio de telemetría. | Modificar |
| `sdd/integrations/model_router.py` | `resolve_review` → `balanced` para R2. | Modificar |
| `sdd/runtime/optimized_gates.py` | `run_node_gates` gana `gate_filter` y `reports_dir` (aditivos, keyword-only); `tier` en el entorno de los R\*; reportes versionados por intento. | Modificar |
| `sdd/runtime/agent.py` | `TurnState`, `tool_specs`, `self_verifying_turn`; ramificación por `self_verify`. | Modificar |
| `sdd/pipeline.toml` | `self_verify = true` en `[[node]]`, nodo por nodo según el despliegue. | Modificar (solo `[[node]]`) |
| `tests/test_self_verify.py` | Bucle de herramientas con proveedor falso: presupuestos, filtrado de R\*, exit codes, aislamiento de reportes. | Crear |
| `tests/test_provider_contract.py` | Contrato de payload de `complete_with_tools` sin red. | Modificar |
| `FLUJO.md`, `HANDOFF.md`, `README.md` | Reflejar verificación en turno y las banderas nuevas. | Modificar |

---

## Task 1: Arnés de conformidad de gates (prerrequisito duro)

Sin esto no se toca ningún gate. El arnés es lo que convierte "corregí el gate" en una afirmación verificable en vez de una promesa.

**Files:**
- Create: `tests/test_gate_conformance.py`
- Create: `tests/fixtures/gate_conformance/`

**Interfaces:**
- Consumes: `sdd/gates/registry.toml` (solo lectura), el contrato `{"findings":[...]}` + exit 1 de `_lib.emit`.
- Produces: un `TestCase` por checker con dos aserciones por regla.

- [ ] **Step 1: Copiar los artefactos reales como fixtures**
  - `tests/fixtures/gate_conformance/nfr-ingles-valido.yaml` ← `project/acortador-v3/tarea-1/spec/20_arch/nfr.yaml`, con `gate_id: G-test` cambiado a `gate_id: G9` (así el fixture es *puramente* golden-pass).
  - `tests/fixtures/gate_conformance/nfr-gate-inexistente.yaml` ← el mismo, conservando `gate_id: G-test` (golden-fail: `nfr-gate-inexistente` es un hallazgo **correcto**).
  - `tests/fixtures/gate_conformance/nfr-gate-citado.yaml` ← el mismo con `gate_id: "G9"` **entre comillas** (golden-pass: hoy falla).
  - `tests/fixtures/gate_conformance/adr-valido.md` ← `project/acortador-min/tarea-1/spec/20_arch/adr/ADR-002-almacenamiento-map.md` sin editar (golden-pass: hoy falla).
  - `tests/fixtures/gate_conformance/adr-una-alternativa.md` ← el mismo con la alternativa 2 borrada (golden-fail: debe seguir fallando después de la corrección).
  - `tests/fixtures/gate_conformance/adr-sin-alternativas.md` ← el mismo sin la sección `## Alternativas` (golden-fail).
  - `tests/fixtures/gate_conformance/adr-sin-coste.md` ← el mismo sin la sección de coste (golden-fail).
  - Añade un `README.md` de una línea en el directorio: de qué corrida real salió cada archivo. Un fixture sin procedencia es un fixture inventado.

- [ ] **Step 2: Escribir el arnés (que falla hoy)**
  - Copia `run_checker` y `rules` de `tests/test_gates.py:24-42` — **no** inventes un runner nuevo.
  - Estructura, con `subTest` porque **este repo no tiene pytest configurado**:

    ```python
    class TestG2Conformance(GateTestCase):
        """Cada regla de G2 contra un artefacto que debe pasar y uno que debe fallar.

        Los golden-pass salen de corridas reales que G2 reprobo siendo correctas;
        los golden-fail existen para que una correccion del gate no pueda
        convertirse en una relajacion sin que una prueba lo delate.
        """

        CASOS = [
            # (fixture, destino en el repo, regla, debe_aparecer)
            ("nfr-ingles-valido.yaml",   "spec/20_arch/nfr.yaml",    "nfr-no-medible",       False),
            ("nfr-gate-citado.yaml",     "spec/20_arch/nfr.yaml",    "nfr-gate-inexistente", False),
            ("nfr-gate-inexistente.yaml","spec/20_arch/nfr.yaml",    "nfr-gate-inexistente", True),
            ("adr-valido.md",            "spec/20_arch/adr/A.md",    "adr-sin-alternativas", False),
            ("adr-una-alternativa.md",   "spec/20_arch/adr/A.md",    "adr-sin-alternativas", True),
            ("adr-sin-alternativas.md",  "spec/20_arch/adr/A.md",    "adr-sin-alternativas", True),
            ("adr-valido.md",            "spec/20_arch/adr/A.md",    "adr-sin-coste",        False),
            ("adr-sin-coste.md",         "spec/20_arch/adr/A.md",    "adr-sin-coste",        True),
        ]

        def test_conformidad(self):
            for fixture, destino, regla, esperado in self.CASOS:
                with self.subTest(fixture=fixture, regla=regla):
                    ...  # sembrar arbol minimo + los 4 artefactos requeridos
                    findings, _ = run_checker("check_arch_spec.py", "--workdir", self.wdp())
                    self.assertEqual(regla in rules(findings), esperado)
    ```
  - Siembra siempre los cuatro artefactos que G2 exige (`nfr.yaml`, `api/openapi.yaml`, `env-contract.yaml`, `threat-model.md`) para que `artefacto-faltante` no ensucie el resultado de las demás reglas.

- [ ] **Step 3: Añadir el test de cobertura del propio arnés**
  - Un test que lee `sdd/gates/registry.toml`, recorre cada `sdd/gates/check_*.py`, extrae con regex las cadenas de regla que aparecen en `finding(...)`, y afirma que **cada regla** aparece en algún `CASOS` de este archivo.
  - Se permite una lista explícita `SIN_ARNES_TODAVIA` de reglas exentas, con un comentario por entrada. Vacía es lo ideal; lo prohibido es una regla exenta en silencio.
  - Esto es lo que impide que el arnés se quede atrás cuando alguien añada una regla.

- [ ] **Step 4: Verificar que el arnés falla por los motivos correctos**
  - `python -m unittest tests.test_gate_conformance -v`
  - Deben fallar **exactamente** los subTests golden-pass de `nfr-ingles-valido` (`nfr-no-medible`), `nfr-gate-citado` (`nfr-gate-inexistente`) y `adr-valido` (`adr-sin-alternativas`). Los golden-fail deben pasar ya.
  - Si falla algo más, **para y entiéndelo**: hay un defecto de G2 que este plan no había catalogado, y el catálogo debe crecer antes de tocar el gate.

**Verificación:**
- [ ] `python -m unittest discover -s tests` — todo verde salvo los 3 subTests golden-pass esperados.
- [ ] Los fixtures existen con su `README.md` de procedencia.
- [ ] El test de cobertura del arnés pasa (o su lista de exenciones está justificada línea por línea).

**Anti-patrones a evitar:**
- ❌ Escribir el fixture golden-pass a mano "parecido" al real. Cópialo de `project/`.
- ❌ Pruebas parametrizadas de pytest. No hay pytest configurado; usa `subTest`.
- ❌ Importar el checker como módulo. Los checkers son subprocesos con un contrato de stdout; el arnés debe probar el contrato, no las funciones internas.
- ❌ Ajustar un fixture para que el test pase. Si un golden-fail deja de fallar, el gate se relajó: eso es el hallazgo, no un estorbo.

---

## Task 2: Cuatro correcciones de medición en G2

**Files:**
- Modify: `sdd/gates/check_arch_spec.py:32-51`
- Test: `tests/test_gate_conformance.py` (ya escrito en la Tarea 1)

**Interfaces:**
- Sin cambios de firma. Sin cambios de umbral (`2` alternativas sigue siendo `2`).
- Produces: los mismos `rule` de siempre — `nfr-no-medible`, `nfr-gate-inexistente`, `adr-sin-alternativas`, `adr-sin-coste`.

- [ ] **Step 1: Claves NFR en inglés e inglés-primero, y como clave, no como substring**
  - Hoy `for key in ["umbral", "metrica", "gate_id"]: if key not in block` es una prueba de substring en español sobre artefactos que `CLAUDE.md` exige en inglés.
  - Reemplaza por:

    ```python
    # Los artefactos van en ingles (CLAUDE.md); el gate probaba substrings en
    # espanol, asi que toda entrada NFR conforme generaba dos hallazgos falsos.
    # Ademas era substring: 'metrica' dentro de una frase del valor contaba como
    # campo presente. Ahora se exige la CLAVE, al inicio de linea.
    NFR_FIELDS = {
        "threshold": ("threshold", "umbral"),
        "metric": ("metric", "metrica", "métrica"),
        "gate_id": ("gate_id",),
    }
    ...
    for canonical, aliases in NFR_FIELDS.items():
        if not any(re.search(rf"^\s*{alias}\s*:", block, re.M) for alias in aliases):
            out.append(finding("spec/20_arch/nfr.yaml", 0, "nfr-no-medible",
                               f"{nid} sin campo {canonical}"))
    ```

- [ ] **Step 2: `gate_id` entrecomillado**
  - `re.search(r"gate_id:\s*(\S+)", block)` captura `"G2"` **con las comillas** cuando el arquitecto escribe YAML entrecomillado, y `"G2"` no está en `known_gates` → `nfr-gate-inexistente` falso.
  - Reemplaza por `re.search(r"gate_id:\s*[\"']?([A-Za-z0-9_.\-]+)[\"']?", block)`.

- [ ] **Step 3: `id:` anclado**
  - `if "id:" not in block: continue` deja pasar un bloque que solo tiene `gate_id:` (porque contiene `id:`), y entonces `re.search(r"id:\s*(\S+)")` captura el valor del gate como si fuera el id del NFR.
  - Reemplaza el guard y la extracción por una sola búsqueda anclada, y salta el bloque si no hay id propio:

    ```python
    nid_match = re.search(r"^\s*(?:-\s*)?id:\s*(\S+)", block, re.M)
    if nid_match is None:
        continue
    nid = nid_match.group(1)
    ```

- [ ] **Step 4: Contar alternativas, no la palabra "alternativa"**
  - `t.count("alternativa") < 2` cuenta vocabulario. Un ADR con `## Alternativas consideradas` y dos opciones numeradas debajo da 1 y se reprueba. Es la causa medida del 79 % de fallo de G2.
  - Reemplaza por un conteo estructural dentro de la sección:

    ```python
    ALT_HEADING = re.compile(r"^#{1,6}\s*.*alternativ", re.M | re.I)
    NEXT_HEADING = re.compile(r"^#{1,6}\s+", re.M)


    def discarded_alternatives(text: str) -> int:
        """Cuenta alternativas ENUMERADAS dentro de la seccion de alternativas.

        Antes se contaba la palabra en todo el archivo. Un ADR real y correcto
        ('## Alternativas consideradas' + dos opciones numeradas) daba 1 y se
        reprobaba: se medía el vocabulario, no el contenido.
        """
        heading = ALT_HEADING.search(text)
        if heading is None:
            return 0
        rest = text[heading.end():]
        end = NEXT_HEADING.search(rest)
        section = rest[:end.start()] if end else rest
        top = len(re.findall(r"^(?:\d+[.)]|[-*+])\s+\S", section, re.M))
        subheads = len(re.findall(r"^#{3,6}\s+\S", section, re.M))
        indented = len(re.findall(r"^[ \t]+(?:\d+[.)]|[-*+])\s+\S", section, re.M))
        return max(top, subheads) or indented
    ```
  - Y en el bucle de ADR: `if discarded_alternatives(adr.read_text(encoding="utf-8")) < 2:`. **El umbral `2` no cambia.**
  - Nota: el `t = adr.read_text().lower()` actual también hay que arreglarlo para pasar `encoding="utf-8"` — en Windows con cp1252 un ADR con acentos puede reventar el gate y producir `gate-roto`. La comprobación de coste sigue usando el texto en minúsculas.

- [ ] **Step 5: Verificar contra el arnés**
  - `python -m unittest tests.test_gate_conformance -v` → **todo verde**, golden-pass y golden-fail.
  - Si algún golden-fail deja de fallar, la corrección se pasó de largo y se convirtió en relajación: revierte y ajusta.

**Verificación:**
- [ ] Los 3 subTests golden-pass que fallaban ahora pasan.
- [ ] Los 5 subTests golden-fail siguen fallando el gate (es decir, siguen pasando el test).
- [ ] `python -m unittest discover -s tests` verde completo.
- [ ] `git diff sdd/gates/check_arch_spec.py` no contiene ningún cambio de umbral numérico ni de `registry.toml`.
- [ ] Re-corre G2 sobre los artefactos reales sin modificarlos:
  `python sdd/gates/check_arch_spec.py --workdir project/acortador-min/tarea-1` — los hallazgos espurios de `nfr-no-medible` y `adr-sin-alternativas` desaparecen; los reales (`gate_id: G-test` inexistente) permanecen.

**Anti-patrones a evitar:**
- ❌ Bajar el umbral de 2 a 1. Eso sí es relajar el gate.
- ❌ Aceptar cualquier bloque con la palabra "alternativa" en cualquier sitio. Estructura, no vocabulario.
- ❌ Tocar `registry.toml` para "arreglar" `gate_id: G-test`. Ese hallazgo es correcto: el arquitecto inventó un gate. El arreglo va en el prompt del arquitecto, no en el registro.

---

## Task 3: Reportes versionados por intento y `tier` en los R\*

Dos huecos de observabilidad que hay que tapar **antes** de medir el efecto del bucle, o no habrá línea base creíble.

**Files:**
- Modify: `sdd/runtime/optimized_gates.py:36-41` (entorno de los R\*), `:184-186` (`_save_report`)
- Modify: `sdd/integrations/model_router.py:275,283` (`resolve_review`)
- Test: `tests/test_optimized_gates.py`, `tests/test_model_router.py`

**Interfaces:**
- `_save_report(directory: Path, node_id: str, report: dict) -> None` — mismo contrato, más un histórico.
- `resolve_review(...)` — misma firma, distinta política de tier para R2.

- [ ] **Step 1: `tier` en el entorno de los gates R\***
  - `optimized_gates.py:40-41` propaga `SDD_REVIEW_MODEL` a `SDD_MODEL`, pero nunca fija `SDD_MODEL_TIER`. `providers.complete` registra `tier=os.environ.get("SDD_MODEL_TIER","")` → toda llamada de R1/R2 aparece en `usage.jsonl` con `tier=""` y el coste de revisión no se puede atribuir a un tier.
  - Fija `SDD_MODEL_TIER` (y `SDD_SELECTION_REASON="review"`) en el mismo bloque, tomándolo de la selección de revisión resuelta.

- [ ] **Step 2: R2 en `balanced`, no en `frontier`**
  - `model_router.resolve_review` fija `"frontier"` en duro (líneas 275 y 283). R2 es revisión de código sobre un diff acotado: no necesita el tier más caro. R1 (revisión de especificación, criterio abierto) se queda en `frontier`.
  - Parametriza el tier por etiqueta de revisión: `R1 → frontier`, `R2 → balanced`. **No** lo hagas configurable desde `pipeline.toml`: eso sería tocar `[gates]`.
  - Cuidado con `_pick` (`model_router.py:183-191`): si el tier pedido no existe, cae **al de mayor capacidad** en silencio. Verifica que `balanced` esté poblado antes de asumir el ahorro, y si no lo está, deja registrado el `fallback_reason` que ya existe.

- [ ] **Step 3: Reportes con historial por intento**
  - Hoy `.agent/reports/{node}.{gate_id}.json` se sobrescribe: el intento 1 desaparece cuando corre el intento 2. El único rastro por intento vive en `chronicle` (`attempts` en `archive_gate_result`).
  - `_save_report` sigue escribiendo el archivo canónico (nada que lo consuma se rompe) **y además** anexa una línea a `.agent/reports/{node}.{gate_id}.history.jsonl` con `{at, status, findings: [rule...]}`.
  - Usa `metrics._append`-equivalente o `os.open(..., O_APPEND|O_CREAT)`: los gates corren en un `ThreadPoolExecutor`, y un `write_text` concurrente intercala.

- [ ] **Step 4: Pruebas**
  - `tests/test_optimized_gates.py`: dos corridas del mismo nodo dejan 2 líneas en el `.history.jsonl` y el JSON canónico refleja la última.
  - `tests/test_model_router.py`: `resolve_review` devuelve `balanced` para R2 y `frontier` para R1; con `balanced` vacío, el `fallback_reason` queda poblado.

**Verificación:**
- [ ] `python -m unittest discover -s tests` verde.
- [ ] `python -m sdd demo` y luego `cat project/<demo>/.agent/reports/*.history.jsonl` muestra una línea por intento.
- [ ] Ninguna llamada de R1/R2 en `usage.jsonl` sale con `tier=""`.

---

## Task 4: Victorias gratis de coste

Independientes del bucle, cero riesgo, ejecutables hoy. Van antes de la Tarea 6 porque el bucle multiplica los tokens de entrada y el caché es lo que hace tolerable esa multiplicación.

**Files:**
- Modify: `sdd/integrations/providers.py:413-414` (breakpoint), `:488` (caché de DeepSeek)
- Test: `tests/test_provider_contract.py`

- [ ] **Step 1: Breakpoint de caché en el bloque `system`**
  - Borra `kwargs["cache_control"] = {"type": "ephemeral"}` (no es un parámetro válido de la request; nunca creó un breakpoint — medido: 0 hits en 310 registros).
  - Sustituye por un bloque de `system` con el marcador, que según el orden de render (`tools → system → messages`) cachea también las herramientas cuando existan:

    ```python
    if os.environ.get("SDD_PROMPT_CACHE", "1") != "0":
        kwargs["system"] = [{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}]
    else:
        kwargs["system"] = system
    ```
  - **Espera 0 hits en la primera corrida y hits desde la segunda.** El system prompt de un nodo es estable entre llamadas del mismo nodo; el `user` (specs, inventario, defectos) no lo es y queda después del breakpoint, que es exactamente donde debe estar.

- [ ] **Step 2: Campos de caché de DeepSeek**
  - `providers.py:488` hace `_add_usage(u.get("prompt_tokens"), u.get("completion_tokens"))` y descarta los campos de caché que el proveedor sí devuelve.
  - Lee los campos de caché del `usage` de la respuesta y pásalos como 3.º y 4.º argumento de `_add_usage`. **Los nombres exactos de esos campos no están verificados** (ver Fase 0): impleméntalo con `u.get(...)` tolerante sobre los candidatos y **registra en `metrics` las claves realmente presentes en `usage`** la primera vez, para que la siguiente corrida real las confirme en vez de adivinarlas.
  - No inventes el nombre y lo declares hecho: si tras una corrida real las claves siguen a 0, el hallazgo es "el proveedor no las expone con ese nombre", y se documenta.

- [ ] **Step 3: Pruebas de contrato sin red**
  - En `tests/test_provider_contract.py`, con un cliente falso que captura `kwargs`: afirma que `system` es una lista de un bloque con `cache_control` cuando `SDD_PROMPT_CACHE` no es `"0"`, y una cadena cuando es `"0"`.
  - Afirma que **no** queda ningún `cache_control` en el nivel superior de `kwargs`.

**Verificación:**
- [ ] `python -m unittest discover -s tests` verde.
- [ ] Una corrida real de dos llamadas al mismo nodo produce `cache_read_input_tokens > 0` en la segunda línea de `usage.jsonl`. **Este es el criterio; sin él la tarea no está hecha.**

---

## Task 5: `complete_with_tools` en la capa de proveedor

La capa de proveedor solo transporta. No sabe qué es un gate, no escribe archivos, no decide nada.

**Files:**
- Modify: `sdd/integrations/providers.py`
- Test: `tests/test_provider_contract.py`

**Interfaces:**
- Produces:
  ```python
  ToolDispatch = Callable[[str, dict[str, object]], tuple[str, bool]]  # (name, input) -> (content, is_error)

  def complete_with_tools(system: str, user: str, tools: list[dict[str, object]],
                          dispatch: ToolDispatch, *, max_iterations: int = 8,
                          should_stop: Callable[[], bool] | None = None,
                          ) -> dict[str, object]:
      """Bucle agentico acotado. Devuelve
      {"text": str, "iterations": int, "stop": str, "tool_calls": [{"name","is_error"}]}.

      No escribe archivos ni ejecuta gates: todo efecto lo produce `dispatch`,
      que inyecta el llamador. Este modulo solo transporta.
      """
  ```
- Consumes: `_with_retry`, `_add_usage`, `_max_tokens`, `_temperature`, `accepts_sampling`, `process_control.timeout_seconds` (todos existentes).

- [ ] **Step 1: Extraer el envoltorio de telemetría**
  - `complete` (`providers.py:339-377`) tiene el `try/finally` que hace `metrics.record` + `metrics.record_usage`. **No lo dupliques.** Extrae un privado `_instrumented(provider, input_chars, fn)` y haz que `complete` y `complete_with_tools` lo usen.
  - Añade `iterations` y `tool_calls` como campos extra de `metrics.record` para el camino de bucle: es la única forma de medir después cuántas iteraciones cuesta de verdad.

- [ ] **Step 2: `_anthropic_tools`**
  - Bucle manual (no el `tool_runner`, que es beta y no reanuda `pause_turn`). Estructura:

    ```python
    def _anthropic_tools(system, user, tools, dispatch, max_iterations, should_stop):
        _require_env("ANTHROPIC_API_KEY", "anthropic")
        import anthropic
        model = os.environ.get("SDD_MODEL", ANTHROPIC_DEFAULT_MODEL)
        client = anthropic.Anthropic(
            timeout=process_control.timeout_seconds("provider_timeout_seconds"))
        cache = os.environ.get("SDD_PROMPT_CACHE", "1") != "0"
        # Orden de render: tools -> system -> messages. El breakpoint en el ultimo
        # bloque de system cachea tools + system juntos.
        system_blocks = [{"type": "text", "text": system}]
        if cache:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
        transcript, calls = [], []
        for iteration in range(1, max_iterations + 1):
            kwargs = {"model": model, "max_tokens": _max_tokens(),
                      "system": system_blocks, "messages": messages, "tools": tools}
            if accepts_sampling(model):
                kwargs["temperature"] = _temperature()

            def once():
                with client.messages.stream(**kwargs) as stream:
                    return stream.get_final_message()

            msg = _with_retry(once, "anthropic.messages.stream+tools")
            usage = getattr(msg, "usage", None)
            _add_usage(getattr(usage, "input_tokens", 0),
                       getattr(usage, "output_tokens", 0),
                       getattr(usage, "cache_read_input_tokens", 0),
                       getattr(usage, "cache_creation_input_tokens", 0))
            stop = getattr(msg, "stop_reason", None)
            if stop == "refusal":
                raise ProviderError(...)          # mismo mensaje que _anthropic
            transcript.append("".join(b.text for b in msg.content if b.type == "text"))
            if stop != "tool_use":
                return _result(transcript, iteration, str(stop), calls)
            messages.append({"role": "assistant", "content": msg.content})
            results = []
            for block in msg.content:
                if block.type != "tool_use":
                    continue
                content, is_error = dispatch(block.name, dict(block.input))
                calls.append({"name": block.name, "is_error": is_error})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            if cache:
                _move_turn_breakpoint(messages)
            if should_stop is not None and should_stop():
                return _result(transcript, iteration, "finish", calls)
        return _result(transcript, max_iterations, "max_iterations", calls)
    ```
  - `stop_reason == "refusal"` debe seguir siendo `ProviderError`: llega como HTTP 200 y sin esta comprobación el turno acaba en silencio sin entregable.
  - `stop_reason == "max_tokens"` a mitad de un `tool_use` deja el JSON de entrada truncado e inservible. **No** intentes continuar con prefill (`claude-opus-5` no está en `PREFILL_OK_MODELS`): anexa un mensaje de usuario pidiendo menos archivos por llamada y consume una iteración.
  - `stop_reason == "pause_turn"`: reenvía sin ejecutar herramientas y consume una iteración.

- [ ] **Step 3: `_move_turn_breakpoint`**
  - El límite de la API es 4 breakpoints y `system` ya gasta uno. Hay que **mover** el del historial, no acumular:

    ```python
    def _move_turn_breakpoint(messages: list[dict[str, object]]) -> None:
        """Un solo breakpoint movil sobre el historial: acumularlos agota el
        limite de 4 de la API y a partir de ahi no se cachea nada."""
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
        last = messages[-1].get("content")
        if isinstance(last, list) and last and isinstance(last[-1], dict):
            last[-1]["cache_control"] = {"type": "ephemeral"}
    ```

- [ ] **Step 4: Despacho por proveedor, sin degradación silenciosa**
  - `complete_with_tools` enruta a `_anthropic_tools` para `anthropic`. Para cualquier otro proveedor lanza `ProviderError` con un mensaje que nombre el proveedor y diga que el bucle de herramientas no está implementado para él.
  - **No** hagas fallback aquí. El fallback es decisión de `agent.py` (Tarea 6), que es quien puede registrar la degradación en telemetría.

- [ ] **Step 5: Pruebas de contrato sin red**
  - Cliente falso que devuelve una secuencia guionada de mensajes: `tool_use(write_files)` → `tool_use(run_gates)` → `end_turn`.
  - Afirma: `tools` va en `kwargs`; `system` es lista con `cache_control`; el mensaje de assistant se anexa antes de los resultados; cada `tool_result` lleva el `tool_use_id` correcto; hay **exactamente un** `cache_control` en `messages` tras tres iteraciones; `stop_reason == "refusal"` lanza `ProviderError`; `max_iterations` se respeta; `should_stop()` corta.

**Verificación:**
- [ ] `python -m unittest discover -s tests` verde.
- [ ] `complete_with_tools` no importa nada de `sdd.gates` ni de `sdd.runtime`. Si lo hace, la separación de capas se rompió.

**Anti-patrones a evitar:**
- ❌ Usar `client.beta.messages.tool_runner`. Es beta y no reanuda `pause_turn`: un turno pausado terminaría en silencio con la respuesta truncada.
- ❌ Poner un `cache_control` nuevo cada iteración sin quitar el anterior.
- ❌ Que la capa de proveedor sepa qué es `run_gates`.

---

## Task 6: Turno auto-verificante en `agent.py`, solo en `architect`

**Files:**
- Modify: `sdd/runtime/agent.py`
- Modify: `sdd/runtime/optimized_gates.py:141` (dos parámetros aditivos)
- Modify: `sdd/pipeline.toml` (un campo, solo en `[[node]]` de `architect`)
- Test: `tests/test_self_verify.py` (crear)

**Interfaces:**
- `run_node_gates(node_id, workdir, pipeline, *, gate_filter: Callable[[str], bool] | None = None, reports_dir: str = ".agent/reports") -> list[dict]` — ambos por defecto reproducen el comportamiento actual **byte por byte**.
- `TurnState.dispatch(name, payload) -> tuple[str, bool]`, `tool_specs(allowed) -> list[dict]`, `self_verifying_turn(...) -> TurnState`.

- [ ] **Step 1: Parámetros aditivos en `run_node_gates`**
  - `gate_filter`: si se pasa, se aplica a `gate_ids` antes de todo lo demás. El bucle lo usará con `lambda gid: not gid.startswith("R")`.
  - `reports_dir`: ruta relativa al workdir donde persistir. **Imprescindible**: sin ella la corrida in-turn sobrescribe `.agent/reports/{node}.{gate_id}.json` y contamina el rastro de auditoría del orquestador con veredictos asesores.
  - Verifica que G7 mantiene su prioridad absoluta y su corte, y que el orden de los `skip_if_prior_failed` no cambia.
  - Prueba de regresión en `tests/test_optimized_gates.py`: llamada sin los parámetros nuevos → reportes y rutas idénticos a antes.

- [ ] **Step 2: `TurnState` — la única puerta de efectos**
  ```python
  class TurnState:
      """Efectos del turno del agente. Es el UNICO lugar con permiso de escritura.

      El veredicto de los gates aqui es ASESOR: el orquestador los vuelve a correr
      fuera del turno y ese es el que manda. Este bucle no puede convertir un rojo
      en verde; solo puede hacer que el primer verde llegue en la primera pasada.
      """

      def __init__(self, workdir: Path, node: str, allowed: list[str],
                   cfg: dict[str, object]) -> None: ...

      def dispatch(self, name: str, payload: dict[str, object]) -> tuple[str, bool]: ...
      def should_stop(self) -> bool: ...
  ```
  - Atributos: `written: list[str]`, `skipped: list[tuple[str, str]]`, `gate_runs: int`, `last_reports: list[dict]`, `finished: bool`, `summary: str`, `blocked_reason: str`.
  - `_write(payload)`: convierte `payload["files"]` (lista de `{path, content}`) a **tuplas** `[(path, content)]` — que es lo que `write_files` espera — y llama a `write_files(self.workdir, self.allowed, files)`. Devuelve las líneas `escrito …` / `OMITIDO …: …`, con `is_error=True` **solo** si no se escribió absolutamente nada.
  - `_gates()`: si `gate_runs >= GATE_BUDGET`, devuelve el mensaje de presupuesto agotado con `is_error=True`. Si no, incrementa y llama:
    ```python
    reports = optimized_gates.run_node_gates(
        self.node, str(self.workdir), self.cfg,
        gate_filter=lambda gid: not gid.startswith("R"),
        reports_dir=f".agent/self-verify/{self.node}.{self.gate_runs}")
    ```
    Devuelve `_render_reports(reports)` con **`is_error=False` siempre**: un gate que reporta hallazgos es una llamada de herramienta exitosa con veredicto negativo, no un fallo de transporte. Marcarlo como error hace que el modelo lo lea como un problema de infraestructura.
  - `_render_reports`: acotado — máximo 12 hallazgos por gate y 300 caracteres de evidencia por hallazgo, con `… y N hallazgos más`. Sin cota, un G6 con 200 hallazgos revienta el presupuesto de tokens del turno.
  - `_finish(payload)`: fija `finished`, `summary`, `blocked_reason`. `should_stop()` devuelve `self.finished`.

- [ ] **Step 3: `tool_specs(allowed)` — tres herramientas, `strict: True`**
  - `write_files`: `{"files": [{"path": str, "content": str}]}`, `required: ["files"]`, `additionalProperties: false`. La descripción enumera los `allowed` y dice que cualquier otra ruta se **omite** y se le informa.
  - `run_gates`: schema vacío (`{"type":"object","properties":{},"additionalProperties":false}`). La descripción indica el presupuesto restante y que hay que **escribir primero** (si no, G0 reportará entregables ausentes, correctamente).
  - `finish`: `{"summary": str, "blocked_reason"?: str}`, `required: ["summary"]`. La descripción repite la regla de `CLAUDE.md`: `blocked_reason` solo si falta un insumo que no te corresponde producir; nunca simules un entregable ausente.
  - `strict: True` va al nivel superior de cada herramienta, hermano de `input_schema`.

- [ ] **Step 4: Addendum anti-Goodhart al prompt**
  - Añade a `PROTOCOL` (o a un `SELF_VERIFY_PROTOCOL` que lo reemplace en el camino de bucle) un bloque explícito:
    > Los hallazgos de un gate son **síntomas**. Corrige la causa. Prohibido: crear barrel files o re-exportadores para bajar el conteo de líneas; repetir palabras clave para satisfacer un conteo; dejar stubs vacíos o `TODO` para que un gate pase. No puedes modificar los gates y no debes intentarlo. Si un hallazgo te parece incorrecto, escribe el artefacto correcto y déjalo así: el revisor humano verá la discrepancia.
  - El camino de bucle **no** debe usar el protocolo `<<<FILE:>>>` — ahí sobra, porque los archivos van por la herramienta. Mantén `<<<BLOCKED:>>>` fuera también: en el bucle eso es `finish(blocked_reason=…)`.

- [ ] **Step 5: `self_verifying_turn` y la ramificación en `main`**
  - Sustituye `agent.py:330` por:
    ```python
    if LOOP_ENABLED and node_cfg.get("self_verify") and providers.current_provider() == "anthropic":
        turn = self_verifying_turn(workdir, node, allowed, cfg, system_prompt, user)
        ...
    else:
        if node_cfg.get("self_verify"):
            print("  [agent] self_verify pedido pero sin bucle de herramientas para "
                  f"proveedor={providers.current_provider()}; una sola llamada",
                  file=sys.stderr)
            metrics.record(str(workdir), "agent_loop_unavailable", node=node,
                           provider=providers.current_provider())
        text = providers.complete(system_prompt, user)
        ...
    ```
  - **Contrato de exit, sin cambios:**
    - `turn.blocked_reason` y `not turn.written` → `3`
    - `turn.written` → `0`
    - nada escrito → `1`
    - El estado de los gates in-turn **no influye en el exit code**. Repite esto en un comentario del código: es lo que mantiene intacta la FSM de defectos del orquestador.
  - `_archive_call` recibe `text=turn_transcript` para que el chronicle conserve el razonamiento del turno completo, y `written`/`skipped` acumulados de todo el bucle.
  - Banderas: `SDD_AGENT_LOOP` (por defecto `1`; `0` fuerza el camino antiguo en todos los nodos — es el rollback), `SDD_AGENT_GATE_BUDGET` (por defecto `3`), `SDD_AGENT_MAX_ITERATIONS` (por defecto `8`).

- [ ] **Step 6: `self_verify = true` en `architect` y en nada más**
  - En `sdd/pipeline.toml`, dentro del `[[node]]` de `architect`. **No** en `[budget]`, **no** en `[gates]`.
  - Justificación del orden: `architect` concentra el 79 % de fallos de G2, 3 de las 5 escaladas de la fase lineal, y es el segundo mayor consumidor de tokens. Es donde el mecanismo tiene más que arreglar y donde su fallo es más visible.

- [ ] **Step 7: `tests/test_self_verify.py`**
  - Proveedor falso monkeypatcheado sobre `providers.complete_with_tools` con guiones deterministas:
    - **Camino feliz:** `write_files` (válido) → `run_gates` (verde) → `finish` → exit 0, archivos en disco.
    - **Corrección:** `write_files` (ADR con 1 alternativa) → `run_gates` (rojo, `adr-sin-alternativas`) → `write_files` (2 alternativas) → `run_gates` (verde) → `finish` → exit 0. **Esta es la prueba que demuestra la tesis del plan.**
    - **Presupuesto de gates:** 4 llamadas a `run_gates` → la 4.ª devuelve presupuesto agotado y no ejecuta subprocesos.
    - **Presupuesto de iteraciones:** un guion que nunca llama a `finish` → termina en `max_iterations` y, si escribió, exit 0.
    - **Ruta fuera de `allowed`:** se omite, el modelo recibe `OMITIDO`, y si no escribió nada más → exit 1.
    - **Filtrado de R\*:** con un nodo cuyos gates incluyen R1, `run_gates` in-turn **no** ejecuta R1. Aserción sobre la lista de `gate_id` de los reportes.
    - **Aislamiento de reportes:** tras el turno, `.agent/reports/` no contiene ningún archivo escrito por la corrida in-turn; `.agent/self-verify/architect.1/` sí.
    - **Bloqueo:** `finish(blocked_reason=…)` sin archivos → exit 3.
    - **Degradación visible:** `self_verify=true` con proveedor no-anthropic → una sola llamada **y** una línea `agent_loop_unavailable` en `metrics.jsonl`.

**Verificación:**
- [ ] `python -m unittest discover -s tests` verde, incluidos `test_gate_conformance` y `test_optimized_gates`.
- [ ] `python -m sdd demo` → `done | tareas: 5/5`. El demo no toca el bucle (usa `simulate_cmd`), así que esto prueba que no rompiste el plano de control.
- [ ] `SDD_AGENT_LOOP=0 python -m unittest discover -s tests` verde: el camino antiguo sigue vivo.
- [ ] Una corrida real con `--project` de un solo nodo `architect`: el chronicle muestra ≥2 iteraciones cuando el primer intento sale rojo, y `.agent/self-verify/` tiene los reportes asesores.

**Anti-patrones a evitar:**
- ❌ Que el exit code dependa del veredicto in-turn. Rompe la FSM de defectos y hace que el bucle pueda enmascarar un rojo.
- ❌ Escribir los reportes in-turn en `.agent/reports/`. Contamina la auditoría.
- ❌ Meter R1/R2 en el bucle. Es el agente juzgándose, y es la parte cara de la factura.
- ❌ Una segunda puerta de escritura además de `write_files`. Toda escritura pasa por ahí o el guard de `allowed` y el anti-traversal dejan de valer.
- ❌ Fallback silencioso cuando el proveedor no soporta el bucle.

---

## Task 7: Extender a `product` y `planner`

Solo después de al menos una corrida real completa con `architect` en bucle y su medición registrada.

- [ ] **Step 1:** Revisa la telemetría de la corrida de `architect`: iteraciones por turno, tokens de entrada con y sin caché, hallazgos de G2 en la primera pasada del orquestador. Si el coste por corrida subió más del 40 % sin mejorar la tasa de primera pasada, **para el despliegue** y reporta: el mecanismo no está pagando.
- [ ] **Step 2:** `self_verify = true` en `product` (gates G0, G1) y `planner` (G0, G10).
- [ ] **Step 3:** Añade a `tests/test_self_verify.py` un caso por nodo con su cadena de gates real.
- [ ] **Step 4:** Corrida real de la fase lineal completa hasta el gate humano.

**Verificación:**
- [ ] Tasa de primera pasada de G1/G2/G10 medida y comparada contra la línea base.
- [ ] Ninguna escalada nueva atribuible al bucle.

---

## Task 8: Extender a `dev_backend` y `dev_frontend` — con prerrequisito declarado

**Prerrequisito, no parte de esta tarea:** los nodos `dev_*` resuelven al tier `economy` (`model_router.DEFAULT_ROLE_TIERS`), que es un proveedor OpenAI-compatible. `complete_with_tools` solo tiene camino Anthropic. Hay que implementar `_openai_tools` **primero**, y eso exige verificar contra documentación —no de memoria— las formas de payload que la Fase 0 dejó explícitamente sin verificar: `tools`/`tool_choice` en la request, la ruta exacta de `choices[0].message.tool_calls`, si `function.arguments` es string JSON o objeto, la forma de `{role:"tool", tool_call_id, content}`, el valor de `finish_reason`, si DeepSeek `/beta` soporta tool calling y cómo interactúa con su campo `prefix`, y las divergencias de GLM/Kimi/DashScope.

- [ ] **Step 1:** Verificar esas formas con documentación oficial y fecha de consulta. Lo que no se pueda verificar se marca como no verificado y **no** se implementa a ciegas.
- [ ] **Step 2:** `_openai_tools` + pruebas de contrato sin red por cada proveedor cuyo payload se haya verificado.
- [ ] **Step 3:** `self_verify = true` en `dev_backend`, luego en `dev_frontend`. Uno a la vez, con una corrida entre medias.
- [ ] **Step 4:** Vigila G6 (imports) y G4 (tamaño de archivo) en el bucle: son los dos hallazgos con mayor tentación de gaming. Revisa manualmente el primer diff en el que el agente reaccione a G4 y confirma que dividió por responsabilidad y no con re-exportadores.

**Verificación:**
- [ ] Ningún barrel file nuevo en `src/`. Búscalo explícitamente.
- [ ] Coste por corrida medido y comparado.

---

## Task 9: Extender a `qa` — el caso caro

`qa` corre G9, que **ejecuta la suite completa**. Un `run_gates` in-turn aquí puede costar minutos, no segundos. Va última por eso.

- [ ] **Step 1:** Confirma que el caché de árbol de G9 (`.agent/g9_last_pass.txt`, `SDD_G9_CACHE`, `check_suite.py:143-165`) evita reejecutar la suite cuando el árbol no cambió. Sin ese caché, esta tarea no es viable.
- [ ] **Step 2:** `SDD_AGENT_GATE_BUDGET=2` específicamente para `qa`: cada llamada es una suite entera.
- [ ] **Step 3:** `self_verify = true` en `qa`. Verifica que el `blame()` de G9 (`check_suite.py:60-83`) sigue prefiriendo archivos de producción sobre archivos de prueba — el bucle no debe cambiar el enrutado de defectos.
- [ ] **Step 4:** Mide la latencia añadida del nodo `qa`. Si excede `agent_timeout_seconds`, el agente muere por timeout y el remedio empeora la enfermedad: baja el presupuesto a 1 o revierte este nodo.

**Verificación:**
- [ ] `qa` no excede `agent_timeout_seconds` en una corrida real.
- [ ] La atribución de defectos de G9 no cambió.

---

## Final Verification Phase

- [ ] `python -m unittest discover -s tests` — verde completo.
- [ ] `SDD_AGENT_LOOP=0 python -m unittest discover -s tests` — verde completo (el camino antiguo vive).
- [ ] `python -m sdd demo` → `done | tareas: 5/5`.
- [ ] `python -m sdd gates --node architect --workdir project/<proyecto-real>` sin hallazgos espurios.
- [ ] `git diff sdd/gates/registry.toml` — **vacío**.
- [ ] `git diff sdd/pipeline.toml` — solo campos `self_verify` dentro de `[[node]]`; `[budget]` y `[gates]` intactos.
- [ ] `grep -rn "cache_control" sdd/integrations/providers.py` — todas las apariciones están dentro de un bloque de contenido, ninguna en el nivel superior de `kwargs`.
- [ ] El test de cobertura del arnés pasa con `SIN_ARNES_TODAVIA` vacía o justificada línea por línea.
- [ ] `FLUJO.md`, `HANDOFF.md` y `README.md` documentan `self_verify`, `SDD_AGENT_LOOP`, `SDD_AGENT_GATE_BUDGET`, `SDD_AGENT_MAX_ITERATIONS` y el directorio `.agent/self-verify/`.
- [ ] Reindexar el grafo: `index_repository(project="D-Miguel-auto_scrum", mode="full")` — la Tarea 5 y la 6 añaden símbolos que el grafo no tiene.

---

## Criterio de éxito medible

| Métrica | Línea base (medida) | Objetivo | Dónde se mide |
|---|---|---|---|
| Tasa de primera pasada de G2 en `architect` | 21 % (2 de 12 corridas) | ≥ 70 % | `.agent/reports/architect.G2.history.jsonl` |
| Hallazgos espurios de G2 por corrida | ≥ 2 por entrada NFR + 1 por ADR conforme | 0 | Tarea 2, arnés de conformidad |
| Hits de caché de prompt | 0 en 310 registros | > 0 desde la 2.ª llamada del mismo nodo | `cache_read_input_tokens` en `usage.jsonl` |
| Iteraciones por turno de `architect` | n/a (no existía el bucle) | mediana ≤ 3 | campo `iterations` de `metrics.jsonl` |
| Coste por corrida | medido en `usage.jsonl` | ≤ +40 % con tasa de primera pasada ≥ 70 % | `usage.jsonl` agregado |
| Llamadas de R1/R2 con `tier=""` | 100 % | 0 % | `usage.jsonl` |

Si la tasa de primera pasada de G2 no sube por encima del 70 % **después de la Tarea 2 sola**, el diagnóstico estaba mal y las Tareas 5-9 no deben ejecutarse: el problema no era que el agente no viera el veredicto.

---

## Rollback

Por capas, de la más barata a la más cara:

1. **Bucle:** `SDD_AGENT_LOOP=0` en el entorno. Vuelve al camino de una sola llamada en todos los nodos, sin tocar código ni configuración. Efecto inmediato.
2. **Un nodo:** quita `self_verify = true` de ese `[[node]]` en `pipeline.toml`. Los demás siguen en bucle.
3. **Caché:** `SDD_PROMPT_CACHE=0`.
4. **Correcciones de G2:** `git revert` del commit de la Tarea 2. El arnés de la Tarea 1 se queda: sus fixtures golden-pass volverán a fallar y eso es información correcta, no un test roto. Documenta por qué en el commit del revert.
5. **`complete_with_tools`:** código muerto si nadie lo llama. No hace falta revertirlo para desactivar el mecanismo.

Ningún paso del rollback requiere tocar `registry.toml`, `[budget]` ni `[gates]`.

---

## Lo que este plan NO hace

Declarado para que nadie lo lea como incluido:

- **No** cambia el orquestador ni el grafo de LangGraph. `graph_runtime.agent_node` sigue igual.
- **No** cambia la FSM de defectos (`workflow_defects.py`), ni los contadores de reintento, ni las `ENVIRONMENT_RULES`.
- **No** quita ni debilita ningún gate, ni cambia ningún umbral.
- **No** toca el gate humano ni las decisiones firmadas.
- **No** resuelve el hallazgo de retención de datos: código propietario de Athelos sigue yendo a DeepSeek (jurisdicción RPC) en los nodos `dev_*`. Es una decisión de proveedor, no de arquitectura del bucle, y sigue pendiente.
- **No** arregla el truncado silencioso de `gather_specs` en `MAX_CHARS_TOTAL = 160000`. Es un defecto real y separado.
- **No** actualiza los IDs de modelo obsoletos de `OPENAI_PRESETS` (`glm-4-plus`, `moonshot-v1-32k`, `qwen`). Separado.

---

## La siguiente decisión que te toca a ti

Este plan asume que la Tarea 2 es legítima según la [Frontera de integridad](#frontera-de-integridad): corregir un oráculo defectuoso no es relajar un gate, y el arnés de la Tarea 1 lo demuestra en cada corrida. Si no compartes ese razonamiento, el plan se detiene en la Tarea 1 — el arnés por sí solo ya documenta los falsos positivos con precisión de fixture — y el resto no se ejecuta, porque un bucle de auto-verificación construido sobre un oráculo que rechaza artefactos correctos amplifica el daño en vez de reducirlo.

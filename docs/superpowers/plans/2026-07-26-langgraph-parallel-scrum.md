# Paralelismo nativo LangGraph y supervisor scrum — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximizar la delegación paralela segura del sprint SDD y añadir un supervisor scrum que prioriza (nunca decide seguridad), manteniendo intactos G7, la propiedad de paths y las reglas de honestidad.

**Architecture:** Plano de control en Python + git + LangGraph. El *sprint* ya despacha tareas con `Send` en worktrees aislados. Este plan (1) permite que defectos disjuntos entren en la misma ola, (2) sube la anchura de ola, (3) inserta un ordenador `scrum.prioritize` determinista con gancho LLM opcional, y (4) añade permisos del harness sin tocar la integridad.

**Tech Stack:** Python 3.12, `unittest`, LangGraph (`Send`, `SqliteSaver`), git worktrees, `tomllib`.

## Global Constraints

- **NO modificar** `gates/*.py`, `gates/registry.toml`, ni las secciones `[budget]`/`[gates]` de `pipeline.toml`. Solo se toca `[runtime].max_concurrency`. (CLAUDE.md prohibición #1)
- **Propiedad de paths por nodo intacta**: G7 sigue revirtiendo escrituras fuera de path. Ningún cambio relaja esto. (CLAUDE.md prohibiciones #1–#3)
- El scrum LLM **solo ordena**: es una permutación de la lista `ready`; no agrega, quita ni cambia dependencias ni la seguridad de solapamiento.
- Todo cambio mantiene verde `python -m sdd test` y `python -m sdd demo` → `done | tareas: 5/5`.
- Tipado explícito en símbolos exportados; sin `print` de depuración en código de producción (usar el `log_fn` inyectado). Objetivo ≤300 líneas/archivo.
- Los tests son `unittest`; se corren con `python -m unittest discover -s tests`.
- Rutas de import de test: `sys.path.insert(0, str(ROOT))` donde `ROOT = .../sdd`.

---

## File Structure

| Archivo | Responsabilidad | Acción |
|---------|-----------------|--------|
| `sdd/task_worktrees.py` | Aislamiento git + selección segura. `safe_batch` deja de aislar defectos. | Modificar |
| `sdd/scrum.py` | Ordenar `ready` por prioridad (determinista + gancho LLM con fallback). | Crear |
| `sdd/parallel_tasks.py` | `schedule` llama a `scrum.prioritize` y usa `slots` para ola ancha. | Modificar |
| `sdd/pipeline.toml` | `[runtime].max_concurrency = 6`. | Modificar (solo runtime) |
| `.claude/settings.json` | Permisos del harness (P1), documentando que G7 no se toca. | Crear |
| `tests/test_langgraph_runtime.py` | Casos de defectos en lote y ola ancha en `TestSafeBatch`. | Modificar |
| `tests/test_scrum.py` | Pruebas del ordenador scrum. | Crear |
| `tests/test_harness_permissions.py` | `settings.json` parsea y no altera propiedad. | Crear |
| `FLUJO.md`, `HANDOFF.md` | Reflejar defectos-en-lote, ola ancha y scrum. | Modificar |

---

## Task 1: Defectos disjuntos entran en la misma ola (P2)

**Files:**
- Modify: `sdd/task_worktrees.py:49-65` (`safe_batch`)
- Test: `tests/test_langgraph_runtime.py` (clase `TestSafeBatch`, ~línea 53)

**Interfaces:**
- Consumes: `_footprint(task, nodes) -> list[str]`, `_overlaps(left, right) -> bool` (existentes, sin cambios).
- Produces: `safe_batch(ready: list[dict], nodes: dict, limit: int) -> list[dict]` — mismo contrato, pero SIN el caso especial "defecto corre solo".

- [ ] **Step 1: Escribir las pruebas que fallan**

En `tests/test_langgraph_runtime.py`, dentro de `class TestSafeBatch`, añadir:

```python
    def test_incluye_defectos_no_solapados(self):
        tasks = [
            {"id": "D-001", "node": "dev_backend", "kind": "defect",
             "deliverables": ["src/domain/x.py"]},
            {"id": "T-5", "node": "dev_frontend", "kind": "plan",
             "deliverables": ["src/web/y.js"]},
        ]
        selected = task_worktrees.safe_batch(tasks, {}, 6)
        self.assertEqual({task["id"] for task in selected}, {"D-001", "T-5"})

    def test_defecto_solapado_no_entra_dos_veces(self):
        tasks = [
            {"id": "D-001", "node": "dev_backend", "kind": "defect",
             "deliverables": ["src/domain/x.py"]},
            {"id": "T-5", "node": "dev_backend", "kind": "plan",
             "deliverables": ["src/domain/x.py"]},
        ]
        selected = task_worktrees.safe_batch(tasks, {}, 6)
        self.assertEqual([task["id"] for task in selected], ["D-001"])

    def test_ola_ancha_selecciona_tres_disjuntas(self):
        tasks = [
            {"id": "T-1", "node": "a", "kind": "plan", "deliverables": ["src/api/a.py"]},
            {"id": "T-2", "node": "b", "kind": "plan", "deliverables": ["src/web/b.js"]},
            {"id": "T-3", "node": "c", "kind": "plan", "deliverables": ["src/domain/c.py"]},
        ]
        selected = task_worktrees.safe_batch(tasks, {}, 6)
        self.assertEqual([task["id"] for task in selected], ["T-1", "T-2", "T-3"])
```

- [ ] **Step 2: Correr para verificar que fallan**

Run: `python -m unittest tests.test_langgraph_runtime.TestSafeBatch -v`
Expected: FAIL — `test_incluye_defectos_no_solapados` obtiene `{"D-001"}` porque el código actual devuelve `[ready[0]]` cuando el primero es defecto.

- [ ] **Step 3: Quitar el caso especial "defecto corre solo"**

En `sdd/task_worktrees.py`, reemplazar la función `safe_batch` completa por:

```python
def safe_batch(ready: list[dict[str, object]],
               nodes: dict[str, dict[str, object]], limit: int) -> list[dict[str, object]]:
    """Elige el conjunto independiente maximal (en orden de `ready`) de tareas
    cuyas huellas de entregables son dos-a-dos disjuntas, hasta `limit`.

    `ready` ya viene priorizado (defectos primero; el scrum afina el resto). A
    diferencia de antes, un defecto NO corre solo: si su huella no solapa otra
    tarea lista, entra en la misma ola. La seguridad la da la disjuncion de
    huellas, no el tipo de tarea."""
    selected: list[dict[str, object]] = []
    footprints: list[list[str]] = []
    for task in ready:
        current = _footprint(task, nodes)
        if all(not _overlaps(current, existing) for existing in footprints):
            selected.append(task)
            footprints.append(current)
        if len(selected) >= max(1, limit):
            break
    return selected or (ready[:1] if ready else [])
```

- [ ] **Step 4: Correr para verificar que pasan (incluida la prueba previa)**

Run: `python -m unittest tests.test_langgraph_runtime.TestSafeBatch -v`
Expected: PASS en las 4 (la existente `test_solo_agrupa_huellas_no_superpuestas` sigue verde: T-1 y T-2 disjuntas, T-3 solapa T-1).

- [ ] **Step 5: Commit**

```bash
git add sdd/task_worktrees.py tests/test_langgraph_runtime.py
git commit -m "feat(sprint): defectos disjuntos entran en la misma ola (P2)"
```

---

## Task 2: Módulo scrum — ordenador determinista con gancho LLM (P4)

**Files:**
- Create: `sdd/scrum.py`
- Test: `tests/test_scrum.py`

**Interfaces:**
- Produces:
  - `read_critical_frs(workdir: str | Path) -> set[str]` — FR marcados `@critical` en los `.feature`.
  - `prioritize(ready: list[dict], *, critical_frs: set[str], slots: int, simulate: bool = True, complete_fn=None, log_fn=None) -> list[dict]` — permutación de `ready` por prioridad. Determinista si `len(ready) <= slots`, en `simulate`, o sin `complete_fn`; con modelo, degrada al orden determinista ante respuesta ilegible.

- [ ] **Step 1: Escribir las pruebas que fallan**

Crear `tests/test_scrum.py`:

```python
"""Pruebas del supervisor scrum: solo ordena, nunca decide seguridad."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

import scrum  # noqa: E402


def _ready():
    return [
        {"id": "T-2", "node": "dev_frontend", "kind": "plan", "fr_refs": ["FR-002"]},
        {"id": "T-1", "node": "dev_backend", "kind": "plan", "fr_refs": ["FR-001"]},
        {"id": "D-001", "node": "dev_backend", "kind": "defect", "fr_refs": ["FR-001"]},
    ]


class TestPrioritize(unittest.TestCase):
    def test_no_llama_al_modelo_si_caben_todas(self):
        def boom(_system, _user):
            raise AssertionError("no debe llamar al modelo si |ready| <= slots")
        order = scrum.prioritize(_ready(), critical_frs=set(), slots=6,
                                 simulate=False, complete_fn=boom)
        self.assertEqual([t["id"] for t in order], ["D-001", "T-1", "T-2"])

    def test_orden_determinista_defectos_y_criticos_primero(self):
        order = scrum.prioritize(_ready(), critical_frs={"FR-002"}, slots=1)
        # defecto primero; luego la tarea critica (FR-002); luego el resto por id.
        self.assertEqual([t["id"] for t in order], ["D-001", "T-2", "T-1"])

    def test_degrada_a_determinista_si_modelo_ilegible(self):
        def ilegible(_system, _user):
            return "no es json ni tiene marcadores"
        order = scrum.prioritize(_ready(), critical_frs=set(), slots=1,
                                 simulate=False, complete_fn=ilegible)
        self.assertEqual([t["id"] for t in order], ["D-001", "T-1", "T-2"])

    def test_respeta_un_orden_valido_del_modelo(self):
        def modelo(_system, _user):
            return '<<<ORDER>>>["T-1", "D-001", "T-2"]<<<END>>>'
        order = scrum.prioritize(_ready(), critical_frs=set(), slots=1,
                                 simulate=False, complete_fn=modelo)
        self.assertEqual([t["id"] for t in order], ["T-1", "D-001", "T-2"])

    def test_permutacion_invalida_del_modelo_se_ignora(self):
        def modelo(_system, _user):
            return '<<<ORDER>>>["T-1", "T-9"]<<<END>>>'  # falta D-001, sobra T-9
        order = scrum.prioritize(_ready(), critical_frs=set(), slots=1,
                                 simulate=False, complete_fn=modelo)
        self.assertEqual([t["id"] for t in order], ["D-001", "T-1", "T-2"])


class TestCriticalFrs(unittest.TestCase):
    def test_lee_fr_criticos_de_los_feature(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            feat = Path(tmp) / "spec/10_product/features"
            feat.mkdir(parents=True)
            (feat / "x.feature").write_text(
                "Caracteristica: x\n\n  @FR-001 @SCN-001 @critical @p1\n"
                "  Escenario: ok\n    Dado a\n    Cuando b\n    Entonces c\n"
                "\n  @FR-002 @SCN-002 @p2\n  Escenario: no critico\n"
                "    Dado a\n    Cuando b\n    Entonces c\n", encoding="utf-8")
            self.assertEqual(scrum.read_critical_frs(tmp), {"FR-001"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correr para verificar que fallan**

Run: `python -m unittest tests.test_scrum -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'scrum'`.

- [ ] **Step 3: Escribir `sdd/scrum.py`**

```python
"""Supervisor scrum: prioriza el orden de ejecucion de las tareas LISTAS.

Solo ORDENA. No decide dependencias ni que archivos toca cada tarea (eso ya lo
resolvio el planner), y no decide la seguridad de solapamiento (eso lo hace
task_worktrees.safe_batch, determinista). Su salida es una PERMUTACION de la
lista `ready`: ni agrega ni quita tareas.

En modo simulado, si caben todas las tareas en la ola, o sin modelo disponible,
el orden es determinista: defectos (desbloquean) -> @critical -> id. Con modelo,
si la respuesta es ilegible o no es una permutacion valida, degrada al orden
determinista y lo registra. El scrum nunca tumba la corrida.
"""
import json
import re
from pathlib import Path
from typing import Callable, Optional

CRITICAL_TAG = "@critical"
_FR = re.compile(r"(FR-\d+)")
_ORDER = re.compile(r"<<<ORDER>>>\s*(.*?)\s*<<<END>>>", re.S)

CompleteFn = Callable[[str, str], str]
LogFn = Callable[..., None]


def read_critical_frs(workdir: "str | Path") -> set[str]:
    """FR marcados @critical en spec/10_product/features/**. Heuristica: un FR es
    critico si aparece en una linea de tags que tambien contiene @critical."""
    root = Path(workdir) / "spec/10_product/features"
    critical: set[str] = set()
    if not root.exists():
        return critical
    for path in root.rglob("*.feature"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if CRITICAL_TAG in line:
                critical.update(_FR.findall(line))
    return critical


def _deterministic_order(ready: list[dict], critical: set[str]) -> list[dict]:
    def key(task: dict) -> tuple[int, int, str]:
        is_defect = 0 if task.get("kind") == "defect" else 1
        is_critical = 0 if (set(task.get("fr_refs") or []) & critical) else 1
        return (is_defect, is_critical, str(task.get("id")))
    return sorted(ready, key=key)


def _valid_permutation(order: object, base: list[dict]) -> bool:
    if not isinstance(order, list):
        return False
    return sorted(str(x) for x in order) == sorted(str(t["id"]) for t in base)


def _ask_model(base: list[dict], critical: set[str],
               complete_fn: CompleteFn) -> object:
    listado = "\n".join(
        f"- {t['id']} node={t.get('node')} kind={t.get('kind')} "
        f"fr={','.join(t.get('fr_refs') or []) or '-'} "
        f"critico={'si' if set(t.get('fr_refs') or []) & critical else 'no'}"
        for t in base)
    system = (
        "Eres un scrum master. Ordenas tareas LISTAS por prioridad de ejecucion. "
        "NO decides dependencias ni que archivos tocan; eso ya esta resuelto. "
        "Prioriza los defectos que desbloquean, luego lo @critical, luego lo que "
        "mas trabajo desbloquea. Responde EXCLUSIVAMENTE con una linea "
        "<<<ORDER>>> seguida de un array JSON con TODOS los ids en orden y "
        "<<<END>>>. Sin prosa, sin ```.")
    user = f"TAREAS LISTAS:\n{listado}"
    text = complete_fn(system, user) or ""
    match = _ORDER.search(text)
    if not match:
        return None
    return json.loads(match.group(1))


def prioritize(ready: list[dict], *, critical_frs: set[str], slots: int,
               simulate: bool = True, complete_fn: Optional[CompleteFn] = None,
               log_fn: Optional[LogFn] = None) -> list[dict]:
    """Devuelve `ready` reordenado por prioridad (permutacion). Ver docstring
    del modulo para el contrato completo."""
    base = _deterministic_order(ready, critical_frs)
    if len(ready) <= max(1, slots) or simulate or complete_fn is None:
        return base
    order: object = None
    try:
        order = _ask_model(base, critical_frs, complete_fn)
    except Exception:  # noqa: BLE001 — el scrum nunca tumba la corrida
        order = None
    if not _valid_permutation(order, base):
        if log_fn is not None:
            log_fn("SCRUM", modo="degradado",
                   motivo="respuesta del modelo ilegible o no permutacion")
        return base
    rank = {str(tid): i for i, tid in enumerate(order)}  # type: ignore[arg-type]
    return sorted(base, key=lambda t: rank[str(t["id"])])
```

- [ ] **Step 4: Correr para verificar que pasan**

Run: `python -m unittest tests.test_scrum -v`
Expected: PASS en las 6.

- [ ] **Step 5: Commit**

```bash
git add sdd/scrum.py tests/test_scrum.py
git commit -m "feat(scrum): ordenador de prioridad determinista con gancho LLM (P4)"
```

---

## Task 3: Cablear scrum en schedule y ampliar la ola (P3 + P4)

**Files:**
- Modify: `sdd/parallel_tasks.py:1-8` (imports), `:26-79` (`schedule`)
- Modify: `sdd/pipeline.toml:17` (`max_concurrency = 6`)
- Test: se cubre con el demo end-to-end (Task 5) y los unit tests de Task 1/2.

**Interfaces:**
- Consumes: `scrum.prioritize(...)`, `scrum.read_critical_frs(...)` (Task 2); `task_worktrees.safe_batch(...)` (Task 1); `taskqueue.runnable(...)` (existente).
- Produces: `schedule(value) -> dict` — misma firma; ahora prioriza con scrum antes de `safe_batch` y usa `slots` como anchura de ola.

- [ ] **Step 1: Subir la anchura de ola por defecto (solo `[runtime]`)**

En `sdd/pipeline.toml`, en la sección `[runtime]`, cambiar:

```toml
max_concurrency = 3
```

por:

```toml
# Anchura de ola: cuantas tareas de huellas disjuntas se despachan por superstep.
# Subida de 3 a 6 para paralelizar olas anchas; bajable a 1 sin cambiar semantica.
max_concurrency = 6
```

> No tocar `[budget]` ni `[gates]`. Solo esta línea de `[runtime]`.

- [ ] **Step 2: Añadir imports y el gancho de modelo en `parallel_tasks.py`**

Al inicio de `sdd/parallel_tasks.py`, reemplazar el bloque de imports:

```python
"""Nodos LangGraph del sprint aislado y paralelo."""
import copy

from langgraph.types import Send

import task_worktrees
import taskqueue
```

por:

```python
"""Nodos LangGraph del sprint aislado y paralelo."""
import copy
import os

from langgraph.types import Send

import scrum
import task_worktrees
import taskqueue


def _scrum_complete(system: str, user: str) -> str:
    """Puente perezoso al proveedor LLM para el scrum en modo real. Se importa
    aqui dentro para no arrastrar el SDK cuando se corre en simulado."""
    import providers
    return providers.complete(system, user)
```

- [ ] **Step 3: Insertar la priorización scrum en `schedule`**

En `sdd/parallel_tasks.py`, dentro de `schedule`, localizar:

```python
        ready = taskqueue.runnable(current["tasks"])
        if not ready:
```

y, tras el bloque `if not ready: ... return current`, localizar:

```python
        limit = int(self.cfg["runtime"].get("max_concurrency", 3))
        batch = task_worktrees.safe_batch(ready, self.nodes, limit)
```

Reemplazar esas dos líneas por:

```python
        slots = int(self.cfg["runtime"].get("max_concurrency", 3))
        simulate = bool(os.environ.get("SDD_SIMULATE"))
        ready = scrum.prioritize(
            ready, critical_frs=scrum.read_critical_frs(self.workdir),
            slots=slots, simulate=simulate,
            complete_fn=None if simulate else _scrum_complete,
            log_fn=lambda event, **kw: self.log_fn(current, event, **kw))
        batch = task_worktrees.safe_batch(ready, self.nodes, slots)
```

- [ ] **Step 4: Correr el path paralelo y el demo para verificar que no hay regresión**

Run: `python -m unittest tests.test_langgraph_runtime.TestDurableHumanGate -v`
Expected: PASS — `test_send_workers_ejecutan_batch_paralelo_y_limpian_worktrees` sigue viendo una ola con `tareas == 2` (con `SDD_FAKE_PARALLEL` solo T-002 y T-003 están listas a la vez; el orden scrum no cambia que sean 2 disjuntas).

Run: `python -m sdd demo`
Expected: `estado final: done | ... | tareas: 5/5`.

- [ ] **Step 5: Commit**

```bash
git add sdd/parallel_tasks.py sdd/pipeline.toml
git commit -m "feat(sprint): scrum prioriza la ola y max_concurrency=6 (P3+P4)"
```

---

## Task 4: Permisos del harness sin tocar G7 (P1)

**Files:**
- Create: `.claude/settings.json`
- Test: `tests/test_harness_permissions.py`

**Interfaces:**
- Produces: `.claude/settings.json` válido con `permissions.allow` para operar el pipeline; documenta que la propiedad de paths no vive aquí.

- [ ] **Step 1: Verificar el esquema real contra la documentación oficial**

CLAUDE.md exige verificar el esquema citando URLs oficiales (no de memoria). Antes de escribir el archivo, consultar la documentación oficial de Claude Code sobre `settings.json` y `permissions` (usar el agente `claude-code-guide` o `WebFetch` sobre `https://docs.claude.com/en/docs/claude-code/settings` y la página de `permissions`/`iam`). Confirmar los nombres de campo exactos: `permissions`, `allow`, `deny`, y el formato de regla `Tool(patrón)` (p. ej. `Bash(git:*)`). Anotar las URLs consultadas en un comentario del test.

Expected: se confirman los campos y el formato de regla; si difieren de lo asumido abajo, ajustar el JSON del Step 3 a lo que digan las URLs.

- [ ] **Step 2: Escribir la prueba que falla**

Crear `tests/test_harness_permissions.py`:

```python
"""El settings.json del harness concede permisos operativos SIN tocar la
propiedad de paths por nodo (eso vive en pipeline.toml + G7).

Esquema verificado contra la doc oficial de Claude Code:
  https://docs.claude.com/en/docs/claude-code/settings
  (campo `permissions.allow`, reglas `Tool(patron)`).
"""
import json
import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = REPO / ".claude/settings.json"
PIPELINE = REPO / "sdd/pipeline.toml"


class TestHarnessPermissions(unittest.TestCase):
    def test_settings_parsea_y_tiene_allow(self):
        self.assertTrue(SETTINGS.exists(), "falta .claude/settings.json")
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.assertIn("permissions", data)
        self.assertIsInstance(data["permissions"].get("allow"), list)
        self.assertTrue(data["permissions"]["allow"], "allow no puede estar vacio")

    def test_no_declara_propiedad_de_paths(self):
        # La propiedad de paths es de pipeline.toml; settings.json NO debe
        # redefinir writes/gates ni prometer que un nodo escribe fuera de lo suyo.
        raw = SETTINGS.read_text(encoding="utf-8")
        for prohibido in ("\"writes\"", "\"gates\"", "\"node\""):
            self.assertNotIn(prohibido, raw,
                             "settings.json no gobierna la propiedad de paths")

    def test_pipeline_conserva_la_propiedad_por_nodo(self):
        cfg = tomllib.loads(PIPELINE.read_text(encoding="utf-8"))
        writes = {n["id"]: n.get("writes", []) for n in cfg["node"]}
        self.assertEqual(writes["qa"], ["tests/", "spec/40_qa/"])
        self.assertIn("src/api/", writes["dev_backend"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Correr para verificar que falla**

Run: `python -m unittest tests.test_harness_permissions -v`
Expected: FAIL — `test_settings_parsea_y_tiene_allow` porque `.claude/settings.json` no existe.

- [ ] **Step 4: Escribir `.claude/settings.json`**

Con el esquema confirmado en el Step 1, crear `.claude/settings.json`:

```json
{
  "_comment": "Permisos operativos del plano de control SDD. La propiedad de paths por nodo (quien escribe donde) NO vive aqui: vive en sdd/pipeline.toml y la verifica el gate G7. Este archivo solo evita friccion de permisos al operar el pipeline bajo Claude Code. Esquema: https://docs.claude.com/en/docs/claude-code/settings",
  "permissions": {
    "allow": [
      "Bash(git:*)",
      "Bash(python:*)",
      "Bash(python -m sdd:*)",
      "Bash(python -m unittest:*)",
      "Read(//**)",
      "Write(//**)",
      "Edit(//**)"
    ],
    "deny": []
  }
}
```

> Si la verificación del Step 1 indicó nombres/patrones distintos (p. ej. otra sintaxis de regla), usar los de la doc oficial, no estos.

- [ ] **Step 5: Correr para verificar que pasa**

Run: `python -m unittest tests.test_harness_permissions -v`
Expected: PASS en las 3.

- [ ] **Step 6: Commit**

```bash
git add .claude/settings.json tests/test_harness_permissions.py
git commit -m "chore(harness): permisos operativos sin tocar la propiedad de paths (P1)"
```

---

## Task 5: Documentar el nuevo comportamiento y verificación final

**Files:**
- Modify: `FLUJO.md` (sección 1 y "Límites de este flujo")
- Modify: `HANDOFF.md` (sección "Arquitectura en dos fases")

**Interfaces:** ninguna nueva; solo prosa que refleja el código ya mergeado.

- [ ] **Step 1: Actualizar `HANDOFF.md`**

En `HANDOFF.md`, en el punto 2 de "Arquitectura en dos fases", reemplazar:

```
2. **Sprint durable** — LangGraph despacha tareas listas con `Send`; las huellas
   no superpuestas corren en worktrees paralelos y se integran en orden. Un
   defecto de otro nodo se vuelve una tarea `D-###` para su dueño.
```

por:

```
2. **Sprint durable** — LangGraph despacha en cada superstep la ola independiente
   maxima de tareas listas (plan y defectos) con huellas de archivo disjuntas, en
   worktrees paralelos, integradas en orden. Un supervisor `scrum` ordena la ola
   por prioridad (defectos que desbloquean, luego @critical); solo ordena, nunca
   decide la seguridad de solapamiento. Un defecto de otro nodo se vuelve una
   tarea `D-###` para su dueño.
```

- [ ] **Step 2: Actualizar `FLUJO.md`**

En `FLUJO.md`, en "Límites de este flujo", añadir al final de la lista:

```
- **La ola paralela mantiene la barrera BSP de LangGraph.** Cada superstep
  espera a que termine su ola antes de planificar la siguiente; el coste es la
  tarea mas lenta de una ola tan ancha como la disjuncion de huellas permita
  (hasta `max_concurrency`). Un pool sin barrera queda fuera de alcance.
- **El scrum ordena, no decide seguridad.** En simulado y ante fallo del modelo
  el orden es determinista (defectos -> @critical -> id); la disjuncion de
  huellas la decide siempre `safe_batch`, codigo determinista.
```

En el subgrafo BUCLE del primer diagrama Mermaid (sección 1), cambiar la etiqueta
del nodo `SEND` de:

```
    SEND["<b>Send workers</b><br/>solo huellas no superpuestas"]
```

a:

```
    SEND["<b>Send workers</b><br/>ola maxima de huellas disjuntas<br/>priorizada por scrum"]
```

- [ ] **Step 3: Verificación final completa**

Run: `python -m sdd test`
Expected: OK — batería completa verde, incluidos `test_scrum`, `test_harness_permissions` y `TestSafeBatch`.

Run: `python -m sdd demo`
Expected: `estado final: done | ... | tareas: 5/5`, con `[GATE G9 ] estado=pass` y `DEFECTO_TAREA` en el log.

- [ ] **Step 4: Commit**

```bash
git add FLUJO.md HANDOFF.md
git commit -m "docs: reflejar ola ancha, defectos concurrentes y supervisor scrum"
```

---

## Self-Review (cobertura del spec)

| Requisito del spec | Task que lo implementa |
|--------------------|------------------------|
| R1 — permisos del harness, G7 intacto (P1) | Task 4 |
| R2 — máximo de tareas disjuntas en paralelo (P2) | Task 1 (defectos en lote) + `safe_batch` maximal existente |
| R3 — supervisor scrum que solo prioriza (P4) | Task 2 (módulo) + Task 3 (cableado) |
| R4 — LangGraph nativo, olas anchas (P3) | Task 3 (`slots`, `max_concurrency=6`) |
| Aceptación: `sdd test` + `sdd demo` verdes | Task 5 |
| Invariante: G7/propiedad intactos | Task 4 (tests) + Global Constraints |
| Fuera de alcance: P5, pool sin barrera | documentado en spec §7 y FLUJO.md |

**Placeholder scan:** sin TBD/TODO; cada step muestra el código real.
**Type consistency:** `prioritize(...)` y `read_critical_frs(...)` se definen en Task 2 con las mismas firmas que consume Task 3; `safe_batch(ready, nodes, limit)` conserva su firma en Task 1.

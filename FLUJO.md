# Cómo funciona el pipeline hoy

Documento de referencia del comportamiento **actual** del sistema (no del deseado).
Todo lo que aparece aquí está implementado y cubierto por `python -m sdd test`.

---

## 1. Vista general: dos fases

La fase lineal produce la especificación y el plan. El bucle de tareas ejecuta ese
plan. Entre ambas, una firma humana.

```mermaid
flowchart TD
    idea["spec/00_intake.yaml<br/>la idea, única fuente de verdad"]

    subgraph LINEAL ["Fase lineal · un turno por rol + revisión crítica"]
        direction TB
        P["<b>product</b><br/>spec/10_product/<br/>G0 · G1 · <i>R1</i>"]
        A["<b>architect</b><br/>spec/20_arch/<br/>G0 · G2 · <i>R1</i>"]
        PL["<b>planner</b><br/>spec/30_plan/tasks.yaml<br/>G0 · G10 · <i>R1</i>"]
        P --> A --> PL
    end

    H{{"<b>gate humano</b><br/>firma spec Y plan<br/>antes de escribir código"}}

    subgraph BUCLE ["Bucle de tareas · un turno por tarea"]
        direction TB
        SEL{"¿hay tarea ejecutable?<br/>dependencias cerradas"}
        EXE["nodo dueño de la tarea<br/><b>dev_backend</b> · <b>dev_frontend</b> · <b>qa</b>"]
        SEL -->|sí| EXE
        EXE -->|"tarea cerrada"| SEL
    end

    OK(["<b>done</b><br/>todas las tareas cerradas"])
    ESC(["<b>escalated</b><br/>quedan tareas pero<br/>ninguna es ejecutable"])

    idea --> P
    PL --> H --> SEL
    SEL -->|"no queda ninguna"| OK
    SEL -->|"quedan bloqueadas"| ESC

    style H fill:#fde68a,stroke:#b45309,color:#000
    style OK fill:#bbf7d0,stroke:#15803d,color:#000
    style ESC fill:#fecaca,stroke:#b91c1c,color:#000
```

En modo `--autonomous` (el del panel web) el gate humano se auto-aprueba, pero solo
se alcanza tras `product`, `architect` y `planner` en verde: nunca firma sobre un
plan inválido.

---

## 2. El ciclo de un nodo: dónde se decide todo

Este es el corazón del sistema. Cada visita a un nodo pasa por aquí, tanto en la
fase lineal como dentro del bucle.

```mermaid
flowchart TD
    START(["visita al nodo"]) --> PUB["publica la tarea en<br/>.agent/current_task.json"]
    PUB --> BASE["congela la línea base de git<br/>.agent/baseline.txt"]
    BASE --> INV["invoca al agente<br/>lee spec + tarea, escribe archivos"]

    INV --> RC{"código de salida"}
    RC -->|"3 · BLOCKED<br/>le falta un insumo"| DEF["defecto del propio nodo"]
    RC -->|"1 · agente caído<br/>IncompleteRead, sin bloques…"| DEF
    RC -->|"0"| GATES["corre los gates del nodo"]

    GATES --> RED{"¿algún gate rojo?"}
    RED -->|no| COMMIT["commit acotado a los paths del nodo<br/>Conventional Commits + FR-### + task_id"]
    COMMIT --> CLOSE["tarea = done<br/>libera a quien la esperaba"]
    CLOSE --> LOOP(["vuelve al bucle"])

    RED -->|sí| ENV{"¿es fallo de entorno?<br/>binario ausente, sin red,<br/>suite colgada"}
    ENV -->|sí| ESC(["ESCALATE_HUMAN<br/>ningún agente lo arregla<br/>escribiendo código"])
    ENV -->|no| G7{"¿violación de propiedad?<br/>gate G7"}
    G7 -->|sí| REV["git checkout + clean<br/>revierte lo escrito fuera"]
    REV --> OWNER
    G7 -->|no| OWNER{"¿de quién es el defecto?"}

    OWNER -->|"de este nodo"| DEF
    OWNER -->|"de otro nodo"| DELEG["crea tarea <b>D-###</b> para su dueño<br/>la tarea actual queda <i>blocked</i>"]
    DELEG --> LOOP

    DEF --> RETRY{"¿reintentos agotados?<br/>max_retries_per_gate"}
    RETRY -->|no| INV
    RETRY -->|sí| ESC

    style ESC fill:#fecaca,stroke:#b91c1c,color:#000
    style COMMIT fill:#bbf7d0,stroke:#15803d,color:#000
    style DELEG fill:#fde68a,stroke:#b45309,color:#000
    style REV fill:#fed7aa,stroke:#c2410c,color:#000
```

**Las tres decisiones que antes no existían** y que sostienen todo lo demás:

- un agente con `exit != 0` **no avanza** — es un defecto del nodo, no un éxito;
- un defecto ajeno **no es un reintento a ciegas** — se convierte en trabajo `D-###`
  asignado a su dueño;
- un fallo de entorno **escala de inmediato** en vez de quemar reintentos.

---

## 2 bis. R1: el revisor crítico

`R1` **no es un gate G\***. Los `G*` son código determinista sin juicio; R1 es un
modelo leyendo el trabajo de otro modelo. Verifica lo que la forma no alcanza:
un PRD puede pasar G1 entero y seguir siendo inservible.

Por eso está acotado por diseño — puede **añadir** defectos, nunca relajar un `G*`.

```mermaid
flowchart TD
    N["nodo de especificación<br/>product · architect · planner"] --> DET["gates deterministas<br/>G0 · G1/G2/G10"]
    DET --> Q{"¿todos verdes?"}
    Q -->|no| SKIP["<b>R1 no se ejecuta</b><br/>criticar un artefacto ya rojo<br/>es tirar una llamada al modelo"]
    SKIP --> FIX["el nodo reescribe"]
    Q -->|sí| TOPE{"¿quedan rondas<br/>de revisión?"}
    TOPE -->|"no · tope agotado"| PASA["pasa DEJANDO CONSTANCIA<br/>en el reporte final"]
    TOPE -->|sí| R1["<b>R1</b> lee el artefacto<br/>+ sus insumos aguas arriba"]

    R1 --> ERR{"¿respondió algo<br/>utilizable?"}
    ERR -->|"no · caído o ilegible"| DEGR["pasa y avisa<br/>los G* siguen sosteniendo<br/>la corrección"]
    ERR -->|sí| SEV{"severidad de<br/>cada hallazgo"}

    SEV -->|"<b>blocking</b>"| BLOCK["defecto al nodo que lo escribió<br/>consume una ronda · reintenta"]
    SEV -->|"<b>mejora</b>"| BACK["backlog en el reporte final<br/>NO frena el pipeline"]
    SEV -->|"sin hallazgos"| OK["verde · no consume ronda"]

    BLOCK --> FIX
    BACK --> OK

    style SKIP fill:#e0e7ff,stroke:#4338ca,color:#000
    style BLOCK fill:#fde68a,stroke:#b45309,color:#000
    style BACK fill:#e0e7ff,stroke:#4338ca,color:#000
    style OK fill:#bbf7d0,stroke:#15803d,color:#000
    style DEGR fill:#fed7aa,stroke:#c2410c,color:#000
    style PASA fill:#fed7aa,stroke:#c2410c,color:#000
```

**Las cuatro cotas que impiden el pulido infinito:**

| Cota | Por qué |
|---|---|
| Corre el último, y solo si los `G*` están verdes | no se gastan tokens criticando algo que ya se va a reescribir |
| Solo `blocking` frena | un revisor siempre encuentra algo mejorable; si todo bloqueara, no converge |
| Tope de rondas por nodo (2 por defecto) | sobre un artefacto subjetivo como un PRD no hay punto fijo garantizado |
| Solo la ronda que **bloquea** consume presupuesto | una revisión limpia no debe castigar al nodo |

Si el revisor se cae o responde algo ilegible, el gate **pasa y lo registra**. Es
deliberado: los deterministas siguen sosteniendo la corrección, y tumbar la corrida
porque el crítico se cayó cuesta más de lo que protege. Lo que no hace es callárselo.

El revisor puede correr en otro modelo (`SDD_REVIEW_MODEL`): un crítico que es
literalmente el mismo modelo tiende a validar su propio criterio.

---

## 3. Quién puede escribir dónde, y quién lo verifica

La propiedad se declara en `pipeline.toml` y la verifica G7 a posteriori con
`git status`. Escribir fuera = revert automático.

```mermaid
flowchart LR
    subgraph NODOS ["nodo"]
        direction TB
        n1["product"]
        n2["architect"]
        n3["planner"]
        n4["dev_backend"]
        n5["dev_frontend"]
        n6["qa"]
    end

    subgraph PATHS ["paths que posee"]
        direction TB
        p1["spec/10_product/"]
        p2["spec/20_arch/"]
        p3["spec/30_plan/"]
        p4["src/api/ · src/domain/ · src/infra/<br/>migrations/ · .env.example"]
        p5["src/web/ · .env.example"]
        p6["tests/ · spec/40_qa/"]
    end

    n1 --> p1
    n2 --> p2
    n3 --> p3
    n4 --> p4
    n5 --> p5
    n6 --> p6
```

### Gates por nodo

| Nodo | Gates | Qué exigen |
|---|---|---|
| `product` | **G0** · G1 · *R1* | entregables presentes · todo FR con escenario Gherkin · *criterio: alcance, requisitos falsables, casos negativos* |
| `architect` | **G0** · G2 · *R1* | entregables presentes · NFR medibles, ADR con alternativas y coste, `toolchain.yaml` · *criterio: sobrearquitectura, umbrales alcanzables* |
| `planner` | **G0** · G10 · *R1* | entregables presentes · plan sin ciclos, todo FR con tarea, entregables dentro de propiedad · *criterio: tamaño de tarea, dependencias reales* |
| `dev_backend` | G7 · **G0** · G4 · G5 · **G6** | propiedad · entregables · tamaño · sin secretos ni valores de entorno · imports resueltos |
| `dev_frontend` | G7 · **G0** · G4 · G5 · **G6** | idem |
| `qa` | G7 · **G0** · **G6** · G8 · **G9** | propiedad · entregables · imports · cobertura de `@critical` · **la suite se ejecuta y pasa** |

En negrita, los gates nuevos; en cursiva, la revisión. **G9 es el único que
ejecuta**: corre `install`/`typecheck`/`test` según `spec/20_arch/toolchain.yaml`.
Mientras ningún gate ejecute, el verde del pipeline no significa nada.

Dos categorías, y la distinción importa: `G*` es código determinista y su veredicto
es incuestionable; `R1` es juicio, y por eso está acotado y solo puede añadir.

---

## 4. Caso real: cómo se comporta ante un defecto ajeno

Esto es exactamente lo que hace `python -m sdd demo` (0 tokens). QA descubre un bug
de dominio que ningún linter puede ver, y el sistema lo lleva a su dueño.

```mermaid
sequenceDiagram
    autonumber
    participant O as Orquestador
    participant Q as qa
    participant G as Gates
    participant B as dev_backend
    participant R as repo git

    O->>Q: T-004 · suite de dominio y contrato
    Q->>R: escribe tests/ … y parchea src/domain/ 🚫
    O->>G: G7 propiedad
    G-->>O: rojo · qa escribió fuera de tests/
    O->>R: revert de lo escrito fuera
    O->>Q: reintenta T-004

    Q->>R: escribe solo tests/ y spec/40_qa/
    O->>G: G7 · G0 · G6 · G8 · G9
    G-->>O: G9 ROJO · KeyError en src/domain/matricula.py
    Note over O: el traceback delata al dominio,<br/>no a la prueba
    O->>O: abre D-001 para dev_backend<br/>T-004 queda blocked

    O->>B: D-001 · corregir G9 suite-roja
    B->>R: SedeDesconocida en vez de KeyError
    O->>G: gates de dev_backend
    G-->>O: verde
    O->>R: commit fix(dev_backend) … (D-001)
    Note over O: D-001 cerrada → T-004 se desbloquea

    O->>Q: T-004 de nuevo
    O->>G: G9
    G-->>O: VERDE · la suite pasa de verdad
    O->>R: commit test(tests) … (T-004)
```

Esa es la diferencia entre una cadena de montaje y un equipo: el defecto **cambia de
dueño** en vez de rebotar contra quien lo encontró.

---

## 5. Repo-as-state: qué se pasa entre nodos

Los agentes no se pasan contexto por chat. Todo lo que necesitan lo leen del repo.

```mermaid
flowchart TD
    subgraph VERSIONADO ["versionado en git · la auditoría"]
        v1["spec/00_intake.yaml"]
        v2["spec/10_product/"]
        v3["spec/20_arch/ + toolchain.yaml"]
        v4["spec/30_plan/tasks.yaml"]
        v5["src/ · migrations/"]
        v6["tests/ · spec/40_qa/"]
    end

    subgraph EFIMERO [".agent/ · fuera de git, estado de la corrida"]
        e1["state.json<br/>cursor, tareas, reintentos, historial"]
        e2["current_task.json<br/>la tarea que toca ahora"]
        e3["baseline.txt<br/>qué estaba sucio antes"]
        e4["reports/&lt;nodo&gt;.&lt;gate&gt;.json<br/>hallazgos del ciclo anterior"]
        e5["REPORT.md<br/>el reporte final"]
    end

    AG["agente de un nodo"]
    VERSIONADO -->|"lee lo relevante<br/>para su rol y su tarea"| AG
    e2 -->|"qué implementar"| AG
    e4 -->|"qué corregir"| AG
    AG -->|"escribe solo en sus paths"| VERSIONADO
```

`state.json` es lo que permite reanudar: `--from task_loop` continúa un sprint a
medias sin repetir lo ya cerrado.

---

## 6. Resiliencia de la llamada al modelo

Dos fallos distintos, dos defensas distintas. Antes había un único turno sin red de
seguridad, y un corte de transporte mataba el nodo entero.

```mermaid
flowchart LR
    C["providers.complete"] --> T{"¿qué pasó?"}
    T -->|"corte de transporte<br/>IncompleteRead, 429, 5xx"| R["reintento con backoff<br/>2s · 4s · 8s"]
    R --> C
    T -->|"truncada por max_tokens<br/>el modelo tenía más que decir"| K["continuación:<br/>se le devuelve lo escrito<br/>como prefill y sigue"]
    K --> C
    T -->|"error de lógica"| F(["ProviderError<br/>falla limpio, no reintenta"])
    T -->|"completa"| OK(["texto completo"])

    style OK fill:#bbf7d0,stroke:#15803d,color:#000
    style F fill:#fecaca,stroke:#b91c1c,color:#000
```

Si sigue truncando tras 6 continuaciones, el error lo dice claro: *la tarea es
demasiado grande, divide el plan*. Eso apunta al planner, que es donde está el
problema de verdad.

---

## Límites de este flujo

- **Secuencial.** El plan ya declara dependencias, así que las tareas sin relación
  podrían correr en paralelo; el bucle todavía las ejecuta de una en una.
- **R2 revisa el código por tarea** (`dev_backend`, `dev_frontend`, `qa`), con el
  mismo mecanismo que R1 y estado por tarea. Su criterio real no está medido: en
  `--simulate` no bloquea, y el modo real no se ha ejecutado.
- **R1/R2 cuestan una llamada al modelo por nodo/tarea y ronda.** El
  `skip_if_prior_failed` evita las inútiles, pero el coste no es cero: en un plan
  grande, R2 añade una llamada por tarea de código.
- **Sin aprendizaje entre corridas.** Un defecto que se repite siempre debería
  acabar en el prompt del nodo, y hoy no lo hace.
- **El presupuesto cuenta llamadas, no tokens ni USD.**
- **El modo real no se ha ejecutado contra un modelo.** Todo lo de arriba está
  verificado en simulación y con 78 pruebas.

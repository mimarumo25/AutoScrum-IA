# Contribuir a auto_scrum

Gracias por el interés en contribuir. Este documento resume cómo está
organizado el repo y qué reglas son innegociables antes de abrir un PR. Las
reglas completas viven en [`CLAUDE.md`](CLAUDE.md) y
[`ENGINEERING_QUALITY.md`](ENGINEERING_QUALITY.md); esto es el resumen para
quien no las haya leído todavía.

## Poner el proyecto a andar

```
pip install -e .
make setup   # equivalente
make demo    # bucle simulado, 0 tokens — la forma más rápida de ver el pipeline correr
make test    # suite de pruebas
make doctor  # verifica que el proveedor/API key configurados sean válidos
```

Copia `.env.example` a `.env` y completa las variables del proveedor que vayas
a usar (por defecto, `ANTHROPIC_API_KEY`). Nunca commitees `.env` ni
`config.json`; ambos están en `.gitignore`.

## Principio rector: repo-as-state

Los agentes del pipeline no se pasan contexto por chat: leen y escriben
artefactos versionados en git bajo `spec/`, `src/`, `tests/`. Si tu
contribución es al propio orquestador (`sdd/`), aplica el mismo criterio a tu
PR: que el diff sea la fuente de verdad, no una descripción aparte.

## Reglas que no se negocian

1. **No se relajan gates.** `gates/*.py`, `gates/registry.toml` y las
   secciones `budget`/`gates` de `pipeline.toml` son código determinista sin
   juicio. Si un gate te bloquea, el problema está en tu código; ajusta el
   código, nunca el umbral.
2. **Propiedad de paths por rol**, verificada por G7:

   | Rol           | Escribe en |
   |---------------|------------|
   | product       | `spec/10_product/` |
   | architect     | `spec/20_arch/` + esqueleto de build |
   | planner       | `spec/30_plan/` |
   | dev_backend   | `src/api/`, `src/domain/`, `src/infra/`, `migrations/`, `.env.example`, `spec/20_arch/env-contract.yaml` |
   | dev_frontend  | `src/web/`, `.env.example` |
   | qa            | `tests/`, `spec/40_qa/` |

   Si tu PR toca `sdd/` (el orquestador en sí, no un proyecto generado), esta
   tabla no aplica directamente, pero el espíritu sí: no mezcles cambios de
   dominios distintos en el mismo PR.
3. **`tests/` y `spec/` no se tocan para hacer pasar un cambio ajeno.** Si una
   prueba te bloquea y crees que está mal, abre un issue con el
   `file:line` y la evidencia; no la edites para que pase.
4. **No se simula un entregable ausente.** Si tu tarea depende de algo que no
   existe, dilo explícitamente en el PR en vez de tapar el hueco con un mock o
   un TODO que oculte el fallo.

## Reglas de código

- Objetivo ≤300 líneas por archivo, límite duro 500. Divide por
  responsabilidad de dominio, no para bajar el conteo de líneas.
- Dependencias hacia adentro: dominio no importa infraestructura ni framework.
- Cero secretos y cero valores dependientes del entorno en literales de
  código. Configuración vía esquema tipado que lee variables de entorno;
  variable ausente = fallo inmediato al arrancar. Toda variable nueva va a
  `.env.example` (y a `spec/20_arch/env-contract.yaml` si aplica).
- Tipado explícito en todo símbolo exportado. Prohibido `any`/`Any`.
- Errores tipados y logging estructurado. Prohibido `console.log`/`print`.
- Consultas parametrizadas; autorización por recurso y tenant en el backend.
- Commits en formato Conventional Commits, referenciando `FR-###` y
  `task_id` cuando aplique.

## Cómo enviar un cambio

1. Abre un issue describiendo el problema o la propuesta antes de invertir
   tiempo en el PR, salvo fixes triviales.
2. Un PR = una responsabilidad. PRs grandes o que mezclan dominios se piden
   partir.
3. Incluye evidencia de que corriste `make test` y, si tocaste un nodo con
   gates propios, `make gates NODE=<nodo> WORKDIR=<repo>`.
4. Si tu cambio afecta comportamiento documentado, actualiza el `README.md`
   correspondiente en el mismo PR.

## Reportar bugs de seguridad

No abras un issue público para vulnerabilidades. Contacta a los
mantenedores por los canales indicados en el repo antes de divulgar
públicamente.

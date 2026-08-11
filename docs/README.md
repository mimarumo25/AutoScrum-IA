# Documentación técnica

Punto de entrada a la documentación de profundidad de AutoScrum. El
[README.md](../README.md) raíz cubre instalación, uso y una vista de alto
nivel de la arquitectura; lo de aquí profundiza donde el README se queda
en la superficie, para quien va a leer o modificar código.

| Documento | Contenido |
|---|---|
| [architecture.md](architecture.md) | Grafo real de LangGraph, forma del estado, checkpointing/resume, scheduler paralelo, worktrees, lease. |
| [gates.md](gates.md) | Referencia completa de cada gate `G0`–`G10` y de `R1`/`R2`: qué verifica exactamente, cómo correrlo aislado, cómo proponer uno nuevo. |
| [glossary.md](glossary.md) | Términos del dominio (`spec_hash`, `task_id`, `worktree`, `lease`, `chronicle`, `lifecycle`, defecto `D-###`, etc.) con su significado preciso en este repo. |

## Otros documentos en este directorio

| Documento | Qué es |
|---|---|
| [auditoria/2026-07-29-auditoria-integral.md](auditoria/2026-07-29-auditoria-integral.md) | Auditoría integral histórica del pipeline. |
| [superpowers/plans/](superpowers/plans/) | Planes de implementación ejecutados (histórico de decisiones). |
| [superpowers/specs/](superpowers/specs/) | Specs de diseño de features ya implementadas. |

Para las reglas duras del pipeline (propiedad de paths, gates que no se
relajan, qué no toca un agente Dev) ver [`CLAUDE.md`](../CLAUDE.md) en la
raíz. Para el flujo de contribución, [`CONTRIBUTING.md`](../CONTRIBUTING.md).

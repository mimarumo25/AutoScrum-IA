# HANDOFF — estado real del sistema

## Qué es

Plano de control de un pipeline SDD multi-agente sobre `repo-as-state`: los agentes
no se pasan contexto por chat, leen y escriben artefactos versionados en git. El
orquestador transporta punteros y decisiones de ruta; el valor está en los gates,
la propiedad de paths, el bucle de tareas y el presupuesto. Todo se ejecuta y se
demuestra **sin gastar un token** con agentes simulados.

Documento vivo. Para el flujo detallado con diagramas, ver [FLUJO.md](FLUJO.md);
para operarlo, [README.md](README.md).

## Cómo verificarlo en esta máquina (Windows / Python 3.12)

```
python -m sdd test      # batería completa del plano de control
python -m sdd demo      # corrida simulada de extremo a extremo (0 tokens)
```

`sdd demo` converge a **`done | tareas: 5/5`**. El guion no es cosmético: construye
un proyecto Python real, ejercita una violación de propiedad revertida por G7, y
planta un bug de dominio que **solo una prueba ejecutada revela** (G9) — el
supervisor lo atribuye a `src/domain/`, abre la tarea de defecto `D-001` para el
backend, que la cierra y desbloquea a QA.

## Arquitectura en dos fases

1. **Lineal** — `product → architect → planner → gate humano`. Produce la
   especificación y `spec/30_plan/tasks.yaml`.
2. **Sprint durable** — LangGraph despacha tareas listas con `Send`; las huellas
   no superpuestas corren en worktrees paralelos y se integran en orden. Un
   defecto de otro nodo se vuelve una tarea `D-###` para su dueño.

## Verificación: dos categorías

- **`G*` — deterministas, sin juicio.** G0 (entregable presente), G1 (trazabilidad),
  G2 (spec técnica), G4 (tamaño), G5 (secretos/entorno), G6 (imports resueltos),
  G7 (propiedad de paths), G8 (cobertura de escenarios), G9 (**ejecuta** la suite:
  install/lint/typecheck/security/test/coverage según `toolchain.yaml`), G10 (plan
  ejecutable).
- **`R1` — revisor crítico, con juicio.** Un modelo lee el trabajo de `product`,
  `architect` y `planner`. Solo puede *añadir* defectos; solo los `blocking` frenan;
  tope de rondas; si se cae, el gate pasa y lo registra.

## Reglas de honestidad (el porqué de casi todo)

El pipeline no puede reportar un éxito que no ocurrió:

- un agente con exit != 0 no avanza (es defecto del nodo);
- G0 exige entregables reales: verde vacío no es verde;
- G9 ejecuta de verdad; sin ejecución el verde no significa nada;
- cada commit contiene solo lo que su nodo posee;
- relanzar un proyecto ya terminado se rechaza (no es un no-op que finge éxito);
- el reporte cuenta tokens (modo real) y distingue COMPLETADO de INCOMPLETO.

## Portabilidad (correcciones respecto al paquete original POSIX)

| Problema | Corrección |
|---|---|
| Estructura plana | reorganizado a `sdd/` con `gates/ agents/ examples/` |
| `python3` no existe (solo `python`) | `{py}` = `sys.executable` en los `.toml` |
| `make` ausente | `python -m sdd <cmd>` (o `sdd` tras `pip install -e .`) |
| `shlex.split` y backslashes | rutas con `.as_posix()`, intérprete con `shlex.quote` |

## Secuencia de arranque (modo real)

1. **Repo objetivo en un git worktree limpio**, no sobre código existente: los
   nodos Dev escriben y los gates fallarían de forma amplia contra un repo previo.
2. **`CLAUDE.md` va en la raíz del repo objetivo.** Es lo que hace que Claude Code
   herede las restricciones duras sin repetirlas en cada prompt.
3. Configura proveedor y API key (`sdd config`, `sdd doctor`) y lanza
   `sdd run --project <nombre>`. Se detiene mediante `interrupt()` tras el plan;
   reanuda con `sdd resume` para firmar y continuar.

## Reanudar una corrida (no perder avances)

El estado durable vive en `.agent/checkpoints.sqlite`; `.agent/state.json` es su
proyeccion legible para el panel y los reportes. Cada nodo/tarea commitea al pasar
sus gates, asi que una corrida interrumpida (conexion caida, proceso muerto) o
escalada NO pierde lo hecho. Para continuar desde donde quedo:

    sdd resume --workdir project/<nombre>/<tarea>          # desde el cursor guardado
    sdd resume --workdir project/<nombre>/<tarea> --node task_loop   # desde un nodo

En el panel web, la pestaña **Tareas** muestra **✓ Aprobar y continuar** en el gate
humano y **▸ Continuar** en una corrida escalada. `--resume` da presupuesto fresco (para
que lo que fallo se reintente en vez de re-escalar) y conserva los commits previos.
Nota: el panel debe reiniciarse (`sdd web`) para exponer el boton si estaba abierto
de antes.

## Lo que NO está hecho (ver la auditoría del sistema)

- El modo real **no se ha ejecutado contra un modelo**: todo lo verificado es el
  plano de control, en simulación y con pruebas. La calidad de los agentes reales
  y del criterio de R1 está sin medir.
- SQLite es el checkpointer local; despliegues multiproceso deben migrarlo a
  PostgreSQL.
- `config.json` guarda las API keys en claro (mitigado con permisos `600` +
  `.gitignore`, no cifrado).

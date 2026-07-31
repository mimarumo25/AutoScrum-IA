# Politica obligatoria de calidad de ingenieria

Esta es la fuente canonica para cualquier humano, editor o agente que cree o
modifique codigo en este repositorio. Aplica a Claude, Codex, Copilot, OpenCode,
Cursor, Windsurf, Gemini y cualquier automatizacion futura. Las instrucciones
especificas de cada herramienta no pueden rebajar estas reglas.

## Regla de entrega

Ningun cambio se considera terminado solo porque compila o parece correcto.
Toda creacion, correccion, refactor o cambio de comportamiento debe:

1. Entender el impacto antes de editar: contratos, callers, datos, persistencia,
   concurrencia, seguridad y compatibilidad.
2. Implementar la solucion minima correcta con responsabilidades claras.
3. Crear o actualizar pruebas unitarias para todo comportamiento modificado.
4. Agregar una prueba de regresion para cada bug corregido.
5. Ejecutar pruebas de integracion, persistencia, API o UI cuando el limite del
   cambio atraviese componentes; usar E2E para recorridos criticos del usuario.
6. Ejecutar todos los quality gates aplicables y corregir sus fallos.
7. Revisar SOLID, mantenibilidad, escalabilidad y seguridad antes de entregar.
8. Informar comandos ejecutados, resultados y cualquier riesgo no verificado.

Si una prueba o gate falla, el trabajo no esta completo. Nunca se elimina,
omite, marca como skip ni debilita una prueba o regla para obtener verde.

## Pruebas obligatorias

- Las pruebas deben verificar resultados observables, errores y casos limite;
  no deben limitarse a ejecutar lineas ni acoplarse a detalles internos.
- Cada rama relevante debe cubrir exito, rechazo/error y limites. Los cambios de
  estado persistente deben cubrir reanudacion e idempotencia.
- Concurrencia, presupuestos, autenticacion, autorizacion y validacion de entrada
  requieren pruebas negativas.
- Los dobles de prueba sustituyen fronteras externas, no la logica que se esta
  validando. No se permiten llamadas reales pagadas o dependencias de red en la
  suite unitaria.
- No se reduce cobertura ni se aceptan pruebas inestables. Una prueba flaky se
  corrige en su causa; no se reintenta indefinidamente ni se ignora.

Para cambios transversales en este proyecto, el baseline de entrega es:

```powershell
python -m pytest tests -q
python -m ruff check --select F sdd tests
python -m compileall -q sdd tests
uv lock --check
uv build
git diff --check
```

Ejecuta ademas linters, type checks, security scans y validadores propios del
lenguaje o componente modificado. Para JavaScript ejecuta al menos
`node --check` sobre archivos editados cuando no exista un gate mas completo.

## SOLID y arquitectura

- **S:** cada modulo, clase y funcion tiene una responsabilidad cohesionada.
  Separa por dominio o razon de cambio, nunca solo para reducir lineas.
- **O:** agrega comportamiento mediante limites y contratos estables cuando haya
  variacion real; no anticipes extensiones hipoteticas.
- **L:** implementaciones sustituibles conservan precondiciones, resultados,
  errores y efectos observables del contrato.
- **I:** interfaces pequenas y especificas; ningun consumidor depende de metodos
  o datos que no usa.
- **D:** dominio y casos de uso dependen de abstracciones, no de proveedores,
  almacenamiento, frameworks, red o UI.

Refactoriza codigo duplicado, funciones con responsabilidades mezcladas,
dependencias circulares y contratos ambiguos antes de ampliar comportamiento.
Prefiere composicion, funciones pequenas, nombres del dominio, tipos explicitos
y estado serializable. Evita capas, factories, wrappers y abstracciones sin una
necesidad concreta: altamente refactorizado no significa sobreingenieria.

## Mantenibilidad y escalabilidad

- Mantener archivos por debajo de 500 lineas; objetivo de 300. Si un archivo ya
  excede el limite y se modifica sustancialmente, dividirlo por responsabilidad.
- Evitar logica duplicada y fuentes de verdad paralelas. Todo workflow debe tener
  un contrato de estado y una autoridad clara para retries, budgets y routing.
- Operaciones con efectos deben ser idempotentes o tener journal/checkpoint.
- No introducir estado global mutable sin sincronizacion ni trabajo bloqueante
  dentro de rutas concurrentes o asincronas.
- Configuracion, timeouts, limites y proveedores deben ser inyectables y
  validados al inicio. No codificar valores del entorno en la logica.
- Errores deben conservar contexto accionable sin filtrar datos sensibles. No
  ocultar excepciones ni usar estrategias fail-open en quality o seguridad.
- Cambios publicos deben documentar contrato, migracion y compatibilidad.

## Seguridad obligatoria

- Validar y normalizar toda entrada no confiable en la frontera del sistema.
- Aplicar minimo privilegio, autorizacion por recurso y defaults fail-closed.
- Nunca registrar, persistir o commitear secretos, tokens, credenciales o PII.
- Evitar inyeccion de comandos, SQL, paths y prompts; usar APIs parametrizadas,
  allowlists y resolucion segura de rutas.
- Fijar limites para tamano, tiempo, concurrencia, retries y consumo de recursos.
- Revisar dependencias y lockfiles; no agregar paquetes innecesarios o sin rango.
- Los cambios de autenticacion, autorizacion, criptografia, ejecucion de procesos,
  carga de archivos o red requieren una prueba de abuso o caso negativo.

## Quality gates y revision final

Antes de entregar, revisar el diff completo y confirmar:

- No hay cambios ajenos, secretos, artefactos generados ni debug temporal.
- Los criterios de aceptacion tienen pruebas trazables.
- Los gates son estrictos y fail-closed; nunca se relajaron para aprobar.
- Tipos, contratos, errores y logs son consistentes.
- No hay regresiones evidentes de rendimiento, concurrencia o seguridad.
- Documentacion y ejemplos representan el comportamiento real.
- La suite relevante y el baseline obligatorio estan verdes.

Si una verificacion no puede ejecutarse, declararlo como riesgo; nunca afirmar
que el cambio esta validado sin evidencia.

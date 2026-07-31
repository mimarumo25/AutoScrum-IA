# ADR-001: Selección del lenguaje de ejecución

**Decisión**: Utilizar Node.js (versión ≥18) como runtime.

**Contexto**: El sistema debe ejecutarse únicamente con la biblioteca estándar del lenguaje elegido. Se requiere un servidor HTTP, generación de códigos únicos, pruebas automatizadas y manejo de concurrencia ligera para el contador de visitas y la idempotencia. El despliegue no debe requerir compilación ni dependencias externas.

**Alternativas descartadas**:
1. **Python (CPython 3.x)**:
   - Motivo de descarte: aunque el módulo `http.server` forma parte de la biblioteca estándar, su API es de bajo nivel y no ofrece un entorno de pruebas integrado tan directo como el de Node (`node:test`). Para pruebas unitarias y de integración se dependería de `unittest`, que requiere más boilerplate y no descubre automáticamente archivos de test sin configuración adicional. Además, la falta de soporte nativo para asincronía en `http.server` obligaría a usar `ThreadingMixIn` para manejar múltiples peticiones, añadiendo complejidad innecesaria.
2. **Go**:
   - Motivo de descarte: la biblioteca estándar de Go incluye `net/http` y `testing`, pero el ciclo de compilación obliga a un paso de `go build` previo a la ejecución, lo que aumenta el tiempo de arranque y la complejidad del pipeline. El sistema está pensado para una entrega rápida y el compilador no aporta beneficios en un escenario puramente en memoria sin requisitos de rendimiento extremo.

**Coste mensual estimado**: 0 USD (software de código abierto, ejecutado en servidor propio o entorno local).

**Consecuencias**:
- Se aprovechan los módulos `node:http`, `node:test`, `node:assert` y `node:crypto` (para generación de códigos) sin ninguna instalación adicional.
- El pipeline de CI se simplifica a `npm test` (que ejecuta el test runner nativo) sin pasos de compilación.
- El equipo debe tener disponible Node.js ≥18 en los entornos de desarrollo y CI.

**Condición de reversión**: Si en el futuro se necesitara renderizado en el lado del servidor o una integración más profunda con sistemas que usen otro lenguaje, se consideraría reescribir el servidor en Python o Go, siempre que se mantenga el principio de cero dependencias externas.

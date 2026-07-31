# ADR-004: Servidor HTTP sin frameworks

**Estado:** Aceptado  
**Fecha:** 2025-03-28  
**Decisores:** Arquitecto de software

## Contexto
La restricción de "cero dependencias externas" impide el uso de Express, Fastify o cualquier otro framework HTTP. El servicio debe exponer exactamente tres rutas.

## Alternativas consideradas

| Opción | Justificación de descarte |
|--------|---------------------------|
| Implementar un router mínimo copiando código de un paquete externo (no dependencia declarada) | Viola el espíritu de la restricción y añade código de terceros sin mantenimiento claro. |
| Utilizar un framework incluido en la instalación estándar de Node (no existe ninguno) | Imposible; Node solo ofrece `http` como módulo base. |

## Decisión
Usar **`http.createServer`** nativo, analizando `req.method` y `req.url` para despachar a las funciones controladoras. Se extraerá la lógica de enrutamiento en una pequeña función `router(req, res, store)` para facilitar el mantenimiento.

## Coste mensual estimado
**$0**.

## Consecuencias
- **Positivo:** Tamaño del proyecto mínimo, arranque rápido.
- **Positivo:** Control total sobre el ciclo de vida de las peticiones.
- **Negativo:** No se dispone de middleware para parsing de cuerpo, logs o CORS; se implementará manualmente el parseo de JSON (con límite de tamaño) y las cabeceras necesarias.
- **Negativo:** Si el número de rutas crece, el enrutador manual se volverá difícil de mantener; en ese punto se consideraría extraer un mini-router propio.

## Condición de reversión
Si la API supera las 10 rutas, evaluar la inclusión de una dependencia de enrutamiento ligera (por ejemplo, `router` de npm) mediante un nuevo ADR que contemple la relajación de la restricción de dependencias.

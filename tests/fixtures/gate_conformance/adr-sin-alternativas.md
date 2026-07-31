# ADR-002: Almacenamiento en memoria con Map

**Estado:** Aprobado  
**Fecha:** 2025-03-21  

## Contexto
El intake prohíbe bases de datos y persistencia en disco. La información (códigos cortos, URLs largas y contadores de visitas) debe residir exclusivamente en memoria. Se necesita una estructura que ofrezca acceso rápido por código y operaciones de inserción/actualización atómicas.

## Decisión
Utilizar un objeto nativo `Map` de JavaScript como almacén principal, donde la clave es el código corto (string) y el valor es un objeto `{ url, visitas }`.

## Coste mensual estimado (USD)
$0 – sin costes externos.

## Consecuencias
- Pérdida total de datos al reiniciar el proceso.  
- Escalabilidad horizontal imposible; solo escala verticalmente dentro de un único proceso.  
- La implementación debe garantizar que las operaciones sobre el mapa sean thread-safe (véase ADR-004).  

## Condición de reversión
Si se requiere persistencia o se alcanzan volúmenes que no caben en memoria (más de ~10 millones de entradas), se introducirá una base de datos en disco o una cache externa, rompiendo la restricción inicial.

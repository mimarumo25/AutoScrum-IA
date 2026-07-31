# Fixtures de conformidad de gates

Artefactos de **corridas reales** del pipeline. Un fixture inventado prueba lo que
el autor imaginó; uno copiado de una corrida prueba lo que el sistema produce de
verdad. Cada archivo dice de dónde salió y qué debe ocurrir con él.

Nomenclatura: un fixture *golden-pass* debe pasar el gate; uno *golden-fail* debe
reprobarlo. Los golden-fail existen para que una corrección de gate no pueda
convertirse en una relajación sin que una prueba lo delate.

## NFR (G2)

Origen: `project/acortador-v3/tarea-1/spec/20_arch/nfr.yaml`

| Fixture | Cambio respecto al original | Debe |
|---|---|---|
| `nfr-ingles-valido.yaml` | `gate_id: G-test` → `G9` (gate real) | **pasar** — declara `metric`/`threshold` en inglés, como exige CLAUDE.md |
| `nfr-gate-citado.yaml` | `gate_id: G-test` → `"G9"` entrecomillado | **pasar** — YAML entrecomillado es válido |
| `nfr-gate-inexistente.yaml` | ninguno (conserva `gate_id: G-test`) | **fallar** con `nfr-gate-inexistente` — `G-test` no está en el registro, y ese hallazgo es correcto |

## ADR (G2)

Origen: `project/acortador-min/tarea-1/spec/20_arch/adr/ADR-002-almacenamiento-map.md`

El original cumple `agents/architect.md:17-18` al pie de la letra: contexto,
decisión, dos alternativas descartadas con su razón, coste en USD, consecuencias
y condición de reversión.

| Fixture | Cambio respecto al original | Debe |
|---|---|---|
| `adr-valido.md` | ninguno | **pasar** |
| `adr-una-alternativa.md` | borrada la alternativa 2 | **fallar** con `adr-sin-alternativas` |
| `adr-sin-alternativas.md` | borrada la sección `## Alternativas` completa | **fallar** con `adr-sin-alternativas` |
| `adr-sin-coste.md` | borrada la sección `## Coste mensual estimado` | **fallar** con `adr-sin-coste` |

Las tres variantes se derivaron del original por script, no a mano, para que la
única diferencia sea la que dice la tabla.

## El defecto que estos fixtures capturan

`check_arch_spec.py` medía vocabulario en lugar de estructura:

- Contaba `t.count("alternativa") < 2` sobre todo el archivo. `adr-valido.md` y
  `adr-una-alternativa.md` dan **ambos 1** (la palabra aparece solo en el
  encabezado `## Alternativas consideradas`): el gate era **incapaz de
  distinguir un ADR conforme de uno al que le falta una alternativa**, y
  reprobaba los dos.
- Comprobaba `"umbral" in block` y `"metrica" in block`, substrings en español,
  contra artefactos que CLAUDE.md exige en inglés. Cada entrada NFR conforme
  generaba dos hallazgos falsos.
- Capturaba `gate_id` con `(\S+)`, así que `"G9"` llegaba **con las comillas** y
  no coincidía con ningún gate del registro.

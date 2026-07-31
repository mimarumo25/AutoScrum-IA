"""Arnes de conformidad: cada regla de gate contra un artefacto real que debe
pasar y uno que debe fallar.

Por que existe: un gate puede reprobar por el motivo equivocado. G2 comprobaba
`t.count("alternativa") < 2` sobre todo el ADR, asi que un ADR conforme con dos
alternativas numeradas bajo un solo encabezado daba 1 y se reprobaba — y era
INDISTINGUIBLE de uno al que de verdad le falta una alternativa. Un gate que no
separa lo correcto de lo incorrecto no es un gate: es ruido con exit 1.

Este arnes fija las dos mitades del contrato:

  golden-pass  un artefacto real que cumple la especificacion NO debe generar
               el hallazgo. Es lo que atrapa un falso positivo.
  golden-fail  un artefacto que viola la regla SI debe generarlo. Es lo que
               impide que "corregir el gate" se convierta en relajarlo.

Sin la mitad golden-fail, cualquier correccion pasa. Con ella, una correccion
que se pase de largo lo delata en la misma corrida.

    python -m unittest tests.test_gate_conformance -v
    python -m unittest discover -s tests
"""
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
GATES = ROOT / "gates"
FIXTURES = Path(__file__).resolve().parent / "fixtures/gate_conformance"
TESTS_DIR = Path(__file__).resolve().parent
PY = sys.executable


def run_checker(script: str, *args: str) -> tuple[list[dict], int]:
    """Corre un checker y devuelve (findings, returncode) segun su contrato."""
    proc = subprocess.run([PY, str(GATES / script), *map(str, args)],
                          capture_output=True, text=True)
    try:
        findings = json.loads(proc.stdout or "{}").get("findings", [])
    except json.JSONDecodeError:
        raise AssertionError(f"{script} no emitio JSON valido:\n"
                             f"STDOUT={proc.stdout!r}\nSTDERR={proc.stderr!r}")
    return findings, proc.returncode


def rules(findings: list[dict]) -> set[str]:
    return {f["rule"] for f in findings}


def write(base: Path, rel: str, body: str) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestG2Conformance(unittest.TestCase):
    """G2 contra los artefactos reales de `tests/fixtures/gate_conformance/`.

    Cada caso siembra un arbol `spec/20_arch/` completo (para que
    `artefacto-faltante` no ensucie el resultado) y sustituye un solo artefacto
    por el fixture bajo prueba. La asercion es sobre UNA regla concreta, asi que
    los hallazgos de las demas reglas no contaminan el caso.
    """

    # (fixture, destino, regla, debe_aparecer)
    CASOS = [
        # --- NFR ---------------------------------------------------------
        # Claves en INGLES, como exige CLAUDE.md. El gate probaba substrings
        # en espanol ("umbral", "metrica"): dos falsos positivos por entrada.
        ("nfr-ingles-valido.yaml", "spec/20_arch/nfr.yaml",
         "nfr-no-medible", False),
        # gate_id entrecomillado: YAML valido que el gate leia con las comillas
        # dentro del identificador y por tanto no encontraba en el registro.
        ("nfr-gate-citado.yaml", "spec/20_arch/nfr.yaml",
         "nfr-gate-inexistente", False),
        ("nfr-gate-citado.yaml", "spec/20_arch/nfr.yaml",
         "nfr-no-medible", False),
        # Verdadero positivo: 'G-test' no existe en el registro. Debe seguir
        # fallando despues de cualquier correccion.
        ("nfr-gate-inexistente.yaml", "spec/20_arch/nfr.yaml",
         "nfr-gate-inexistente", True),
        # --- ADR ---------------------------------------------------------
        # ADR real y conforme: dos alternativas numeradas bajo un encabezado,
        # coste en USD, consecuencias y condicion de reversion.
        ("adr-valido.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", False),
        ("adr-valido.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-coste", False),
        # Golden-fail: la seccion existe pero solo enumera una alternativa.
        # Bajo el gate viejo este archivo y adr-valido.md eran indistinguibles.
        ("adr-una-alternativa.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", True),
        ("adr-sin-alternativas.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", True),
        ("adr-sin-coste.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-coste", True),
        # Los arquitectos reales usan cuatro formas distintas de declarar las
        # alternativas. Cada una necesita su golden-pass, o la siguiente
        # correccion del gate vuelve a romper la que no este cubierta.
        ("adr-etiqueta-negrita.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", False),
        ("adr-tabla.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", False),
        # Golden-fail de la forma tabla: cabecera + separador + UNA sola fila.
        ("adr-tabla-una-fila.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-alternativas", True),
        # Coste declarado en INGLES ('## Cost estimate', '$0/month'), como los
        # artefactos que exige CLAUDE.md. La regex solo aceptaba español.
        ("adr-coste-ingles.md", "spec/20_arch/adr/ADR-001.md",
         "adr-sin-coste", False),
    ]

    def _seed(self, base: Path) -> None:
        """Los cuatro artefactos que G2 exige, todos validos."""
        write(base, "spec/20_arch/nfr.yaml", fixture("nfr-ingles-valido.yaml"))
        write(base, "spec/20_arch/api/openapi.yaml",
              "openapi: 3.1.0\ninfo: {title: x, version: '1'}\npaths: {}\n")
        write(base, "spec/20_arch/env-contract.yaml",
              "variables:\n  - name: DATABASE_URL\n    tipo: url\n"
              "    requerida: true\n    secreta: true\n")
        write(base, "spec/20_arch/threat-model.md", "# STRIDE\nOWASP A01\n")
        write(base, "spec/20_arch/adr/ADR-001.md", fixture("adr-valido.md"))

    def test_conformidad(self) -> None:
        for nombre, destino, regla, esperado in self.CASOS:
            with self.subTest(fixture=nombre, regla=regla, esperado=esperado):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    self._seed(base)
                    write(base, destino, fixture(nombre))
                    findings, _ = run_checker("check_arch_spec.py",
                                              "--workdir", base.as_posix())
                    presente = regla in rules(findings)
                    self.assertEqual(
                        presente, esperado,
                        f"{nombre}: se esperaba {'' if esperado else 'NO '}"
                        f"generar '{regla}'. Hallazgos: {sorted(rules(findings))}")

    def test_artefacto_faltante_sigue_detectandose(self) -> None:
        """El arnes no debe volver ciego al gate para lo que ya detectaba bien."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._seed(base)
            (base / "spec/20_arch/threat-model.md").unlink()
            findings, rc = run_checker("check_arch_spec.py",
                                       "--workdir", base.as_posix())
            self.assertEqual(rc, 1)
            self.assertIn("artefacto-faltante", rules(findings))


class TestG5Conformance(unittest.TestCase):
    """G5: valores dependientes del entorno en codigo.

    Las cuatro reglas de G5 vivian en un `for rule, rx in RULES` y por eso el
    conteo de reglas de este arnes no las veia. Al hacerlas visibles resulto que
    tres de las cuatro no tenian ninguna prueba.
    """

    LIMPIO = ("import os\n\n\n"
              "def build() -> str:\n"
              "    return os.environ['DATABASE_URL']\n")

    # (regla, linea de codigo que la viola)
    VIOLACIONES = [
        ("hardcoded-url", 'BASE = "https://api.stripe.com/v1"\n'),
        ("hardcoded-secret", 'api_key = "sk_live_abcdefgh12345678"\n'),
        ("hardcoded-port", "port = 5432\n"),
        ("hardcoded-dsn", 'DSN = "postgres://user:pw@db.internal/app"\n'),
    ]

    def _run(self, body: str) -> set[str]:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, "src/infra/config.py", body)
            write(base, "spec/20_arch/env-contract.yaml",
                  "variables:\n  - name: DATABASE_URL\n    requerida: true\n")
            write(base, ".env.example", "DATABASE_URL=postgres://localhost/dev\n")
            findings, _ = run_checker("check_hardcoding.py",
                                      "--workdir", base.as_posix())
            return rules(findings)

    def test_cada_violacion_se_detecta(self) -> None:
        for regla, linea in self.VIOLACIONES:
            with self.subTest(regla=regla):
                self.assertIn(regla, self._run(self.LIMPIO + linea))

    def test_codigo_que_lee_del_entorno_pasa(self) -> None:
        self.assertEqual(self._run(self.LIMPIO), set())

    def test_gate_ignore_exime_la_linea(self) -> None:
        """El escape existe para el falso positivo legitimo (una URL de esquema
        en un comentario, por ejemplo). Si dejara de funcionar, el unico remedio
        seria relajar la regex, que es exactamente lo que no se debe hacer."""
        for regla, linea in self.VIOLACIONES:
            with self.subTest(regla=regla):
                exenta = linea.rstrip("\n") + "  # gate-ignore\n"
                self.assertNotIn(regla, self._run(self.LIMPIO + exenta))


# --- Cobertura del propio arnes ---------------------------------------------

def _rule_identifiers(source: str) -> tuple[set[str], set[str]]:
    """Extrae las reglas que un checker puede emitir.

    Devuelve (resueltas, no_resueltas). El tercer argumento posicional de
    `finding(file, line, rule, evidence)` es la regla. Cuando es una variable,
    se intenta resolver su asignacion si es un dict literal o un `.get()` sobre
    uno — el patron que usan check_suite y check_hardcoding. Lo que no se pueda
    resolver estaticamente se devuelve como no resuelto, nunca se adivina.
    """
    tree = ast.parse(source)
    assigned: dict[str, set[str]] = {}

    # RULES = [("hardcoded-url", rx), ...] recorrido con `for rule, rx in RULES`.
    # Sin resolver esto, las 4 reglas de G5 quedan invisibles para el conteo y la
    # cobertura seria una ilusion.
    sequences: dict[str, list[ast.Tuple]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            items = [e for e in node.value.elts if isinstance(e, ast.Tuple)]
            if items:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        sequences[target.id] = items
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
                and isinstance(node.iter, ast.Name)):
            continue
        items = sequences.get(node.iter.id)
        if not items:
            continue
        for position, element in enumerate(node.target.elts):
            if not isinstance(element, ast.Name):
                continue
            values = {item.elts[position].value for item in items
                      if position < len(item.elts)
                      and isinstance(item.elts[position], ast.Constant)
                      and isinstance(item.elts[position].value, str)}
            if values:
                assigned.setdefault(element.id, set()).update(values)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            continue
        values: set[str] = set()
        source_node = node.value
        # rule = {...}.get(step, "por-defecto")
        if isinstance(source_node, ast.Call) and \
                isinstance(source_node.func, ast.Attribute) and \
                source_node.func.attr == "get":
            for arg in source_node.args[1:]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    values.add(arg.value)
            source_node = source_node.func.value
        if isinstance(source_node, ast.Dict):
            values.update(v.value for v in source_node.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if values:
            for name in targets:
                assigned.setdefault(name, set()).update(values)

    resolved: set[str] = set()
    unresolved: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "finding"):
            continue
        if len(node.args) < 3:
            unresolved.add(f"finding() con {len(node.args)} args posicionales")
            continue
        arg = node.args[2]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            resolved.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in assigned:
            resolved.update(assigned[arg.id])
        else:
            unresolved.add(ast.unparse(arg))
    return resolved, unresolved


class TestArnesCubreTodasLasReglas(unittest.TestCase):
    """Cada regla que un checker puede emitir tiene que estar ejercida en algun
    test. Sin esto el arnes se queda atras en silencio en cuanto alguien anade
    una regla, y el gate vuelve a ser una caja negra."""

    # Reglas sin cobertura. Cada entrada necesita un motivo. Vacio es el
    # objetivo; lo prohibido es una regla exenta sin explicacion.
    #
    # HUECO REAL, no excepcion de diseno: las 15 entradas marcadas
    # "sin-cobertura" son reglas que NINGUN test de la suite menciona. Las
    # descubrio este arnes la primera vez que corrio. Estan aqui para que el
    # hueco sea visible y contable, no para silenciarlo: cada una que se cubra
    # se borra de esta lista. Vaciar esta lista es trabajo pendiente declarado.
    _HUECO = "sin-cobertura: hueco real detectado por este arnes"
    SIN_ARNES_TODAVIA = {
        # check_review.py reenvia `f["rule"]` tal como lo emite el modelo
        # revisor: no hay un conjunto fijo de reglas que enumerar.
        "revision-no-disponible": "cubierta en tests/test_revisor.py",
        # --- check_plan.py: validacion de forma del plan (G10) --------------
        "campo-faltante": _HUECO,
        "dependencia-inexistente": _HUECO,
        "fr-invalido": _HUECO,
        "id-invalido": _HUECO,
        "nodo-invalido": _HUECO,
        "plan-invalido": _HUECO,
        "plan-vacio": _HUECO,
        "tarea-invalida": _HUECO,
        # --- check_suite.py: caminos de fallo de entorno y de pasos (G9) ----
        # Varias exigen simular un entorno hostil (sin red, comando colgado),
        # que es mas caro de montar que las demas.
        "comando-fallido": _HUECO,
        "entorno-sin-red": _HUECO,
        "seguridad-rojo": _HUECO,
        "suite-colgada": _HUECO,
        "toolchain-invalido": _HUECO,
        # --- otros ---------------------------------------------------------
        "env-sin-ejemplo": _HUECO,
        "sin-escenarios": _HUECO,
    }

    def _menciones_en_tests(self) -> str:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         for path in sorted(TESTS_DIR.glob("test_*.py")))

    def test_toda_regla_esta_ejercida(self) -> None:
        texto = self._menciones_en_tests()
        sin_cubrir: list[str] = []
        for path in sorted(GATES.glob("check_*.py")):
            resolved, _ = _rule_identifiers(path.read_text(encoding="utf-8"))
            for regla in sorted(resolved):
                if regla in self.SIN_ARNES_TODAVIA:
                    continue
                if f'"{regla}"' not in texto and f"'{regla}'" not in texto:
                    sin_cubrir.append(f"{path.name}: {regla}")
        self.assertEqual(sin_cubrir, [],
                         "reglas de gate sin ningun test que las mencione; "
                         "anade el caso o justificalas en SIN_ARNES_TODAVIA")

    def test_no_hay_reglas_irresolubles_sin_declarar(self) -> None:
        """Si un checker construye la regla de un modo que este arnes no sabe
        leer, la cobertura seria una ilusion. Se declara explicitamente."""
        # check_review.py reenvia la regla que emite el modelo revisor: no hay
        # un conjunto fijo que enumerar, y por eso se declara aqui en vez de
        # fingir que el extractor la resuelve.
        esperadas_dinamicas = {"check_review.py": {"f['rule']"}}
        for path in sorted(GATES.glob("check_*.py")):
            _, unresolved = _rule_identifiers(path.read_text(encoding="utf-8"))
            with self.subTest(checker=path.name):
                self.assertEqual(
                    unresolved, esperadas_dinamicas.get(path.name, set()),
                    f"{path.name} construye reglas de una forma que el "
                    f"extractor no resuelve; extiende _rule_identifiers o "
                    f"declaralo en esperadas_dinamicas")

    def test_las_exenciones_no_estan_vacias_de_motivo(self) -> None:
        for regla, motivo in self.SIN_ARNES_TODAVIA.items():
            with self.subTest(regla=regla):
                self.assertTrue(motivo.strip(),
                                f"'{regla}' esta exenta sin motivo escrito")


if __name__ == "__main__":
    unittest.main()

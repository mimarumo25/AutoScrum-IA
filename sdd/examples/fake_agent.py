#!/usr/bin/env python3
"""Agente simulado. NO usa modelos: escribe artefactos deterministas para
demostrar que el plano de control detecta, enruta, revierte y converge.

El proyecto que construye es minusculo pero REAL: dominio en Python, una suite
que se ejecuta de verdad con unittest y un toolchain.yaml que el gate G9 corre.
Sin eso el demo probaria el enrutamiento pero no la unica pregunta que importa:
¿el pipeline sabe distinguir software que funciona de software que no?

Guion (modo normal):
  product      falla G1 en el intento 1 (FR sin escenario) y corrige en el 2.
  architect    entrega la especificacion completa, incluido toolchain.yaml.
  planner      corta el sistema en T-001..T-004 con dependencias reales.
  dev_backend  T-001 sale con un defecto que solo una prueba ejecutada revela.
  qa           T-004 viola propiedad (G7 revierte), luego escribe la suite; la
               suite se pone roja y el fallo se atribuye a src/domain/ -> el
               supervisor abre una tarea de defecto D-001 para dev_backend.
  dev_backend  cierra D-001; T-004 se desbloquea y la suite pasa en verde.

Con SDD_FAKE_STUCK el agente NUNCA corrige: ejercita el techo de reintentos y la
escalacion a humano (ESCALATE_HUMAN).
Con SDD_FAKE_PARALLEL T-002, T-003 y T-005 quedan listas juntas para probar una
ola ancha de Send workers.
"""
import json
import os
import shutil
import sys
from pathlib import Path

STUCK = bool(os.environ.get("SDD_FAKE_STUCK"))
PARALLEL = bool(os.environ.get("SDD_FAKE_PARALLEL"))
node, workdir = sys.argv[1], Path(sys.argv[2])

_task_path = workdir / ".agent/current_task.json"
TASK = json.loads(_task_path.read_text(encoding="utf-8")) if _task_path.exists() else None
TID = TASK["id"] if TASK else None
KIND = TASK["kind"] if TASK else None

counter = workdir / ".agent/fake" / (f"{node}.{TID}" if TID else node)
counter.parent.mkdir(parents=True, exist_ok=True)
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))

# El repo objetivo se prueba con el mismo Python que corre el pipeline. En un
# proyecto real este comando lo declara el arquitecto segun su stack.
PYCMD = Path(shutil.which("python") or shutil.which("python3") or sys.executable).name


def w(rel, body):
    p = workdir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.lstrip("\n"), encoding="utf-8")


def done_marker(name):
    """Marca de progreso propia del simulador (fuera del arbol versionado)."""
    return workdir / ".agent/fake" / name


# --- Fase lineal ------------------------------------------------------------

if node == "product":
    w("spec/10_product/prd.md", """
# PRD reinscripcion
FR-001 el acudiente renueva la matricula de un estudiante.
FR-002 el administrador consulta el estado de renovacion por sede.
""")
    feat = """
Caracteristica: Reinscripcion

  @FR-001 @SCN-001 @p1 @critical
  Escenario: renovacion exitosa
    Dado que el acudiente tiene sesion activa
    Cuando confirma la renovacion de su estudiante
    Entonces recibe el comprobante de la matricula
"""
    if n > 1 and not STUCK:  # correccion: FR-002 tenia requerimiento sin escenario
        feat += """
  @FR-002 @SCN-002 @p2
  Escenario: consulta de estado por sede
    Dado que el administrador tiene sesion activa
    Cuando abre el tablero de su sede
    Entonces ve el estado de renovacion de cada estudiante
"""
    w("spec/10_product/features/reinscripcion.feature", feat)

elif node == "architect":
    w("spec/20_arch/nfr.yaml", """
nfr:
  - id: NFR-001
    categoria: rendimiento
    metrica: latencia_p95_ms
    umbral: 800
    metodo_de_medicion: k6 sobre POST /matriculas
    gate_id: manual
""")
    w("spec/20_arch/api/openapi.yaml",
      "openapi: 3.1.0\ninfo: {title: matriculas, version: '1'}\npaths: {}\n")
    w("spec/20_arch/env-contract.yaml", """
variables:
  - name: PAYMENT_API_URL
    tipo: url
    requerida: true
    secreta: false
""")
    w("spec/20_arch/adr/ADR-001.md", """
# ADR-001 monolito modular sobre PostgreSQL
Alternativa descartada: microservicios, no hay disparador de escala.
Alternativa descartada: Firebase, no cubre reportes relacionales.
Coste mensual estimado: 28 USD.
""")
    w("spec/20_arch/threat-model.md",
      "# STRIDE\nSpoofing: sesion por token corto. OWASP A01, A03 mapeados.\n")
    # El contrato que hace ejecutable al gate G9.
    # Sin -t: unittest pone tests/ en sys.path como top-level y el cwd queda en
    # sys.path[0], asi 'from src.domain...' resuelve. Con -t . exige que tests/
    # sea paquete importable y la suite ni arranca.
    w("spec/20_arch/toolchain.yaml", f"""
language: python
dir: .
test: {PYCMD} -m unittest discover -s tests -v
""")

elif node == "planner":
    frontend_dep = "[T-001]" if PARALLEL else "[T-002]"
    extra_task = """
  - id: T-005
    title: registro de auditoria de matriculas
    node: dev_backend
    scope: infra
    fr_refs: [FR-002]
    deliverables: [src/infra/auditoria.py]
    context: [src/domain/matricula.py]
    depends_on: [T-001]
    acceptance: registrar() devuelve un evento inmutable con estudiante y sede
""" if PARALLEL else ""
    qa_dependencies = "[T-001, T-002, T-003, T-005]" if PARALLEL else \
        "[T-001, T-002, T-003]"
    w("spec/30_plan/tasks.yaml", f"""
tasks:
  - id: T-001
    title: reglas de renovacion de matricula
    node: dev_backend
    scope: domain
    fr_refs: [FR-001]
    deliverables: [src/domain/matricula.py]
    context: []
    depends_on: []
    acceptance: renovar() rechaza una sede desconocida con error tipado del dominio

  - id: T-002
    title: handler de creacion de matricula
    node: dev_backend
    scope: api
    fr_refs: [FR-001, FR-002]
    deliverables: [src/api/matriculas.py, .env.example]
    context: [src/domain/matricula.py]
    depends_on: [T-001]
    acceptance: crear() resuelve la URL de pago desde el entorno y propaga el tenant

  - id: T-003
    title: vista de renovacion del acudiente
    node: dev_frontend
    scope: web
    fr_refs: [FR-001]
    deliverables: [src/web/renovacion.js]
    context: [spec/20_arch/api/openapi.yaml]
    depends_on: {frontend_dep}
    acceptance: la vista expone los estados loading, empty, error y success

{extra_task}

  - id: T-004
    title: suite de dominio y contrato
    node: qa
    scope: tests
    fr_refs: [FR-001, FR-002]
    deliverables: [tests/test_matricula.py, spec/40_qa/traceability.md]
    context: [src/domain/matricula.py, src/api/matriculas.py]
    depends_on: {qa_dependencies}
    acceptance: SCN-001 y SCN-002 cubiertos y la suite completa en verde
""")

# --- Bucle de tareas --------------------------------------------------------

elif node == "dev_backend":
    if KIND == "defect" or done_marker("fix_dominio").exists():
        # Cierra D-001: la sede desconocida deja de reventar con KeyError.
        done_marker("fix_dominio").write_text("1")
        w("src/domain/matricula.py", """
\"\"\"Reglas de renovacion. Dominio puro: no importa infraestructura ni framework.\"\"\"


class ErrorDeDominio(Exception):
    \"\"\"Raiz de los errores tipados del dominio.\"\"\"


class SedeDesconocida(ErrorDeDominio):
    pass


class SinCupo(ErrorDeDominio):
    pass


ESTADOS = ("pendiente", "renovada")


def renovar(estudiante_id: str, cupos_por_sede: dict, sede: str) -> dict:
    if sede not in cupos_por_sede:
        raise SedeDesconocida(sede)
    if cupos_por_sede[sede] <= 0:
        raise SinCupo(sede)
    return {"estudiante_id": estudiante_id, "sede": sede, "estado": "renovada"}
""")
    elif TID == "T-001":
        # Defecto plantado: la sede ausente revienta con KeyError en vez de con un
        # error del dominio. Ningun linter lo ve; solo una prueba EJECUTADA lo caza.
        w("src/domain/matricula.py", """
\"\"\"Reglas de renovacion. Dominio puro: no importa infraestructura ni framework.\"\"\"


class ErrorDeDominio(Exception):
    \"\"\"Raiz de los errores tipados del dominio.\"\"\"


class SedeDesconocida(ErrorDeDominio):
    pass


class SinCupo(ErrorDeDominio):
    pass


ESTADOS = ("pendiente", "renovada")


def renovar(estudiante_id: str, cupos_por_sede: dict, sede: str) -> dict:
    if cupos_por_sede[sede] <= 0:
        raise SinCupo(sede)
    return {"estudiante_id": estudiante_id, "sede": sede, "estado": "renovada"}
""")
    elif TID == "T-002":
        w("src/api/matriculas.py", """
import os

from src.domain.matricula import renovar


def crear(payload: dict, cupos: dict) -> dict:
    base_url = os.environ["PAYMENT_API_URL"]
    resultado = renovar(payload["estudiante_id"], cupos, payload["sede"])
    resultado["url_pago"] = base_url + "/transactions"
    resultado["tenant"] = payload["tenant_id"]
    return resultado
""")
        w(".env.example", "PAYMENT_API_URL=https://sandbox.example.test\n")
    elif TID == "T-005":
        w("src/infra/auditoria.py", """
def registrar(estudiante_id: str, sede: str) -> dict:
    return {"estudiante_id": estudiante_id, "sede": sede, "tipo": "matricula"}
""")

elif node == "dev_frontend":
    w("src/web/renovacion.js", """
import { crearMatricula } from './client.js';

export const ESTADOS_VISTA = ['loading', 'empty', 'error', 'success'];

export async function renovar(estudianteId) {
  return crearMatricula({ estudianteId });
}
""")
    w("src/web/client.js", """
export async function crearMatricula(payload) {
  return { estado: 'renovada', ...payload };
}
""")

elif node == "qa":
    if (n == 1 or STUCK) and TID == "T-004":
        # Violacion de propiedad: QA parchea codigo de produccion para que pase.
        p = workdir / "src/domain/matricula.py"
        if p.exists():
            p.write_text(p.read_text(encoding="utf-8")
                         + "\n# parche de QA para que pase la prueba\n", encoding="utf-8")
    w("tests/test_matricula.py", """
import unittest

from src.domain.matricula import SedeDesconocida, SinCupo, renovar


class TestRenovacion(unittest.TestCase):
    def test_SCN_001_renovacion_exitosa(self):
        r = renovar("e1", {"norte": 3}, "norte")
        self.assertEqual(r["estado"], "renovada")

    def test_SCN_002_sede_desconocida_es_error_de_dominio(self):
        with self.assertRaises(SedeDesconocida):
            renovar("e1", {"norte": 3}, "sur")

    def test_sin_cupo(self):
        with self.assertRaises(SinCupo):
            renovar("e1", {"norte": 0}, "norte")


if __name__ == "__main__":
    unittest.main()
""")
    w("spec/40_qa/traceability.md", """
# Trazabilidad escenario -> prueba

| escenario | prueba |
|-----------|--------|
| SCN-001   | tests/test_matricula.py::TestRenovacion::test_SCN_001_renovacion_exitosa |
| SCN-002   | tests/test_matricula.py::TestRenovacion::test_SCN_002_sede_desconocida_es_error_de_dominio |
""")

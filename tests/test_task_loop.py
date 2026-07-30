"""Pruebas del bucle de tareas, de la honestidad del orquestador y de la
resiliencia del proveedor.

Las tres cosas que la corrida real hizo mal y que aqui quedan clavadas:

  1. dev_backend salio con exit=1 (IncompleteRead) y el pipeline lo dio por
     aprobado y avanzo al siguiente nodo.
  2. `git commit` fallo con 'nothing to commit' y el log imprimio
     'APROBADO accion=commit' igualmente.
  3. La llamada al modelo era un unico turno sin reintento ni continuacion: un
     corte de transporte o un truncamiento mataba el nodo entero.

    python -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
from unittest.mock import patch
from http.client import IncompleteRead
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

from sdd.integrations import providers
from sdd.runtime import orchestrator, taskqueue

PY = sys.executable
CFG = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
NODES = {n["id"]: n for n in CFG["node"]}


@contextlib.contextmanager
def sin_ruido():
    """Silencia el log de un componente que esta fallando a proposito.

    Varias pruebas provocan cortes y truncamientos para verificar que se manejan;
    su log por stdout es correcto, pero mezclado con el avance de unittest hace
    que una bateria en verde parezca una bateria rota.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def git_repo(path: Path):
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True,
                   capture_output=True)
    return path


class OrchestratorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wd = git_repo(Path(self._tmp.name))
        self.state, self.spath = orchestrator.load_state(self.wd, "product")
        self.args = argparse.Namespace(workdir=str(self.wd), task="t", simulate=True)
        self._real_invoke = orchestrator.invoke_agent
        self.salida = ""

    def tearDown(self):
        orchestrator.invoke_agent = self._real_invoke
        self._tmp.cleanup()

    def fake_agent(self, rc, detail="", writes=None):
        def _invoke(node, workdir, cfg, simulate, task, visit_id=""):
            for rel, body in (writes or {}).items():
                p = Path(workdir) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            return rc, detail
        orchestrator.invoke_agent = _invoke

    def step(self):
        """Ejecuta una visita de nodo capturando el log del orquestador.

        Estas pruebas provocan fallos a proposito, asi que el orquestador escupe
        DEFECTO y ESCALATE_HUMAN por stdout: es su comportamiento correcto. Sin
        capturarlo, una corrida verde de la bateria se lee como una corrida rota.
        Se guarda en self.salida para poder inspeccionarlo cuando algo falle.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            orchestrator.step(self.state, self.args, CFG, NODES, auto_human=True)
        self.salida += buffer.getvalue()
        return self.salida

    def events(self, name):
        return [e for e in self.state["history"] if e["event"] == name]


class TestHonestidadDelOrquestador(OrchestratorCase):
    """Un agente caido no avanza, y un commit que no ocurre no se reporta."""

    def test_agente_con_exit_distinto_de_cero_no_avanza(self):
        # Este es el fallo original, literal: IncompleteRead a media respuesta.
        self.fake_agent(1, "IncompleteRead: IncompleteRead(32751 bytes read)")
        self.step()

        self.assertEqual(self.state["cursor"], "product", "no debe pasar a architect")
        self.assertEqual(self.events("APROBADO"), [], "no puede aprobar un nodo caido")
        defectos = self.events("DEFECTO")
        self.assertEqual(len(defectos), 1)
        self.assertEqual(defectos[0]["regla"], "agente-fallido")
        self.assertIn("IncompleteRead", defectos[0]["evidencia"])

    def test_agente_caido_sube_modelo_y_luego_escala(self):
        # El escalado de modelo concede UN intento extra, no un presupuesto nuevo:
        # reiniciar attempts convertia max_retries_per_gate en el doble sin tocar
        # pipeline.toml. Y al no converger el estado es 'escalated' (codigo 1), no
        # 'waiting_human', que es la pausa firmada del gate humano y sale con 0.
        self.fake_agent(1, "boom")
        for _ in range(CFG["budget"]["max_retries_per_gate"] + 1):
            self.step()
        self.assertEqual(self.state["status"], "running")
        self.assertTrue(self.events("MODEL_ESCALATION"))

        # Un solo paso mas basta: el contador de reintentos sigue agotado.
        self.step()
        self.assertEqual(self.state["status"], "escalated")
        self.assertTrue(self.events("RECUPERACION_EN_ESPERA"))
        self.assertTrue(self.events("ESCALATE_HUMAN"))

    def test_agente_bloqueado_se_distingue_del_agente_roto(self):
        # exit 3 = el agente dice "me falta un insumo", en vez de inventarse un mock.
        self.fake_agent(3, "falta src/domain/parser.py, lo debe producir dev_backend")
        self.step()
        self.assertEqual(self.events("DEFECTO")[0]["regla"], "agente-bloqueado")

    def test_nodo_sin_cambios_no_se_reporta_como_commit(self):
        # El agente "corre bien" pero no deja nada: G0 lo caza antes de aprobar.
        self.fake_agent(0)
        self.step()
        self.assertEqual(self.events("APROBADO"), [])
        self.assertIn("entregable-ausente",
                      {d["regla"] for d in self.events("DEFECTO")})

    def test_nodo_en_verde_commitea_de_verdad(self):
        self.fake_agent(0, writes={
            "spec/10_product/prd.md": "# PRD\nFR-001 el usuario hace algo.\n",
            "spec/10_product/features/x.feature":
                "Caracteristica: x\n\n  @FR-001 @SCN-001 @p1\n  Escenario: ok\n"
                "    Dado algo\n    Cuando pasa\n    Entonces resulta\n",
        })
        # Esta prueba cubre approve/commit; el contrato de R1 vive en test_revisor.py.
        with patch.object(orchestrator, "run_node_gates", return_value=[]):
            self.step()
        aprobado = self.events("APROBADO")
        self.assertEqual(aprobado[0]["accion"], "commit")
        self.assertEqual(self.state["cursor"], "architect")
        log = subprocess.run(["git", "-C", str(self.wd), "log", "--oneline"],
                             capture_output=True, text=True).stdout
        self.assertIn("docs(product)", log)


class TestCommitAcotado(OrchestratorCase):
    """Cada commit lleva lo de su dueno y nada mas."""

    def test_no_arrastra_el_trabajo_sin_terminar_de_otro_nodo(self):
        (self.wd / "tests").mkdir()
        (self.wd / "tests/test_ajeno.py").write_text("# de QA, aun en rojo\n", encoding="utf-8")
        (self.wd / "spec/10_product").mkdir(parents=True)
        (self.wd / "spec/10_product/prd.md").write_text("# PRD\n", encoding="utf-8")

        ok, _ = orchestrator.commit(self.wd, "docs(product): x", ["spec/10_product/"])
        self.assertTrue(ok)
        files = subprocess.run(["git", "-C", str(self.wd), "show", "--name-only",
                                "--format=", "HEAD"], capture_output=True, text=True).stdout
        self.assertIn("spec/10_product/prd.md", files)
        self.assertNotIn("test_ajeno.py", files, "el commit invadio trabajo de QA")

    def test_sin_cambios_propios_no_hay_commit(self):
        ok, detail = orchestrator.commit(self.wd, "docs(product): x", ["spec/10_product/"])
        self.assertFalse(ok)
        self.assertIn("sin cambios propios", detail)


class TestColaDeTareas(unittest.TestCase):
    """El plan como grafo: dependencias, defectos y desbloqueo."""

    def tasks(self):
        return [
            {"id": "T-001", "node": "dev_backend", "title": "dominio", "fr_refs": ["FR-001"],
             "deliverables": [], "depends_on": [], "acceptance": "", "scope": "",
             "kind": "plan", "status": "pending"},
            {"id": "T-002", "node": "qa", "title": "pruebas", "fr_refs": ["FR-001"],
             "deliverables": [], "depends_on": ["T-001"], "acceptance": "", "scope": "",
             "kind": "plan", "status": "pending"},
        ]

    def test_respeta_las_dependencias(self):
        t = self.tasks()
        self.assertEqual(taskqueue.next_runnable(t)["id"], "T-001")
        taskqueue.mark_done(t, "T-001")
        self.assertEqual(taskqueue.next_runnable(t)["id"], "T-002")

    def test_cola_agotada_devuelve_none(self):
        t = self.tasks()
        taskqueue.mark_done(t, "T-001")
        taskqueue.mark_done(t, "T-002")
        self.assertIsNone(taskqueue.next_runnable(t))

    def test_defecto_bloquea_la_tarea_y_la_libera_al_cerrarse(self):
        t = self.tasks()
        taskqueue.mark_done(t, "T-001")
        qa = taskqueue.by_id(t, "T-002")
        findings = [{"file": "src/domain/x.py", "line": 0, "rule": "suite-roja", "evidence": "e"}]
        d = taskqueue.make_defect(t, "dev_backend", "G9", findings, qa, 1)

        self.assertEqual(qa["status"], "blocked")
        self.assertEqual(qa["blocked_by"], "D-001")
        # El defecto se atiende primero: es lo que desatasca el camino.
        self.assertEqual(taskqueue.next_runnable(t)["id"], "D-001")
        taskqueue.mark_done(t, d["id"])
        self.assertEqual(qa["status"], "pending")
        self.assertEqual(taskqueue.next_runnable(t)["id"], "T-002")

    def test_una_rama_en_espera_no_detiene_tareas_independientes(self):
        tasks = self.tasks() + [{
            "id": "T-003", "node": "dev_frontend", "title": "interfaz",
            "fr_refs": [], "deliverables": [], "depends_on": [],
            "acceptance": "", "scope": "", "kind": "plan", "status": "pending",
        }]
        taskqueue.mark_needs_input(tasks, "T-001", "falta una decision", "G4")

        self.assertEqual(taskqueue.next_runnable(tasks)["id"], "T-003")
        self.assertEqual(taskqueue.by_id(tasks, "T-002")["status"], "pending")
        self.assertEqual(taskqueue.by_id(tasks, "T-001")["status"], "needs_input")

    def test_sin_ramas_ejecutables_escala_sin_fingir_exito(self):
        # Ninguna rama puede avanzar y quedan tareas sin cerrar: es interbloqueo, no
        # pausa. Debe salir con codigo != 0; 'waiting_human' devolveria 0 y una corrida
        # atascada se reportaria como completada.
        tasks = self.tasks()
        taskqueue.mark_needs_input(tasks, "T-001", "requiere dato")
        state = {"tasks": tasks, "status": "running", "current_task": None}
        events = []

        target = orchestrator.enter_task_loop(
            state, ".", lambda _state, event, **fields: events.append((event, fields)))

        self.assertIsNone(target)
        self.assertEqual(state["status"], "escalated")
        self.assertIn("RAMAS_EN_ESPERA", [event for event, _ in events])
        self.assertEqual(events[-1][0], "ESCALATE_HUMAN")

    def test_fallo_lineal_vuelve_al_agente_anterior_y_reanuda_al_dependiente(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"attempts": {}, "status": "running", "cursor": "architect",
                     "defect_seq": 0, "tasks": [], "recoveries": [],
                     "recovery_seq": 0, "resume_stack": []}
            finding = [{"file": "spec/10_product/prd.md", "line": 8,
                        "rule": "criterio-ausente", "evidence": "falta criterio"}]
            events = []
            emit = lambda _state, event, **fields: events.append((event, fields))

            orchestrator.handle_defect(
                state, tmp, {"id": "architect"}, None, "product", "G2",
                finding, {"max_retries_per_gate": 2, "max_defect_tasks": 12}, emit)

            self.assertEqual(state["cursor"], "product")
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["recoveries"][0]["failed_node"], "architect")
            self.assertEqual(state["recoveries"][0]["owner"], "product")
            current = json.loads((Path(tmp) / ".agent/current_task.json").read_text())
            self.assertEqual(current["kind"], "defect")
            self.assertIn("falta criterio", current["findings"][0]["evidence"])

            with mock.patch.object(orchestrator, "commit", return_value=(True, "ok")):
                orchestrator.approve(
                    state, tmp, {"id": "product", "next": "architect", "writes": []},
                    None, emit)

            self.assertEqual(state["cursor"], "architect")
            self.assertEqual(state["recoveries"][0]["status"], "corrected")
            self.assertIn("CORRECCION_RECIBIDA", [event for event, _ in events])

    def test_agotar_reintentos_lineales_sube_modelo_antes_de_pedir_ayuda(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(orchestrator.config, "load", return_value={
                    "routing": {"max_frontier_escalations_per_task": 1}}):
            state = {"attempts": {"architect:G2": 2}, "status": "running",
                     "cursor": "architect", "defect_seq": 0, "tasks": [],
                     "recoveries": [], "recovery_seq": 0, "resume_stack": []}
            finding = [{"file": "spec/20_arch/nfr.yaml", "line": 1,
                        "rule": "nfr-no-medible", "evidence": "falta metrica"}]
            emit = lambda *_args, **_kwargs: None
            budget = {"max_retries_per_gate": 2, "max_defect_tasks": 12}

            orchestrator.handle_defect(
                state, tmp, {"id": "architect"}, None, "architect", "G2",
                finding, budget, emit)

            self.assertEqual(state["status"], "running")
            self.assertTrue(state["recoveries"][0]["model_escalated"])
            # El escalado NO reinicia el contador de reintentos: hacerlo relajaria
            # max_retries_per_gate sin tocar pipeline.toml. El intento en el que se
            # escala ya esta consumido.
            self.assertEqual(state["attempts"]["architect:G2"], 3)

            state["attempts"]["architect:G2"] = 2
            orchestrator.handle_defect(
                state, tmp, {"id": "architect"}, None, "architect", "G2",
                finding, budget, emit)
            # Agotado el unico escalado permitido: escalated (codigo 1), no la pausa
            # firmada waiting_human (codigo 0).
            self.assertEqual(state["status"], "escalated")
            self.assertEqual(state["recoveries"][0]["status"], "needs_input")

    def test_reintentos_de_tarea_crean_correccion_sin_escalar_proyecto(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {"id": "T-100", "node": "dev_backend", "title": "api",
                    "status": "pending", "kind": "plan", "fr_refs": [],
                    "deliverables": [], "depends_on": [], "acceptance": "", "scope": ""}
            state = {"attempts": {"T-100:G4": 2}, "status": "running",
                     "cursor": "dev_backend", "defect_seq": 0, "tasks": [task]}
            finding = [{"file": "src/api/a.py", "line": 2,
                        "rule": "lint", "evidence": "fallo"}]

            orchestrator.handle_defect(
                state, tmp, {"id": "dev_backend"}, task, "dev_backend", "G4",
                finding, {"max_retries_per_gate": 2, "max_defect_tasks": 12},
                lambda *_args, **_kwargs: None)

            self.assertEqual(state["status"], "running")
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(task["blocked_by"], "D-001")
            self.assertEqual(taskqueue.by_id(state["tasks"], "D-001")["node"],
                             "dev_backend")

    def test_defectos_encadenados_se_cierran_sin_reactivar_trabajo_obsoleto(self):
        t = self.tasks()
        taskqueue.mark_done(t, "T-001")
        qa = taskqueue.by_id(t, "T-002")
        findings = [{"file": "src/api/main.py", "line": 1,
                     "rule": "typecheck-rojo", "evidence": "e"}]
        parent = taskqueue.make_defect(
            t, "dev_backend", "G9", findings, qa, 1)
        child = taskqueue.make_defect(
            t, "dev_backend", "G9", findings, parent, 2)

        taskqueue.mark_done(t, child["id"])

        self.assertEqual(child["status"], "done")
        self.assertEqual(parent["status"], "done")
        self.assertNotIn("blocked_by", parent)
        self.assertEqual(qa["status"], "pending")
        self.assertNotIn("blocked_by", qa)
        self.assertEqual(taskqueue.next_runnable(t)["id"], "T-002")

    def test_reanudar_reconcilia_hijo_integrado_antes_del_checkpoint(self):
        t = self.tasks()
        taskqueue.mark_done(t, "T-001")
        qa = taskqueue.by_id(t, "T-002")
        findings = [{"file": "src/api/main.py", "line": 1,
                     "rule": "typecheck-rojo", "evidence": "e"}]
        parent = taskqueue.make_defect(
            t, "dev_backend", "G9", findings, qa, 1)
        child = taskqueue.make_defect(
            t, "dev_backend", "G9", findings, parent, 2)
        child["status"] = "done"
        parent["status"] = "pending"
        parent.pop("blocked_by", None)

        reconciled = taskqueue.reconcile_completed_defects(t)

        self.assertEqual(reconciled, ["D-001"])
        self.assertEqual(parent["status"], "done")
        self.assertEqual(qa["status"], "pending")
        self.assertEqual(taskqueue.next_runnable(t)["id"], "T-002")

    def test_fallo_de_instalacion_se_enruta_al_arquitecto_no_a_qa(self):
        # La corrida real escalo porque `npm ci` fallaba y el defecto sobre
        # package.json iba a QA (que no lo posee), gastando reintentos. Ahora el
        # arquitecto posee package.json, asi que route() lo manda a su dueno real.
        pkg_finding = [{"file": "package.json", "line": 0, "rule": "instalacion-fallida",
                        "evidence": "npm install salio 1"}]
        report = {"gate_id": "G9", "node": "qa", "status": "fail", "route_by": "path",
                  "default_owner": "qa", "findings": pkg_finding}
        owner, gate, _ = orchestrator.route([report], CFG)
        self.assertEqual(owner, "architect", "package.json es del arquitecto, no de QA")

    def test_mensaje_de_commit_referencia_fr_y_task_id(self):
        t = self.tasks()[0]
        msg = taskqueue.commit_message("dev_backend", t)
        self.assertTrue(msg.startswith("feat("))
        self.assertIn("FR-001", msg)
        self.assertIn("T-001", msg)
        self.assertTrue(taskqueue.commit_message("qa", self.tasks()[1]).startswith("test("))


class TestResilienciaDelProveedor(unittest.TestCase):
    """El corte de transporte se reintenta; el truncamiento se continua."""

    def test_incomplete_read_se_reconoce_como_transitorio(self):
        self.assertTrue(providers._is_transient(IncompleteRead(b"")))

    def test_error_envuelto_tambien_se_reconoce(self):
        try:
            try:
                raise IncompleteRead(b"")
            except IncompleteRead as e:
                raise RuntimeError("el SDK lo envolvio") from e
        except RuntimeError as wrapped:
            self.assertTrue(providers._is_transient(wrapped))

    def test_error_de_logica_no_se_reintenta(self):
        self.assertFalse(providers._is_transient(ValueError("prompt invalido")))

    def test_continuacion_empalma_una_respuesta_truncada(self):
        trozos = [("<<<FILE: a.py>>>\nprimera", True),
                  (" parte\n<<<END>>>", False)]
        llamadas = []

        def call(prefill):
            llamadas.append(prefill)
            return trozos[len(llamadas) - 1]

        with sin_ruido():
            texto = providers._continue_until_complete(call)
        self.assertIn("<<<END>>>", texto)
        self.assertEqual(llamadas[0], "", "la primera llamada no lleva prefill")
        self.assertIn("primera", llamadas[1], "la segunda retoma lo ya escrito")

    def test_truncamiento_perpetuo_falla_con_mensaje_util(self):
        original = providers.MAX_CONTINUATIONS
        providers.MAX_CONTINUATIONS = 2
        try:
            with self.assertRaises(providers.ProviderError) as ctx, sin_ruido():
                providers._continue_until_complete(lambda prefill: ("x", True))
            self.assertIn("divide el plan", str(ctx.exception))
        finally:
            providers.MAX_CONTINUATIONS = original

    def test_reintento_agotado_produce_ProviderError_clasificado(self):
        original = providers.MAX_RETRIES, providers.BACKOFF_BASE_S
        providers.MAX_RETRIES, providers.BACKOFF_BASE_S = 2, 0
        try:
            def siempre_falla():
                raise IncompleteRead(b"")
            with self.assertRaises(providers.ProviderError) as ctx, sin_ruido():
                providers._with_retry(siempre_falla, "prueba")
            self.assertIn("IncompleteRead", str(ctx.exception))
        finally:
            providers.MAX_RETRIES, providers.BACKOFF_BASE_S = original


class TestCorridaCompleta(unittest.TestCase):
    """El demo entero: de la idea a una suite que se ejecuta en verde."""

    def test_pipeline_simulado_converge_con_la_suite_ejecutada(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            proc = subprocess.run(
                [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd),
                 "--simulate", "--autonomous"], capture_output=True, text=True)
            salida = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, salida[-3000:])
            self.assertIn("estado final: done", salida)
            self.assertIn("tareas: 5/5", salida)
            # G9 verde al final = la suite del proyecto generado corrio de verdad.
            self.assertIn("[GATE G9           ] estado=pass", salida)
            # Y el camino incluyo un defecto delegado a otro nodo.
            self.assertIn("DEFECTO_TAREA", salida)
            self.assertTrue((wd / "src/domain/matricula.py").exists())
            self.assertTrue((wd / "tests/test_matricula.py").exists())

    def test_gate_humano_para_tras_el_plan_y_se_reanuda(self):
        # Sin --from explicito, load_state ignoraba el argumento cuando ya existia
        # state.json: el gate humano volvia a pararse en el mismo sitio y no habia
        # forma de continuar la corrida.
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            base = [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd), "--simulate"]

            parada = subprocess.run(base, capture_output=True, text=True)
            self.assertIn("estado final: waiting_human", parada.stdout)
            self.assertTrue((wd / "spec/30_plan/tasks.yaml").exists(),
                            "el humano debe poder revisar el plan, no solo la spec")
            self.assertFalse((wd / "src").exists(), "no se escribe codigo antes de la firma")

            sigue = subprocess.run(base + ["--from", "task_loop"],
                                   capture_output=True, text=True)
            self.assertEqual(sigue.returncode, 0, (sigue.stdout + sigue.stderr)[-2000:])
            self.assertIn("tareas: 5/5", sigue.stdout)


class TestReanudar(unittest.TestCase):
    """--resume: retomar sin perder lo commiteado, tras un corte o una escalada."""

    def _crashable_repo(self, tmp):
        # Repo con agente atascado: escalara (agota reintentos). Simula una corrida
        # que no llego a completar y que el usuario quiere continuar.
        wd = git_repo(Path(tmp))
        return wd

    def test_resume_sin_estado_previo_falla_claro(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            proc = subprocess.run(
                [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd), "--resume"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("no hay corrida que reanudar", proc.stdout)

    def test_resume_escalado_en_dispatch_vuelve_al_scheduler(self):
        state = {
            "status": "escalated", "cursor": "parallel_dispatch",
            "attempts": {"T-003:G9": 3}, "resume_at": "qa",
            "parallel_batch": None, "parallel_results": {"stale": {}},
            "worker_task_id": "T-003", "current_task": "T-003",
        }

        previous = orchestrator.prepare_resume(state)

        self.assertEqual(previous, "escalated")
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["cursor"], "task_loop")
        self.assertEqual(state["attempts"], {})
        self.assertIsNone(state["parallel_batch"])
        self.assertEqual(state["parallel_results"], {})
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["resume_checkpoint"]["from_node"], "parallel_dispatch")
        self.assertEqual(state["resume_history"][-1]["attempts"], {"T-003:G9": 3})

    def test_resume_lineal_conserva_el_nodo_exacto_que_fallo(self):
        state = {
            "status": "escalated", "cursor": "architect",
            "attempts": {"architect:G2": 3}, "current_task": None,
        }

        previous = orchestrator.prepare_resume(state)

        self.assertEqual(previous, "escalated")
        self.assertEqual(state["cursor"], "architect")
        self.assertEqual(state["resume_checkpoint"]["from_node"], "architect")
        self.assertEqual(state["resume_history"][-1]["attempts"],
                         {"architect:G2": 3})
        self.assertEqual(state["attempts"], {})

    def test_resume_antiguo_reconstruye_y_publica_la_correccion_del_arquitecto(self):
        findings = [
            "NFR-USAB-01 sin campo metrica",
            "NFR-USAB-02 sin campo metrica",
            "NFR-SEC-01 sin campo metrica",
            "NFR-SEC-02 sin campo metrica",
            "NFR-SEC-03 sin campo metrica",
        ]
        state = {
            "status": "escalated", "cursor": "architect", "started_at": 1,
            "attempts": {"architect:G2": 3}, "current_task": None, "tasks": [],
            "history": [
                *[{"event": "DEFECTO", "gate": "G2", "owner": "architect",
                   "ubicacion": "spec/20_arch/nfr.yaml:0",
                   "regla": "nfr-no-medible", "evidencia": item}
                  for item in findings],
                {"event": "ESCALATE_HUMAN",
                 "motivo": "architect:G2 fallo 3 veces"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator.prepare_resume(state, tmp)
            published = json.loads(
                (Path(tmp) / ".agent/current_task.json").read_text(encoding="utf-8"))

        self.assertGreater(state["started_at"], 1)
        self.assertEqual(state["original_started_at"], 1)
        self.assertEqual(state["resume_recovery"]["owner"], "architect")
        self.assertEqual(state["resume_recovery"]["findings"], 5)
        self.assertEqual(state["recoveries"][0]["status"], "assigned")
        self.assertTrue(state["recoveries"][0]["model_escalated"])
        self.assertEqual(published["node"], "architect")
        self.assertEqual(published["kind"], "defect")
        self.assertEqual(len(published["findings"]), 5)

    def test_save_reintenta_colision_temporal_de_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".agent/state.json"
            original_replace = Path.replace
            calls = []

            def flaky_replace(source, target):
                calls.append(str(source))
                if len(calls) < 3:
                    raise PermissionError("archivo ocupado")
                return original_replace(source, target)

            with patch.object(Path, "replace", flaky_replace), \
                    patch.object(orchestrator.time, "sleep"):
                orchestrator.save({"status": "running"}, path)

            self.assertEqual(len(calls), 3)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["status"], "running")
            self.assertFalse(list(path.parent.glob(".state.json.*.tmp")))

    def test_resume_running_rancio_en_dispatch_vuelve_al_scheduler(self):
        state = {
            "status": "running", "cursor": "parallel_dispatch",
            "attempts": {}, "parallel_batch": None,
            "parallel_results": {}, "current_task": None,
        }

        previous = orchestrator.prepare_resume(state)

        self.assertEqual(previous, "running")
        self.assertEqual(state["cursor"], "task_loop")

    def test_resume_continua_una_corrida_escalada_sin_perder_commits(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            wd = git_repo(Path(tmp))
            env_stuck = {**_os.environ, "SDD_FAKE_STUCK": "1"}
            base = [PY, "-m", "sdd.runtime.orchestrator", "--workdir", str(wd),
                    "--simulate", "--autonomous"]
            # 1) Corrida que espera ayuda: product no logra corregir por si solo.
            esc = subprocess.run(base, capture_output=True, text=True, env=env_stuck)
            # No convergio: escalated (codigo 1). waiting_human se reserva para la
            # pausa firmada del gate humano, que sale con codigo 0.
            self.assertIn("estado final: escalated", esc.stdout)
            commits_antes = subprocess.run(
                ["git", "-C", str(wd), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True).stdout.strip()

            # 2) Sin --resume, relanzar se rechaza (no finge exito).
            rej = subprocess.run(base, capture_output=True, text=True)
            # Sin acentos: la consola de Windows captura cp1252 y el 'á' no calza.
            self.assertIn("en estado 'escalated'", rej.stdout)

            # 3) Con --resume y ya sin el fallo, continua y completa; el historial
            #    (commits) previo se conserva, no se reinicia. Además emulamos
            #    un checkpoint anterior a la lógica de recuperaciones: debe
            #    reconstruir la tarea y no caducar por la fecha de la corrida.
            state_path = wd / ".agent/state.json"
            old_state = json.loads(state_path.read_text(encoding="utf-8"))
            old_state["started_at"] = 1
            old_state.pop("recoveries", None)
            old_state.pop("recovery_seq", None)
            old_state.pop("resume_stack", None)
            state_path.write_text(json.dumps(old_state), encoding="utf-8")
            current_task = wd / ".agent/current_task.json"
            current_task.unlink(missing_ok=True)
            ok = subprocess.run(base + ["--resume"], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, (ok.stdout + ok.stderr)[-2000:])
            self.assertIn("REANUDADO", ok.stdout)
            self.assertIn("RECUPERACION_RESTAURADA", ok.stdout)
            self.assertNotIn("max_wall_minutes agotado", ok.stdout)
            self.assertIn("estado final: done", ok.stdout)
            self.assertIn("tareas: 5/5", ok.stdout)
            commits_despues = subprocess.run(
                ["git", "-C", str(wd), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            self.assertGreaterEqual(int(commits_despues), int(commits_antes),
                                    "reanudar no debe perder los commits previos")


if __name__ == "__main__":
    unittest.main()

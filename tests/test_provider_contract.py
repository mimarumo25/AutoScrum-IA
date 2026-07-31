"""Contrato del payload enviado al proveedor, sin llamar a la API.

Este archivo cubre el hueco que dejo pasar tres defectos a la vez: no habia ninguna
prueba que mirara el payload real que se manda a Anthropic. Los modelos de generacion
actual retiraron parametros que antes eran normales, y enviarlos ya no se ignora
—devuelve HTTP 400—, asi que el pipeline no podia completar un solo nodo con el
proveedor por defecto. Nada de esto necesita red: se inspecciona el payload.

    python -m unittest discover -s tests
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent / "sdd"
sys.path.insert(0, str(ROOT))

from sdd.integrations import providers


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 10
    output_tokens = 20
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Message:
    def __init__(self, text="hola", stop_reason="end_turn", stop_details=None):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = _Usage()


class _Stream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        return self._message


def fake_anthropic(messages_queue):
    """Modulo anthropic falso que registra cada payload y devuelve respuestas dadas."""
    captured = []

    class _Messages:
        def stream(self, **kwargs):
            captured.append(kwargs)
            return _Stream(messages_queue[min(len(captured) - 1,
                                              len(messages_queue) - 1)])

    class _Client:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _Client
    return module, captured


class ProviderContractCase(unittest.TestCase):
    def call_anthropic(self, model, responses, env=None):
        module, captured = fake_anthropic(responses)
        environ = {"ANTHROPIC_API_KEY": "k", "SDD_MODEL": model,
                   "SDD_PROMPT_CACHE": "0", **(env or {})}
        with mock.patch.dict(sys.modules, {"anthropic": module}), \
                mock.patch.dict("os.environ", environ, clear=False):
            text = providers.complete("sistema", "usuario")
        return text, captured


class TestParametrosDeMuestreo(ProviderContractCase):
    """temperature/top_p/top_k devuelven 400 en los modelos actuales."""

    def test_no_envia_temperature_al_modelo_por_defecto(self):
        # claude-opus-5 es el default del paquete: si se le manda temperature, la
        # primera llamada del primer nodo falla con 400 y la corrida no arranca.
        _text, captured = self.call_anthropic("claude-opus-5", [_Message()])
        self.assertNotIn("temperature", captured[0])
        self.assertNotIn("top_p", captured[0])
        self.assertNotIn("top_k", captured[0])

    def test_no_envia_temperature_en_ningun_modelo_ofrecido_que_lo_rechace(self):
        for model in ("claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"):
            with self.subTest(model=model):
                _text, captured = self.call_anthropic(model, [_Message()])
                self.assertNotIn("temperature", captured[0])

    def test_si_lo_envia_donde_sigue_siendo_valido(self):
        # No es "quitarlo siempre": en los modelos que lo aceptan se conserva, para
        # que el ajuste por perfil de agente siga sirviendo donde aplica.
        _text, captured = self.call_anthropic("claude-haiku-4-5", [_Message()])
        self.assertIn("temperature", captured[0])

    def test_modelo_desconocido_cae_en_el_camino_seguro(self):
        # Omitir el parametro es valido en toda generacion; enviarlo no. Un modelo
        # nuevo puesto a mano en SDD_MODEL debe omitirlo.
        _text, captured = self.call_anthropic("modelo-que-no-existe-aun", [_Message()])
        self.assertNotIn("temperature", captured[0])

    def test_el_sufijo_de_fecha_no_rompe_la_deteccion(self):
        _text, captured = self.call_anthropic("claude-haiku-4-5-20251001", [_Message()])
        self.assertIn("temperature", captured[0])


class TestContinuacionSinPrefill(ProviderContractCase):
    """El prefill del ultimo turno de asistente tambien devuelve 400."""

    def test_continuacion_no_usa_turno_de_asistente(self):
        truncada = _Message("primera parte", stop_reason="max_tokens")
        final = _Message(" y el resto", stop_reason="end_turn")
        text, captured = self.call_anthropic("claude-opus-5", [truncada, final])

        self.assertEqual(len(captured), 2, "debe haber continuado")
        roles = [m["role"] for m in captured[1]["messages"]]
        self.assertNotIn("assistant", roles,
                         "un prefill de asistente devuelve 400 en este modelo")
        self.assertIn("primera parte", text)
        self.assertIn("el resto", text)

    def test_la_continuacion_lleva_la_cola_para_poder_empalmar(self):
        truncada = _Message("bloque A", stop_reason="max_tokens")
        final = _Message("bloque B", stop_reason="end_turn")
        _text, captured = self.call_anthropic("claude-opus-5", [truncada, final])
        continuacion = captured[1]["messages"][-1]["content"]
        self.assertIn("bloque A", continuacion)
        self.assertIn("<<<FILE:>>>", continuacion,
                      "debe recordar el protocolo de bloques al empalmar")

    def test_conserva_el_prefill_donde_sigue_soportado(self):
        truncada = _Message("mitad", stop_reason="max_tokens")
        final = _Message(" final", stop_reason="end_turn")
        _text, captured = self.call_anthropic("claude-haiku-4-5", [truncada, final])
        self.assertEqual(captured[1]["messages"][-1]["role"], "assistant")


class TestNegativaYRespuestaVacia(ProviderContractCase):
    """Una negativa llega con HTTP 200; sin comprobarla se vuelve un fallo invisible."""

    def test_refusal_falla_en_vez_de_devolver_vacio(self):
        negativa = _Message(None, stop_reason="refusal",
                            stop_details=types.SimpleNamespace(category="cyber"))
        with self.assertRaises(providers.ProviderError) as ctx:
            self.call_anthropic("claude-opus-5", [negativa])
        self.assertIn("refusal", str(ctx.exception))
        self.assertIn("cyber", str(ctx.exception))

    def test_respuesta_vacia_sin_error_declarado_tambien_falla(self):
        # Vale para cualquier proveedor: devolver "" como respuesta completa deja al
        # agente sin nada que escribir y saliendo con 0.
        vacia = _Message("", stop_reason="end_turn")
        with self.assertRaises(providers.ProviderError):
            self.call_anthropic("claude-opus-5", [vacia])


class TestClasificacionDeErrores(unittest.TestCase):
    def test_409_no_es_transitorio(self):
        # Un Conflict es semantico: reintentarlo repite la misma peticion invalida.
        self.assertNotIn(409, providers.TRANSIENT_STATUS)

    def test_429_y_5xx_si_son_transitorios(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertIn(status, providers.TRANSIENT_STATUS)


class TestBackoff(unittest.TestCase):
    def test_respeta_retry_after_del_proveedor(self):
        error = types.SimpleNamespace(
            response=types.SimpleNamespace(headers={"retry-after": "7"}),
            __cause__=None, __context__=None)
        delay, source = providers._backoff_delay(1, error)
        self.assertEqual(delay, 7.0)
        self.assertEqual(source, "Retry-After")

    def test_sin_retry_after_aplica_jitter(self):
        error = types.SimpleNamespace(__cause__=None, __context__=None)
        muestras = {providers._backoff_delay(3, error)[0] for _ in range(40)}
        self.assertGreater(len(muestras), 1,
                           "sin jitter, N workers reintentan sincronizados y "
                           "convierten un 429 en una tormenta")
        techo = providers.BACKOFF_BASE_S * (2 ** 2)
        for valor in muestras:
            self.assertGreaterEqual(valor, techo / 2)
            self.assertLessEqual(valor, techo)

    def test_una_fecha_http_en_retry_after_no_se_malinterpreta(self):
        error = types.SimpleNamespace(
            response=types.SimpleNamespace(
                headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            __cause__=None, __context__=None)
        delay, source = providers._backoff_delay(1, error)
        self.assertEqual(source, "backoff+jitter")
        self.assertGreater(delay, 0)


class TestBreakpointDeCache(ProviderContractCase):
    """El breakpoint de cache iba como parametro top-level de la request.

    No existe tal parametro en la Messages API: el breakpoint se declara dentro
    de un bloque de contenido. El fallo no daba error, solo 0 aciertos de cache
    en 310 registros de usage.jsonl — el peor tipo de bug, el que parece
    funcionar.
    """

    def test_el_breakpoint_va_en_el_bloque_de_system(self):
        _, captured = self.call_anthropic("claude-opus-5", [_Message()],
                                          env={"SDD_PROMPT_CACHE": "1"})
        system = captured[0]["system"]
        self.assertIsInstance(system, list)
        self.assertEqual(system[0]["type"], "text")
        self.assertEqual(system[0]["text"], "sistema")
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_nunca_va_en_el_nivel_superior(self):
        """La regresion concreta: `kwargs["cache_control"] = ...`."""
        for cache in ("0", "1"):
            with self.subTest(SDD_PROMPT_CACHE=cache):
                _, captured = self.call_anthropic("claude-opus-5", [_Message()],
                                                  env={"SDD_PROMPT_CACHE": cache})
                self.assertNotIn("cache_control", captured[0])

    def test_desactivarlo_devuelve_system_como_cadena(self):
        _, captured = self.call_anthropic("claude-opus-5", [_Message()],
                                          env={"SDD_PROMPT_CACHE": "0"})
        self.assertEqual(captured[0]["system"], "sistema")

    def test_el_contexto_volatil_queda_despues_del_breakpoint(self):
        """Si el contexto que cambia en cada llamada entrara antes del
        breakpoint, el prefijo no casaria nunca y el cache seria inutil."""
        _, captured = self.call_anthropic("claude-opus-5", [_Message()],
                                          env={"SDD_PROMPT_CACHE": "1"})
        self.assertEqual(captured[0]["messages"][0]["content"], "usuario")


class TestCamposDeCacheOpenAI(unittest.TestCase):
    """Los tokens de cache de los proveedores OpenAI-compatibles se descartaban,
    asi que el ahorro de los nodos dev_* era invisible en usage.jsonl."""

    def test_campo_plano(self):
        self.assertEqual(
            providers._openai_cache_fields(
                "deepseek", {"prompt_tokens": 100, "prompt_cache_hit_tokens": 64}),
            (64, 0))

    def test_campo_anidado_estilo_openai(self):
        self.assertEqual(
            providers._openai_cache_fields(
                "openai", {"prompt_tokens": 100,
                           "prompt_tokens_details": {"cached_tokens": 32}}),
            (32, 0))

    def test_sin_campos_de_cache_registra_las_claves_reales(self):
        """No se adivina el nombre: si ningun candidato aparece, se deja
        constancia de lo que el proveedor SI devolvio para que una corrida real
        lo confirme en vez de que el codigo lo suponga para siempre."""
        with mock.patch.object(providers.metrics, "record") as record:
            with mock.patch.dict("os.environ",
                                 {"SDD_METRICS_WORKDIR": "/tmp/x"}, clear=False):
                read, write = providers._openai_cache_fields(
                    "glm", {"prompt_tokens": 10, "algo_desconocido": 5})
        self.assertEqual((read, write), (0, 0))
        record.assert_called_once()
        self.assertEqual(record.call_args.args[1], "provider_usage_sin_cache")
        self.assertIn("algo_desconocido", record.call_args.kwargs["keys"])

    def test_usage_vacio_no_registra_nada(self):
        with mock.patch.object(providers.metrics, "record") as record:
            self.assertEqual(providers._openai_cache_fields("glm", {}), (0, 0))
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()

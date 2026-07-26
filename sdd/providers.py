#!/usr/bin/env python3
"""Capa de proveedores LLM para los agentes reales del pipeline.

Interfaz unica: complete(system, user) -> str.

Proveedor seleccionado por SDD_PROVIDER (default: anthropic). Cada proveedor
lee su API key de su propia variable de entorno; si falta, FALLA AL ARRANCAR con
un mensaje claro (nunca un default silencioso) — como manda CLAUDE.md.

  anthropic  -> API de Anthropic (SDK oficial). Key: ANTHROPIC_API_KEY
  deepseek   -> DeepSeek (compat. OpenAI).       Key: DEEPSEEK_API_KEY
  qwen       -> Alibaba Qwen / DashScope.        Key: DASHSCOPE_API_KEY
  glm        -> Zhipu GLM.                       Key: ZHIPUAI_API_KEY
  kimi       -> Moonshot Kimi.                   Key: MOONSHOT_API_KEY
  openai     -> cualquier endpoint compat. OpenAI. SDD_BASE_URL + SDD_API_KEY + SDD_MODEL

Modelo: SDD_MODEL sobreescribe el default de cada proveedor.
"""
import json
import os
import time
import urllib.error
import urllib.request

import metrics


class ProviderError(RuntimeError):
    pass


# Uso de tokens de la ULTIMA llamada a complete(). El presupuesto contaba solo
# llamadas; esto permite ademas contar tokens (y, aguas arriba, coste). Se acumula
# a traves de las continuaciones para no subcontar una respuesta larga.
_LAST_USAGE = {"input_tokens": 0, "output_tokens": 0, "calls": 0,
               "cache_read_input_tokens": 0,
               "cache_creation_input_tokens": 0}


def last_usage() -> dict:
    return dict(_LAST_USAGE)


def _reset_usage():
    _LAST_USAGE.update(input_tokens=0, output_tokens=0, calls=0,
                       cache_read_input_tokens=0,
                       cache_creation_input_tokens=0)


def _add_usage(input_tokens, output_tokens, cache_read=0, cache_creation=0):
    _LAST_USAGE["input_tokens"] += int(input_tokens or 0)
    _LAST_USAGE["output_tokens"] += int(output_tokens or 0)
    _LAST_USAGE["calls"] += 1
    _LAST_USAGE["cache_read_input_tokens"] += int(cache_read or 0)
    _LAST_USAGE["cache_creation_input_tokens"] += int(cache_creation or 0)


# --- Resiliencia de la llamada ---------------------------------------------
# La corrida que motivo esto murio con IncompleteRead(32751 bytes read): el stream
# se corto a media respuesta, el agente salio con exit=1 y el pipeline siguio como
# si nada. Dos defensas distintas para dos fallos distintos:
#   - corte de transporte  -> reintento con backoff (es transitorio)
#   - respuesta truncada por max_tokens -> continuacion (no es un error: el modelo
#     tenia mas que decir). Se le devuelve lo ya emitido como prefill y sigue.

MAX_TOKENS = int(os.environ.get("SDD_MAX_TOKENS", "16000"))
MAX_CONTINUATIONS = int(os.environ.get("SDD_MAX_CONTINUATIONS", "6"))
MAX_RETRIES = int(os.environ.get("SDD_MAX_RETRIES", "4"))
BACKOFF_BASE_S = float(os.environ.get("SDD_BACKOFF_BASE_S", "2"))

TRANSIENT_NAMES = {
    "IncompleteRead", "APIConnectionError", "APITimeoutError", "APIStatusError",
    "RemoteDisconnected", "ConnectionResetError", "ConnectionAbortedError",
    "ProtocolError", "ChunkedEncodingError", "ReadTimeout", "TimeoutError",
    "URLError", "socket.timeout", "BadStatusLine",
    "InternalServerError", "RateLimitError", "OverloadedError", "ServiceUnavailable",
}
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    """Recorre la cadena de causas: los SDK envuelven el error de transporte."""
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in TRANSIENT_NAMES:
            return True
        if getattr(cur, "status_code", None) in TRANSIENT_STATUS:
            return True
        if getattr(getattr(cur, "response", None), "status_code", None) in TRANSIENT_STATUS:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _with_retry(fn, what: str):
    """Ejecuta fn con backoff exponencial mientras el fallo sea transitorio."""
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 — se reclasifica abajo
            last = e
            if not _is_transient(e) or attempt == MAX_RETRIES:
                break
            delay = BACKOFF_BASE_S * (2 ** (attempt - 1))
            print(f"  [provider] {what}: {type(e).__name__} — reintento "
                  f"{attempt}/{MAX_RETRIES - 1} en {delay:.0f}s", flush=True)
            time.sleep(delay)
    raise ProviderError(f"{what} fallo tras {MAX_RETRIES} intento(s): "
                        f"{type(last).__name__}: {last}") from last


def _continue_until_complete(call, seed_prefill=""):
    """Acumula la respuesta a traves de continuaciones hasta que deje de truncarse.

    `call(prefill) -> (texto, truncado)`. Cuando el modelo corta por limite de
    tokens se le reenvia lo ya escrito como turno de asistente y retoma ahi; el
    protocolo de bloques <<<FILE:>>> sobrevive intacto al empalme.
    """
    acc = seed_prefill
    for i in range(MAX_CONTINUATIONS + 1):
        text, truncated = call(acc)
        acc = (acc + text).rstrip()
        if not truncated:
            return acc
        if i < MAX_CONTINUATIONS:
            print(f"  [provider] respuesta truncada por limite de tokens; "
                  f"continuacion {i + 1}/{MAX_CONTINUATIONS}", flush=True)
    raise ProviderError(
        f"la respuesta seguia truncada tras {MAX_CONTINUATIONS} continuaciones "
        f"({len(acc)} caracteres). La tarea es demasiado grande: divide el plan.")


# Proveedores compatibles con la API de OpenAI (los modelos chinos lo son).
# DeepSeek: se usa el endpoint /beta, no /v1. El /v1 rechaza el campo `prefix`
# (HTTP 400 "prefix is only available when using beta api"), que la continuacion
# ante truncamiento necesita para retomar una respuesta cortada. El /beta es un
# superconjunto: las llamadas normales funcionan igual y ademas soporta prefix.
OPENAI_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/beta",
                 "key_env": "DEEPSEEK_API_KEY", "model": "deepseek-v4-flash"},
    "qwen":     {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                 "key_env": "DASHSCOPE_API_KEY", "model": "qwen-max"},
    "glm":      {"base_url": "https://open.bigmodel.cn/api/paas/v4",
                 "key_env": "ZHIPUAI_API_KEY", "model": "glm-4-plus"},
    "kimi":     {"base_url": "https://api.moonshot.cn/v1",
                 "key_env": "MOONSHOT_API_KEY", "model": "moonshot-v1-32k"},
}

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"

# Modelos seleccionables por proveedor (para el dropdown de la interfaz). El
# primero de cada lista es el default. 'openai' es libre: se escribe SDD_MODEL.
MODEL_CHOICES = {
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"],
    "deepseek":  ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    "qwen":      ["qwen-max", "qwen-plus", "qwen-turbo"],
    "glm":       ["glm-4-plus", "glm-4", "glm-4-air"],
    "kimi":      ["moonshot-v1-32k", "moonshot-v1-128k", "moonshot-v1-8k"],
    "openai":    [],
}


def default_model(provider: str) -> str:
    choices = MODEL_CHOICES.get(provider, [])
    return choices[0] if choices else ""


def current_provider():
    return os.environ.get("SDD_PROVIDER", "anthropic").lower()


def _require_env(name, provider):
    val = os.environ.get(name)
    if not val:
        raise ProviderError(
            f"falta la variable de entorno {name}, requerida por el proveedor "
            f"'{provider}'. Definela y reintenta (fail-fast, sin default silencioso).")
    return val


def describe():
    """Config efectiva del proveedor actual, para el comando `doctor` (sin secretos)."""
    p = current_provider()
    if p == "anthropic":
        return {"provider": p, "model": os.environ.get("SDD_MODEL", ANTHROPIC_DEFAULT_MODEL),
                "key_env": "ANTHROPIC_API_KEY", "key_present": bool(os.environ.get("ANTHROPIC_API_KEY"))}
    if p == "openai":
        return {"provider": p, "model": os.environ.get("SDD_MODEL", "(SDD_MODEL sin definir)"),
                "base_url": os.environ.get("SDD_BASE_URL", "(SDD_BASE_URL sin definir)"),
                "key_env": "SDD_API_KEY", "key_present": bool(os.environ.get("SDD_API_KEY"))}
    if p in OPENAI_PRESETS:
        cfg = OPENAI_PRESETS[p]
        return {"provider": p, "model": os.environ.get("SDD_MODEL", cfg["model"]),
                "base_url": os.environ.get("SDD_BASE_URL", cfg["base_url"]),
                "key_env": cfg["key_env"], "key_present": bool(os.environ.get(cfg["key_env"]))}
    return {"provider": p, "error": "proveedor desconocido"}


def complete(system: str, user: str) -> str:
    _reset_usage()
    p = current_provider()
    started = time.perf_counter()
    outcome = "ok"
    try:
        if p == "anthropic":
            return _anthropic(system, user)
        if p == "openai" or p in OPENAI_PRESETS:
            return _openai_compatible(p, system, user)
        raise ProviderError(
            f"SDD_PROVIDER='{p}' desconocido. Opciones: anthropic, "
            f"{', '.join(OPENAI_PRESETS)}, openai.")
    except BaseException:
        outcome = "error"
        raise
    finally:
        usage = last_usage()
        metrics.record(
            os.environ.get("SDD_METRICS_WORKDIR"),
            os.environ.get("SDD_METRICS_OPERATION", "llm"),
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            outcome=outcome, provider=p,
            node=os.environ.get("SDD_METRICS_NODE", ""),
            task=os.environ.get("SDD_METRICS_TASK", ""),
            input_chars=len(system) + len(user), **usage)
        if usage["calls"]:
            metrics.record_usage(
                os.environ.get("SDD_METRICS_WORKDIR"),
                operation=os.environ.get("SDD_METRICS_OPERATION", "llm"),
                provider=p, node=os.environ.get("SDD_METRICS_NODE", ""),
                task=os.environ.get("SDD_METRICS_TASK", ""), **usage)


def _anthropic(system: str, user: str) -> str:
    _require_env("ANTHROPIC_API_KEY", "anthropic")
    try:
        import anthropic
    except ImportError as e:
        raise ProviderError(
            "el proveedor 'anthropic' requiere el SDK: pip install anthropic") from e
    model = os.environ.get("SDD_MODEL", ANTHROPIC_DEFAULT_MODEL)
    client = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY del entorno

    def call(prefill: str):
        messages = [{"role": "user", "content": user}]
        if prefill:
            messages.append({"role": "assistant", "content": prefill})

        def once():
            # Streaming: max_tokens alto sin riesgo de timeout HTTP.
            kwargs = {"model": model, "max_tokens": MAX_TOKENS,
                      "system": system, "messages": messages}
            if os.environ.get("SDD_PROMPT_CACHE", "1") != "0":
                kwargs["cache_control"] = {"type": "ephemeral"}
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()

        msg = _with_retry(once, "anthropic.messages.stream")
        u = getattr(msg, "usage", None)
        _add_usage(
            getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
            getattr(u, "cache_read_input_tokens", 0),
            getattr(u, "cache_creation_input_tokens", 0))
        text = "".join(b.text for b in msg.content if b.type == "text")
        return text, msg.stop_reason == "max_tokens"

    return _continue_until_complete(call)


def _openai_compatible(provider: str, system: str, user: str) -> str:
    if provider == "openai":
        base = _require_env("SDD_BASE_URL", provider)
        key = _require_env("SDD_API_KEY", provider)
        model = _require_env("SDD_MODEL", provider)
    else:
        cfg = OPENAI_PRESETS[provider]
        base = os.environ.get("SDD_BASE_URL", cfg["base_url"])
        key = _require_env(cfg["key_env"], provider)
        model = os.environ.get("SDD_MODEL", cfg["model"])
    url = base.rstrip("/") + "/chat/completions"

    # 'prefix' es propio de DeepSeek (endpoint /beta). Otros compatibles con OpenAI
    # devuelven HTTP 400 ante un campo desconocido, asi que solo se envia a DeepSeek.
    soporta_prefix = provider == "deepseek" or "deepseek.com" in base

    def call(prefill: str):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        if prefill:
            # Continuacion: el turno de asistente parcial hace de prefijo.
            msg = {"role": "assistant", "content": prefill}
            if soporta_prefix:
                msg["prefix"] = True
            messages.append(msg)
        body = json.dumps({"model": model, "messages": messages,
                           "max_tokens": MAX_TOKENS, "temperature": 0.2,
                           "stream": False}).encode("utf-8")

        def once():
            req = urllib.request.Request(
                url, data=body,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code in TRANSIENT_STATUS:
                    raise                       # lo reintenta _with_retry
                raise ProviderError(f"{provider} HTTP {e.code}: {detail}") from e

        data = _with_retry(once, f"{provider} chat/completions")
        u = data.get("usage") or {}
        _add_usage(u.get("prompt_tokens"), u.get("completion_tokens"))
        try:
            choice = data["choices"][0]
            return choice["message"]["content"] or "", choice.get("finish_reason") == "length"
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{provider} respuesta inesperada: {str(data)[:300]}") from e

    return _continue_until_complete(call)

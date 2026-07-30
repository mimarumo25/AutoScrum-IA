#!/usr/bin/env python3
"""Enrutamiento adaptativo y seguro de modelos por rol.

Las claves nunca forman parte de una decision. Solo se materializan de forma
temporal en el entorno del proceso que realiza la llamada.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable

from sdd.core import config
from sdd.integrations import providers

TIERS = ("economy", "balanced", "frontier")
DEFAULT_ROLE_TIERS = {
    "product": "frontier",
    "architect": "frontier",
    "planner": "frontier",
    "dev_backend": "economy",
    "dev_frontend": "economy",
    "qa": "balanced",
}
DEFAULT_PROVIDER_PRIORITY = [
    "anthropic", "openai", "glm", "kimi", "deepseek", "qwen",
]
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "SDD_API_KEY"}
KEY_ENV.update({name: value["key_env"]
                for name, value in providers.OPENAI_PRESETS.items()})
_ENV_LOCK = threading.RLock()


class ModelRoutingError(RuntimeError):
    """No existe una selección utilizable y segura."""


def _routing(cfg: dict) -> dict:
    value = cfg.get("routing")
    return value if isinstance(value, dict) else {}


def _profile(cfg: dict, role: str) -> dict:
    value = (cfg.get("agent_profiles") or {}).get(role)
    return value if isinstance(value, dict) else {}


def credential_present(cfg: dict, provider: str) -> bool:
    key_name = KEY_ENV.get(provider, "")
    return bool((cfg.get("keys") or {}).get(provider) or
                (key_name and os.environ.get(key_name)))


def classify_model(model_id: str) -> str:
    """Clasifica IDs nuevos sin acoplar el sistema a un catálogo estático."""
    value = (model_id or "").lower()
    if re.search(r"opus|max|pro|reasoner|o[1-9](?:-|$)|ultra", value):
        return "frontier"
    if re.search(r"flash|turbo|mini|nano|haiku|air|lite", value):
        return "economy"
    if value:
        return "balanced"
    return "unclassified"


def _catalog(cfg: dict) -> list[dict]:
    raw = _routing(cfg).get("model_catalog") or {}
    rows: list[dict] = []
    if isinstance(raw, dict):
        for provider, entries in raw.items():
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                rows.append({"provider": str(provider), **entry,
                             "source": entry.get("source", "catalog")})
    return rows


def _configured_candidates(cfg: dict) -> list[dict]:
    """Incluye modelos explícitos aunque todavía no haya discovery."""
    pairs: set[tuple[str, str]] = set()
    global_provider = str(cfg.get("provider") or "")
    global_model = str(cfg.get("model") or "")
    if global_provider:
        model = global_model or providers.default_model(global_provider)
        if model:
            pairs.add((global_provider, model))
    for profile in (cfg.get("agent_profiles") or {}).values():
        if not isinstance(profile, dict):
            continue
        provider = str(profile.get("provider") or "")
        model = str(profile.get("model") or "")
        if provider and model:
            pairs.add((provider, model))
    result = []
    for provider, model in pairs:
        result.append({
            "provider": provider, "id": model, "tier": classify_model(model),
            "enabled": True, "source": "configured",
        })
    return result


def candidates(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load()
    merged: dict[tuple[str, str], dict] = {}
    for item in [*_configured_candidates(cfg), *_catalog(cfg)]:
        key = (str(item.get("provider") or ""), str(item.get("id") or ""))
        if all(key):
            merged[key] = {**merged.get(key, {}), **item}
    result = []
    for item in merged.values():
        provider = str(item["provider"])
        # Una sola fuente de verdad para el tier. El catalogo de discovery guardaba
        # "unclassified" mientras resolve_agent clasificaba el MISMO modelo con
        # classify_model: la UI mostraba dos tiers distintos para un solo modelo y el
        # routing automatico lo descartaba por "tier sin clasificar" aunque el runtime
        # sabia perfectamente cual era. Un tier puesto a mano en config sigue ganando;
        # lo que no este clasificado se deriva con la misma funcion que usa el runtime.
        declared = str(item.get("tier") or "")
        tier = declared if declared in TIERS else classify_model(str(item.get("id") or ""))
        enabled = bool(item.get("enabled", True))
        reason = ""
        if not enabled:
            reason = "deshabilitado"
        elif not credential_present(cfg, provider):
            reason = "sin credencial"
        elif tier not in TIERS:
            reason = "tier sin clasificar"
        result.append({
            **item, "tier": tier, "enabled": enabled,
            "available": not reason, "disabled_reason": reason,
        })
    known = {str(item.get("provider")) for item in result}
    for provider in DEFAULT_PROVIDER_PRIORITY:
        if provider in known:
            continue
        reason = ("sin credencial" if not credential_present(cfg, provider)
                  else "catalogo no descubierto")
        result.append({
            "provider": provider, "id": "", "tier": "unclassified",
            "enabled": False, "available": False,
            "disabled_reason": reason, "source": "placeholder",
        })
    return result


def _priority(cfg: dict, preferred: str = "") -> list[str]:
    routing = _routing(cfg)
    values = [preferred, str(cfg.get("provider") or "")]
    values += [str(p) for p in routing.get("provider_priority",
                                          DEFAULT_PROVIDER_PRIORITY)]
    output = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _pick(available: list[dict], requested_tier: str,
          priority: list[str], excluded_provider: str = "") -> tuple[dict, str]:
    rank = {provider: index for index, provider in enumerate(priority)}
    source_rank = {"discovery": 0, "catalog": 0, "manual": 0, "configured": 1}
    ordered = sorted(
        available,
        key=lambda item: (rank.get(str(item["provider"]), len(rank)),
                          source_rank.get(str(item.get("source")), 1),
                          str(item["id"])),
    )
    exact = [item for item in ordered if item["tier"] == requested_tier and
             item["provider"] != excluded_provider]
    if exact:
        return exact[0], ""
    exact_same = [item for item in ordered if item["tier"] == requested_tier]
    if exact_same:
        return exact_same[0], "no hay diversidad de proveedor disponible"
    # "Mejor" significa el tier de mayor capacidad disponible.
    tier_rank = {"frontier": 3, "balanced": 2, "economy": 1}
    fallback = sorted(
        [item for item in ordered if item["provider"] != excluded_provider] or ordered,
        key=lambda item: (-tier_rank.get(str(item["tier"]), 0),
                          rank.get(str(item["provider"]), len(rank))),
    )
    if fallback:
        return fallback[0], f"tier {requested_tier} no disponible"
    raise ModelRoutingError("no hay modelos clasificados con credencial válida")


def _decision(item: dict, requested: str, reason: str, escalated: bool,
              fallback_reason: str = "") -> dict:
    return {
        "provider": str(item["provider"]),
        "model": str(item["id"]),
        "tier": str(item.get("tier") or "unclassified"),
        "requested_tier": requested,
        "selection_reason": reason,
        "fallback_reason": fallback_reason,
        "escalated": bool(escalated),
    }


def resolve_agent(role: str, task: dict | None = None,
                  cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    task = task or {}
    profile = _profile(cfg, role)
    explicit_provider = str(profile.get("provider") or "").strip()
    explicit_model = str(profile.get("model") or "").strip()
    if explicit_model and not explicit_provider:
        explicit_provider = str(cfg.get("provider") or "").strip()
    if explicit_provider and explicit_model:
        if not credential_present(cfg, explicit_provider):
            raise ModelRoutingError(
                f"el override de {role} usa {explicit_provider} sin credencial")
        tier = classify_model(explicit_model)
        item = {"provider": explicit_provider, "id": explicit_model, "tier": tier}
        return _decision(item, tier, "override explícito del perfil", False)

    routing = _routing(cfg)
    if routing.get("mode") == "manual":
        provider = explicit_provider or str(cfg.get("provider") or "")
        model = explicit_model or str(cfg.get("model") or
                                      providers.default_model(provider))
        if not provider or not model or not credential_present(cfg, provider):
            raise ModelRoutingError(
                f"modo manual sin proveedor, modelo o credencial valida para {role}")
        tier = classify_model(model)
        return _decision(
            {"provider": provider, "id": model, "tier": tier},
            tier, "configuracion manual global", False)
    escalated = bool(task.get("model_escalated"))
    role_tiers = routing.get("role_tiers") or DEFAULT_ROLE_TIERS
    requested = "frontier" if escalated else str(
        role_tiers.get(role, DEFAULT_ROLE_TIERS.get(role, "balanced")))
    available = [item for item in candidates(cfg) if item["available"]]
    selected, fallback = _pick(
        available, requested, _priority(cfg, explicit_provider))
    reason = ("escalado único tras fallo propio" if escalated else
              f"política adaptativa para {role}")
    return _decision(selected, requested, reason, escalated, fallback)


def resolve_review(author_provider: str = "", cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    review = _routing(cfg).get("reviewer") or {}
    provider = str(review.get("provider") or os.environ.get(
        "SDD_REVIEW_PROVIDER", "")).strip()
    model = str(review.get("model") or os.environ.get(
        "SDD_REVIEW_MODEL", "")).strip()
    if model and not provider:
        provider = str(cfg.get("provider") or os.environ.get("SDD_PROVIDER", ""))
    if provider and model:
        if not credential_present(cfg, provider):
            raise ModelRoutingError(
                f"el revisor manual usa {provider} sin credencial")
        item = {"provider": provider, "id": model, "tier": classify_model(model)}
        return _decision(item, "frontier", "override manual del revisor", False)
    if _routing(cfg).get("mode") == "manual":
        provider = str(cfg.get("provider") or "")
        model = str(cfg.get("model") or providers.default_model(provider))
        if not provider or not model or not credential_present(cfg, provider):
            raise ModelRoutingError("modo manual sin configuracion valida para el revisor")
        return _decision(
            {"provider": provider, "id": model, "tier": classify_model(model)},
            "frontier", "configuracion manual global del revisor", False)

    prefer_different = bool(review.get("prefer_different_provider", True))
    available = [item for item in candidates(cfg) if item["available"]]
    selected, fallback = _pick(
        available, "frontier", _priority(cfg, provider),
        author_provider if prefer_different else "")
    reason = "revisor frontier"
    if prefer_different and selected["provider"] != author_provider:
        reason += " con diversidad de proveedor"
    elif prefer_different and author_provider:
        fallback = fallback or "no hay diversidad de proveedor disponible"
    return _decision(selected, "frontier", reason, False, fallback)


def preview(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    decisions = {}
    for role in DEFAULT_ROLE_TIERS:
        try:
            decisions[role] = resolve_agent(role, cfg=cfg)
        except ModelRoutingError as error:
            decisions[role] = {"error": str(error)}
    author = decisions.get("dev_backend", {}).get("provider", "")
    try:
        reviewer = resolve_review(author, cfg)
    except ModelRoutingError as error:
        reviewer = {"error": str(error)}
    return {
        "mode": _routing(cfg).get("mode", "adaptive"),
        "roles": decisions,
        "reviewer": reviewer,
        "candidates": candidates(cfg),
    }


def _provider_endpoint(provider: str) -> tuple[str, str]:
    if provider == "openai":
        return os.environ.get("SDD_BASE_URL", "https://api.openai.com/v1"), "SDD_API_KEY"
    if provider == "anthropic":
        return "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"
    preset = providers.OPENAI_PRESETS.get(provider)
    if not preset:
        raise ModelRoutingError(f"proveedor desconocido: {provider}")
    return str(preset["base_url"]), str(preset["key_env"])


def discover(provider: str, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load()
    provider = provider.strip().lower()
    key = str((cfg.get("keys") or {}).get(provider) or
              os.environ.get(KEY_ENV.get(provider, ""), ""))
    if not key:
        raise ModelRoutingError(f"{provider} no tiene credencial configurada")
    base, _ = _provider_endpoint(provider)
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if provider == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    request = urllib.request.Request(base.rstrip("/") + "/models", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as error:
        raise ModelRoutingError(f"discovery de {provider} falló: {error}") from error
    raw = payload.get("data") or payload.get("models") or []
    ids = [str(item.get("id") if isinstance(item, dict) else item)
           for item in raw]
    previous = {str(item.get("id")): item for item in _catalog(cfg)
                if item.get("provider") == provider}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [{
        "id": model_id,
        # Un tier ya fijado a mano se conserva; uno nuevo se deriva con la misma
        # funcion que usa el runtime, para no dejar el catalogo y la ejecucion
        # afirmando tiers distintos sobre el mismo modelo.
        "tier": (str(previous.get(model_id, {}).get("tier") or "")
                 if str(previous.get(model_id, {}).get("tier") or "") in TIERS
                 else classify_model(model_id)),
        "enabled": bool(previous.get(model_id, {}).get("enabled", True)),
        "discovered_at": now,
        "source": "discovery",
    } for model_id in ids if model_id]


def persist_discovery(provider: str, entries: Iterable[dict]) -> dict:
    cfg = config.load()
    routing = dict(_routing(cfg))
    catalog = dict(routing.get("model_catalog") or {})
    catalog[provider] = [dict(entry) for entry in entries]
    routing["model_catalog"] = catalog
    return config.save({"routing": routing})


@contextmanager
def selection_environment(selection: dict, cfg: dict | None = None):
    """Aplica credenciales en alcance acotado y restaura el entorno al salir."""
    cfg = cfg or config.load()
    provider = str(selection["provider"])
    key_name = KEY_ENV.get(provider)
    key = str((cfg.get("keys") or {}).get(provider) or
              (os.environ.get(key_name, "") if key_name else ""))
    if not key_name or not key:
        raise ModelRoutingError(f"{provider} no tiene credencial válida")
    updates = {
        "SDD_PROVIDER": provider,
        "SDD_MODEL": str(selection["model"]),
        key_name: key,
        "SDD_MODEL_TIER": str(selection.get("tier") or ""),
        "SDD_SELECTION_REASON": str(selection.get("selection_reason") or ""),
        "SDD_MODEL_ESCALATED": "1" if selection.get("escalated") else "0",
    }
    with _ENV_LOCK:
        before = {name: os.environ.get(name) for name in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for name, value in before.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

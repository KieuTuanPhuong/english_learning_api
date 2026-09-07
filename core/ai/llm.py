"""Provider dispatch for text LLM calls.

Every AI feature asks for a *task* — ``grading``, ``assist`` — and this module
resolves it to ``(provider, model)`` from settings, then calls that provider's
``generate_json`` with one shared contract. Audio requests always go to
Gemini (the only multimodal provider wired in).

Settings (all optional; env-driven in ``config/settings.py``):

* ``AI_GRADING_MODEL`` / ``AI_ASSIST_MODEL`` — ``"provider:model"``, e.g.
  ``nvidia:moonshotai/kimi-k3`` or ``gemini:gemini-3.6-flash``. A bare
  provider name (``nvidia``) uses that provider's default model.
* ``AI_TEXT_PROVIDER`` — provider for tasks without a spec.
* ``AI_FALLBACK_PROVIDER`` — if the chosen provider fails (NIM queue timeout,
  quota, missing key), retry once on this provider (text tasks only).

Every reply carries ``engine`` = ``"provider:model"`` of the provider that
actually answered, so labels stay honest after a fallback.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from . import gemini, nvidia
from .gemini import GeminiError

log = logging.getLogger(__name__)

PROVIDERS = {"gemini": gemini, "nvidia": nvidia}
TASK_SETTINGS = {"grading": "AI_GRADING_MODEL", "assist": "AI_ASSIST_MODEL"}


def resolve(task: str = "assist", *, audio: bool = False) -> tuple[str, str]:
    """``(provider, model)`` for a task. Audio forces Gemini."""
    if audio:
        return "gemini", gemini.model_name()
    spec = (getattr(settings, TASK_SETTINGS.get(task, ""), "") or "").strip()
    if not spec:
        spec = (getattr(settings, "AI_TEXT_PROVIDER", "gemini") or "gemini").strip()
    provider, _, model = spec.partition(":")
    provider = provider.lower() or "gemini"
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown AI provider '{provider}' (expected one of {sorted(PROVIDERS)})")
    if not model:
        model = PROVIDERS[provider].model_name()
    return provider, model


def label(task: str = "assist", *, audio: bool = False) -> str:
    provider, model = resolve(task, audio=audio)
    return f"{provider}:{model}"


def _call(provider: str, model: str, audio, kwargs: dict) -> dict:
    if provider == "gemini":
        return gemini.generate_json(audio=audio, **kwargs)
    return nvidia.generate_json(model=model, **kwargs)


def generate_json(task: str = "assist", *, audio=None, **kwargs) -> dict:
    """Route ``gemini.generate_json``-style kwargs to the configured provider,
    falling back to ``AI_FALLBACK_PROVIDER`` once on failure. The returned
    dict always carries ``engine`` (``provider:model`` that answered)."""
    provider, model = resolve(task, audio=audio is not None)
    try:
        data = _call(provider, model, audio, kwargs)
    except (GeminiError, ImproperlyConfigured) as exc:
        fallback = (getattr(settings, "AI_FALLBACK_PROVIDER", "") or "").lower()
        if audio is not None or not fallback or fallback == provider or fallback not in PROVIDERS:
            raise
        log.warning("AI provider %s:%s failed (%s); falling back to %s", provider, model, exc, fallback)
        provider, model = fallback, PROVIDERS[fallback].model_name()
        data = _call(provider, model, audio, kwargs)
    data["engine"] = f"{provider}:{model}"
    return data

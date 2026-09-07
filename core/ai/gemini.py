"""Minimal Google Gemini ``generateContent`` client (stdlib only — no SDK).

Used by ``core/ai/assist.py`` for the two coaching features (teacher-feedback
review, student mistake explanation). Kept deliberately small:

* ``GEMINI_API_KEY`` (env / settings) authenticates via the ``x-goog-api-key``
  header.
* ``GEMINI_MODEL`` picks the model; default ``gemini-3.6-flash`` (the model the
  API itself recommends to new keys — ``gemini-2.5-flash`` is retired for them).
* Every call asks for ``application/json`` with a ``responseSchema`` so the
  reply parses straight into a dict. ``GeminiError`` wraps transport, quota and
  malformed-reply failures so views can turn them into a clean 502/400.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DEFAULT_MODEL = "gemini-3.6-flash"
_RETRY_STATUSES = {429, 500, 503}
_RETRY_DELAYS = (1.0, 3.0, 6.0)  # seconds between attempts on a retryable status
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
class GeminiError(RuntimeError):
    """Raised for any failure talking to Gemini (network, HTTP, bad JSON)."""


def _api_key() -> str:
    key = getattr(settings, "GEMINI_API_KEY", "") or ""
    if not key:
        raise ImproperlyConfigured(
            "GEMINI_API_KEY is not set. Add it to .env or set AI_ASSIST_BACKEND=mock."
        )
    return key


def model_name() -> str:
    return getattr(settings, "GEMINI_MODEL", None) or DEFAULT_MODEL


def generate_json(
    *,
    system: str,
    user: str,
    schema: dict,
    temperature: float = 0.3,
    timeout: float = 60.0,
) -> dict:
    """POST one prompt and return the parsed JSON object the model produced.

    ``schema`` is the OpenAPI-subset ``responseSchema`` Gemini accepts
    (``{"type": "OBJECT", "properties": {...}, "required": [...]}``).
    """
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": temperature,
        },
    }
    url = f"{_BASE}/{model_name()}:generateContent"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": _api_key(),
        },
    )
    # Overloaded / rate-limited replies are common and short-lived; retry a
    # few times with backoff before surfacing a 502 to the user.
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return parse_response(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in _RETRY_STATUSES and delay is not None:
                time.sleep(delay)
                continue
            raise GeminiError(f"Gemini HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GeminiError(f"Gemini request failed: {exc}") from exc
    raise GeminiError("Gemini request failed after retries")  # unreachable


def parse_response(raw: str) -> dict:
    """Pull the JSON text out of a ``generateContent`` reply and decode it.
    Split out so tests can exercise it without the network."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini returned non-JSON body") from exc
    if "error" in payload:
        raise GeminiError(f"Gemini error: {payload['error'].get('message', payload['error'])}")
    try:
        candidate = payload["candidates"][0]
    except (KeyError, IndexError, TypeError) as exc:
        block = payload.get("promptFeedback", {}).get("blockReason")
        raise GeminiError(
            f"Gemini returned no candidates (blockReason={block})"
        ) from exc
    text = "".join(
        part.get("text", "")
        for part in candidate.get("content", {}).get("parts", [])
    ).strip()
    if not text:
        raise GeminiError(
            f"Gemini candidate had no text (finishReason={candidate.get('finishReason')})"
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini reply was not valid JSON") from exc
    if not isinstance(data, dict):
        raise GeminiError("Gemini reply JSON was not an object")
    return data


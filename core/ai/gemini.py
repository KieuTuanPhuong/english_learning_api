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

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DEFAULT_MODEL = "gemini-3.6-flash"
_RETRY_STATUSES = {429, 500, 503}
_RETRY_DELAYS = (1.0, 3.0, 6.0)  # seconds between attempts on a retryable status
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # inlineData request ceiling
_AUDIO_MIME = {
    ".webm": "audio/webm", ".weba": "audio/webm", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mp3",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".aiff": "audio/aiff", ".aif": "audio/aiff",
    ".flac": "audio/flac",
}
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


def audio_mime(name: str) -> str:
    ext = Path(urllib.parse.urlparse(str(name)).path).suffix.lower()
    if ext in _AUDIO_MIME:
        return _AUDIO_MIME[ext]
    guess, _ = mimetypes.guess_type(str(name))
    return guess or "audio/webm"


def resolve_audio(url_or_path: str) -> tuple[str, bytes]:
    """Turn a submission's ``audio_recording_url`` (or a local path) into
    ``(mime, bytes)``. Local ``MEDIA_URL`` links and filesystem paths are read
    directly; ``http(s)`` links are downloaded (capped at ``MAX_AUDIO_BYTES``).
    Raises ``ValueError`` when nothing usable is found so callers return 400."""
    if not url_or_path:
        raise ValueError("No audio recording to analyse.")
    src = str(url_or_path)
    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    media_root = Path(getattr(settings, "MEDIA_ROOT", "media"))
    # Always via urlparse: stored links now carry a `?t=<signature>` query
    # (core/media.py) that must not leak into the filesystem lookup.
    path = urllib.parse.urlparse(src).path or src
    local = None
    if path.startswith(media_url):
        local = media_root / path[len(media_url):]
    elif Path(path).is_absolute() or Path(path).exists():
        local = Path(path)
    if local is not None:
        if not local.is_file():
            if src.startswith(("http://", "https://")):
                local = None  # remote host serves a different MEDIA tree; fall through
            else:
                raise ValueError(f"Audio file not found: {src}")
        else:
            data = local.read_bytes()
            if len(data) > MAX_AUDIO_BYTES:
                raise ValueError("Audio file too large for AI analysis.")
            return audio_mime(local.name), data
    if src.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(src, timeout=30) as resp:
                data = resp.read(MAX_AUDIO_BYTES + 1)
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(f"Could not download audio: {exc}") from exc
        if len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Audio file too large for AI analysis.")
        mime = ctype if ctype.startswith(("audio/", "video/")) else audio_mime(src)
        return mime, data
    raise ValueError(f"Unsupported audio location: {src}")


def generate_json(
    *,
    system: str,
    user: str,
    schema: dict,
    temperature: float = 0.3,
    timeout: float = 60.0,
    audio: tuple[str, bytes] | None = None,
) -> dict:
    """POST one prompt and return the parsed JSON object the model produced.

    ``schema`` is the OpenAPI-subset ``responseSchema`` Gemini accepts
    (``{"type": "OBJECT", "properties": {...}, "required": [...]}``).
    ``audio`` = ``(mime, bytes)`` attaches a recording inline (multimodal).
    """
    parts: list[dict] = []
    if audio is not None:
        mime, data = audio
        parts.append({"inlineData": {
            "mimeType": mime, "data": base64.b64encode(data).decode("ascii"),
        }})
    parts.append({"text": user})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
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
            with urllib.request.urlopen(req, timeout=timeout if audio is None else max(timeout, 120.0)) as resp:
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


_TRANSCRIBE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"transcript": {"type": "STRING"}},
    "required": ["transcript"],
}


def transcribe(url_or_path: str) -> str:
    """Verbatim transcript of a recording (fillers and self-corrections kept)."""
    audio = resolve_audio(url_or_path)
    data = generate_json(
        system=(
            "Transcribe the attached English recording verbatim. Keep fillers "
            "(um, uh), repetitions and self-corrections; write [inaudible] for "
            "unclear parts; return an empty string if there is no speech."
        ),
        user="Transcribe the attached audio.",
        schema=_TRANSCRIBE_SCHEMA,
        temperature=0.0,
        audio=audio,
    )
    return (data.get("transcript") or "").strip()

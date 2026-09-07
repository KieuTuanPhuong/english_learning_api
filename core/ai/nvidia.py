"""NVIDIA NIM (build.nvidia.com) client — OpenAI-compatible chat completions.

Hosts open models such as ``moonshotai/kimi-k3`` and
``deepseek-ai/deepseek-v4-flash-0731`` / ``deepseek-v4-pro-0813``. Text only:
these models take no audio, so speaking/pronunciation stay on Gemini
(``core/ai/llm.py`` enforces that).

Structured output: NIM does not reliably honour ``response_format`` across
models, so the JSON schema is spelled out in the system prompt and the reply
is parsed defensively (code fences / leading prose stripped, and any
``<think>...</think>`` reasoning block DeepSeek/Kimi emit is dropped).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .gemini import GeminiError  # shared "AI unavailable" error type

BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "moonshotai/kimi-k3"
# 504 is NIM's ~300 s gateway timeout for a queued model: retrying inside a
# web request only multiplies the wait, so it is surfaced as a 502 instead.
_RETRY_STATUSES = {429, 500, 502, 503}
_RETRY_DELAYS = (2.0, 5.0)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _api_key() -> str:
    key = getattr(settings, "NVIDIA_API_KEY", "") or ""
    if not key:
        raise ImproperlyConfigured(
            "NVIDIA_API_KEY is not set. Add it to .env or route AI_*_MODEL to gemini."
        )
    return key


def model_name() -> str:
    return getattr(settings, "NVIDIA_MODEL", None) or DEFAULT_MODEL


def _schema_hint(schema: dict) -> str:
    """Render Gemini's OpenAPI-subset responseSchema as a compact JSON example
    for models without native schema enforcement."""

    def render(node):
        t = (node.get("type") or "STRING").upper()
        if t == "OBJECT":
            return {k: render(v) for k, v in (node.get("properties") or {}).items()}
        if t == "ARRAY":
            return [render(node.get("items") or {"type": "STRING"})]
        if "enum" in node:
            return " | ".join(node["enum"])
        return {"STRING": "string", "INTEGER": 0, "NUMBER": 0.0, "BOOLEAN": True}.get(t, "string")

    required = schema.get("required") or []
    return (
        "Respond with ONE JSON object and nothing else — no prose, no markdown "
        "fences, no reasoning. Shape (every key below is required"
        + (f"; required: {', '.join(required)}" if required else "")
        + "):\n"
        + json.dumps(render(schema), ensure_ascii=False, indent=1)
    )


def _stream_content(resp) -> tuple[str, str | None]:
    """Accumulate the ``content`` deltas of an SSE chat-completions stream.
    ``reasoning_content`` (Kimi K3 / DeepSeek V4 thinking) is discarded.
    Returns ``(content, finish_reason)``."""
    content: list[str] = []
    finish = None
    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if "error" in chunk:
            err = chunk["error"]
            raise GeminiError(f"NVIDIA stream error: {err.get('message') if isinstance(err, dict) else err}")
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    return "".join(content), finish


def generate_json(
    *,
    system: str,
    user: str,
    schema: dict,
    temperature: float = 0.3,
    timeout: float | None = None,
    audio=None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> dict:
    """Same contract as ``gemini.generate_json``; ``audio`` is rejected.

    Streams (``stream: true``): NIM queues large models for a minute or more
    before the first byte, and a non-streaming call sits silent for the whole
    generation, so streaming is what keeps proxies from dropping the socket.
    ``timeout`` is the per-read socket timeout (default ``NVIDIA_TIMEOUT``).
    """
    if audio is not None:
        raise ValueError("NVIDIA-hosted text models cannot analyse audio; use Gemini.")
    body: dict = {
        "model": model or model_name(),
        "messages": [
            {"role": "system", "content": system + "\n\n" + _schema_hint(schema)},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if not getattr(settings, "NVIDIA_THINKING", False):
        # Kimi K3 / DeepSeek V4 are reasoning models; the JSON tasks here do
        # not need the thinking pass and it multiplies latency + tokens.
        body["chat_template_kwargs"] = {"thinking": False}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {_api_key()}",
        },
    )
    timeout = timeout or float(getattr(settings, "NVIDIA_TIMEOUT", 300))
    for delay in (*_RETRY_DELAYS, None):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content, finish = _stream_content(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in _RETRY_STATUSES and delay is not None:
                time.sleep(delay)
                continue
            raise GeminiError(f"NVIDIA HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GeminiError(f"NVIDIA request failed: {exc}") from exc
        if not content.strip():
            raise GeminiError(f"NVIDIA stream had no content (finish_reason={finish})")
        return extract_json(content)
    raise GeminiError("NVIDIA request failed after retries")  # unreachable


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a chat reply (fences / prose / think
    blocks tolerated)."""
    cleaned = _THINK_RE.sub("", text or "").strip()
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise GeminiError("NVIDIA reply contained no JSON object")
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise GeminiError("NVIDIA reply was not valid JSON") from exc
    if not isinstance(data, dict):
        raise GeminiError("NVIDIA reply JSON was not an object")
    return data


def parse_response(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError("NVIDIA returned non-JSON body") from exc
    if "error" in payload or "detail" in payload and "choices" not in payload:
        err = payload.get("error") or payload.get("detail")
        msg = err.get("message") if isinstance(err, dict) else err
        raise GeminiError(f"NVIDIA error: {msg}")
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("NVIDIA returned no choices") from exc
    content = (choice.get("message") or {}).get("content") or ""
    if not content.strip():
        raise GeminiError(
            f"NVIDIA choice had no content (finish_reason={choice.get('finish_reason')})"
        )
    return extract_json(content)

"""Server-side audio normalization + upload validation.

docs/research/04-pronunciation-practice.md §5 phase 0. ffmpeg/ffprobe are invoked
via ``subprocess`` (no pydub). ffmpeg is only a HARD dependency when the real
engine runs (``PRONUNCIATION_BACKEND=azure``): the mock engine scores from the
raw upload bytes and never transcodes, so dev/test run fine without ffmpeg
installed. Size + content-type are always enforced; duration is enforced only
when ffprobe is available (best-effort), so the offline test path is not gated
on a binary that may be absent.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from rest_framework import serializers

# Upload caps (research doc N3): drills are short.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_DURATION_SECONDS = 30

# Mock-test caps. A drill is one sentence; an IELTS Speaking Part 1 or Part 3
# runs four to five minutes, so the drill limits would reject every real answer.
MOCK_TEST_MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB
MOCK_TEST_MAX_DURATION_SECONDS = 6 * 60         # 6 min, above the longest part

# Browser MediaRecorder emits webm/opus (Chrome/Firefox) or mp4/aac (Safari);
# some send generic audio/*. Accept the families we transcode from.
_ALLOWED_CONTENT_PREFIXES = ("audio/", "video/webm", "video/mp4", "video/ogg")

FFMPEG_TIMEOUT_SECONDS = 30


def validate_upload(uploaded_file, max_bytes=None, max_seconds=None) -> None:
    """Reject an audio upload that is too big, the wrong type, or too long.

    Raises ``rest_framework.serializers.ValidationError`` (→ HTTP 400). Duration
    is checked only if ffprobe is on PATH; size + content-type are hard caps.

    ``max_bytes`` / ``max_seconds`` default to the pronunciation-drill limits.
    Mock-test speaking parts pass the larger ``MOCK_TEST_*`` caps, and a per-part
    ``TestSectionExercise.max_record_seconds`` overrides ``max_seconds`` again so
    an IELTS Part 2 long turn is held to its real two minutes."""
    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    max_seconds = MAX_DURATION_SECONDS if max_seconds is None else max_seconds

    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise serializers.ValidationError(
            f"Audio file too large ({size} bytes); limit is {max_bytes}."
        )

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if content_type and not content_type.startswith(_ALLOWED_CONTENT_PREFIXES):
        raise serializers.ValidationError(
            f"Unsupported audio content-type '{content_type}'."
        )

    duration = _probe_duration(uploaded_file)
    if duration is not None and duration > max_seconds:
        raise serializers.ValidationError(
            f"Audio too long ({duration:.1f}s); limit is {max_seconds}s."
        )


def transcode_to_wav16k(src_path) -> Path:
    """Normalize any input to WAV PCM 16 kHz mono next to the source. The single
    normalization point (research doc §3.3 pipeline A). Requires the ffmpeg
    binary — callers on the real-engine path only."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required to transcode audio for the real pronunciation "
            "engine but was not found on PATH. Install ffmpeg (see "
            "docs/DEPLOYMENT.md) or set PRONUNCIATION_BACKEND=mock."
        )
    src = Path(src_path)
    dst = src.with_suffix(".16k.wav")
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            str(dst),
        ],
        capture_output=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(
            "ffmpeg transcode failed: "
            + proc.stderr.decode("utf-8", "replace")[-500:]
        )
    return dst


def _probe_duration(uploaded_file):
    """Best-effort duration in seconds via ffprobe, or ``None`` if ffprobe is
    unavailable / the probe fails (offline dev/test path)."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".upload", delete=True) as tmp:
            for chunk in uploaded_file.chunks():
                tmp.write(chunk)
            tmp.flush()
            uploaded_file.seek(0)  # rewind for the real save
            proc = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    tmp.name,
                ],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        if proc.returncode != 0:
            return None
        return float(proc.stdout.decode().strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None

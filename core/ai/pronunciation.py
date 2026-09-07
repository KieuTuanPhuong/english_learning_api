"""Pronunciation-assessment backends (docs/research/04-pronunciation-practice.md §4.4).

Mirrors ``core/ai/backends.py`` + ``service.py`` structurally — same
STUB-vs-REAL split, same loud-failure stance, same ``AiModel.active()`` binding —
but returns a different shape (phoneme-level measurement, not an LLM rubric), so
it is copied structurally rather than imported.

============================== STUB vs REAL ==============================
MockPronunciationBackend  -> DETERMINISTIC, no network, no ffmpeg. Scores derive
                             from sha256(reference_text + audio byte-size) so
                             seed/tests are stable (research doc N4).
GeminiPronunciationBackend -> LIVE. ``PRONUNCIATION_BACKEND=gemini``: the
                             recording goes inline to Gemini with the reference
                             text; the model returns word/phoneme judgements in
                             the §4.3 shape. No ffmpeg (browser webm/mp4 sent
                             as-is). Coarser than Azure's acoustic model.
AzurePronunciationBackend -> STUBBED. Documents where the Azure Speech SDK call
                             goes; raises until env keys exist so a misconfigured
                             deploy fails loudly.
=========================================================================
"""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# pyrefly: ignore [missing-import]
from ..models import AiModel, GradingStrictness
from . import gemini

# Strictness -> multiplier, matching backends.py `_STRICTNESS_FACTOR`. Applied to
# the deterministic mock scores; for the real engine it shifts display thresholds
# (research doc §4.4), not Azure's calibrated numbers.
_STRICTNESS_FACTOR = {
    GradingStrictness.LENIENT: Decimal("1.10"),
    GradingStrictness.STANDARD: Decimal("1.00"),
    GradingStrictness.STRICT: Decimal("0.85"),
}

_TWO_DP = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


class BasePronunciationBackend:
    """Interface every backend implements. ``ai_model`` is the active ``AiModel``
    row (or ``None``); subclasses read ``endpoint_url`` / ``strictness`` from it."""

    def __init__(self, ai_model=None):
        self.ai_model = ai_model

    @property
    def strictness(self):
        return self.ai_model.strictness if self.ai_model else GradingStrictness.STANDARD

    def assess(self, *, audio_path: str, reference_text: str) -> dict:
        """Returns
        ``{"overall", "accuracy", "fluency", "completeness", "prosody",
           "words": [...§4.3 shape...], "engine": str, "metadata": dict}``."""
        raise NotImplementedError


class MockPronunciationBackend(BasePronunciationBackend):
    """Deterministic, offline. Emits a plausible word/phoneme breakdown by
    splitting ``reference_text`` and assigning seeded per-phoneme accuracies."""

    def assess(self, *, audio_path: str, reference_text: str) -> dict:
        try:
            audio_bytes = os.path.getsize(audio_path)
        except OSError:
            audio_bytes = 0
        seed = hashlib.sha256(
            f"{reference_text}|{audio_bytes}".encode("utf-8")
        ).digest()

        words = [w for w in reference_text.split() if w]
        word_results = []
        word_accuracies = []
        for word_index, word in enumerate(words):
            graphemes = [ch for ch in word.lower() if ch.isalpha()] or list(word.lower())
            phonemes = []
            phoneme_values = []
            for phoneme_index, grapheme in enumerate(graphemes):
                acc = self._apply(Decimal(
                    self._raw(seed, word_index * 7 + phoneme_index + 1)
                ))
                phonemes.append({"phoneme": grapheme, "accuracy": float(acc)})
                phoneme_values.append(acc)
            word_acc = _q(
                sum(phoneme_values) / Decimal(len(phoneme_values))
            ) if phoneme_values else self._apply(Decimal(70))
            word_accuracies.append(word_acc)
            word_results.append({
                "word": word,
                "accuracy": float(word_acc),
                "error_type": "Mispronunciation" if word_acc < 60 else None,
                "phonemes": phonemes,
            })

        accuracy = _q(
            sum(word_accuracies) / Decimal(len(word_accuracies))
        ) if word_accuracies else self._apply(Decimal(70))
        fluency = self._apply(Decimal(self._raw(seed, 101)))
        # Scripted mock assumes every target word was attempted.
        completeness = self._apply(Decimal(100))
        prosody = self._apply(Decimal(self._raw(seed, 103)))
        overall = _q((accuracy + fluency + completeness + prosody) / Decimal(4))

        return {
            "overall": overall,
            "accuracy": accuracy,
            "fluency": fluency,
            "completeness": completeness,
            "prosody": prosody,
            "words": word_results,
            "engine": "mock",
            "metadata": {"seed": seed.hex()[:16], "audio_bytes": audio_bytes},
        }

    @staticmethod
    def _raw(digest: bytes, index: int, lo: int = 40, hi: int = 100) -> int:
        return lo + (digest[index % len(digest)] % (hi - lo + 1))

    def _apply(self, score: Decimal) -> Decimal:
        score = score * _STRICTNESS_FACTOR[self.strictness]
        score = max(Decimal("0"), min(Decimal("100"), score))
        return _q(score)


class AzurePronunciationBackend(BasePronunciationBackend):
    """STUBBED real backend. Wire ``assess`` to ship live scoring.

    Required at deploy time (NOT needed for the mock default):
      * ffmpeg on PATH                 -> transcode upload to WAV PCM 16 kHz mono
      * env AZURE_SPEECH_KEY           -> Azure Speech auth
      * env AZURE_SPEECH_REGION or AiModel.active().endpoint_url -> region
      * azure-cognitiveservices-speech in requirements.txt (none today)
    """

    def assess(self, *, audio_path: str, reference_text: str) -> dict:
        # TODO(real): core.audio.transcode_to_wav16k(audio_path) -> wav; then
        # SpeechConfig(key=AZURE_SPEECH_KEY, region=...) +
        # PronunciationAssessmentConfig(reference_text=reference_text,
        #   granularity=Phoneme, phoneme_alphabet="IPA", enable_miscue=True)
        # with prosody enabled; map NBest[0].PronunciationAssessment +
        # Words[].Phonemes[] into the §4.3 shape.
        raise ImproperlyConfigured(
            "AzurePronunciationBackend.assess is a stub. Set "
            "PRONUNCIATION_BACKEND=mock, or implement the Azure Speech call "
            "(AZURE_SPEECH_KEY + AZURE_SPEECH_REGION + ffmpeg)."
        )


_PRON_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING"},
        "accuracy": {"type": "NUMBER"},
        "fluency": {"type": "NUMBER"},
        "completeness": {"type": "NUMBER"},
        "prosody": {"type": "NUMBER"},
        "feedback": {"type": "STRING"},
        "words": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "word": {"type": "STRING"},
                    "accuracy": {"type": "NUMBER"},
                    "error_type": {
                        "type": "STRING",
                        "enum": ["None", "Mispronunciation", "Omission", "Insertion"],
                    },
                    "phonemes": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "phoneme": {"type": "STRING"},
                                "accuracy": {"type": "NUMBER"},
                            },
                            "required": ["phoneme", "accuracy"],
                        },
                    },
                },
                "required": ["word", "accuracy", "error_type", "phonemes"],
            },
        },
    },
    "required": [
        "transcript", "accuracy", "fluency", "completeness", "prosody",
        "feedback", "words",
    ],
}

_PRON_SYSTEM = textwrap.dedent("""
    You are a pronunciation assessment engine for English learners. You receive
    a short audio recording and the REFERENCE TEXT the student was asked to
    read. Listen carefully and score 0-100:
    - "accuracy": how closely the phonemes match a clear standard accent
      (General American or RP both fine);
    - "fluency": rhythm, pauses, hesitations, speed;
    - "completeness": share of reference words actually spoken (0-100);
    - "prosody": stress, intonation, naturalness.
    "words": one entry per REFERENCE word in order. "accuracy" per word;
    "error_type": "Omission" if not spoken, "Mispronunciation" if clearly wrong
    (accuracy < 60), else "None". "phonemes": the word's IPA phonemes, each with
    its own accuracy — mark the specific sounds that were wrong. Append any
    extra spoken words at the end with "Insertion". "transcript": what you
    actually heard. "feedback": two sentences on the most important sound(s)
    to fix. If the audio is silent or unrelated, give low completeness and say
    so in feedback. Be consistent: identical audio should yield the same scores.
""").strip()


class GeminiPronunciationBackend(BasePronunciationBackend):
    """Live assessment through Gemini's audio understanding."""

    def assess(self, *, audio_path: str, reference_text: str) -> dict:
        audio = gemini.resolve_audio(audio_path)
        user = (
            "REFERENCE TEXT: " + json.dumps(reference_text)
            + "\n\nThe student's recording is attached. Assess it."
        )
        data = gemini.generate_json(
            system=_PRON_SYSTEM, user=user, schema=_PRON_SCHEMA,
            temperature=0.1, audio=audio,
        )

        def score(key):
            return score_f(data.get(key))

        words = []
        for w in data.get("words") or []:
            err = w.get("error_type") or "None"
            words.append({
                "word": str(w.get("word", "")),
                "accuracy": float(score_f(w.get("accuracy"))),
                "error_type": None if err == "None" else err,
                "phonemes": [
                    {"phoneme": str(p.get("phoneme", "")), "accuracy": float(score_f(p.get("accuracy")))}
                    for p in (w.get("phonemes") or [])
                ],
            })
        accuracy, fluency = score("accuracy"), score("fluency")
        completeness, prosody = score("completeness"), score("prosody")
        overall = _q((accuracy + fluency + completeness + prosody) / Decimal(4))
        return {
            "overall": overall,
            "accuracy": accuracy,
            "fluency": fluency,
            "completeness": completeness,
            "prosody": prosody,
            "words": words,
            "engine": f"gemini:{gemini.model_name()}",
            "metadata": {
                "transcript": data.get("transcript", ""),
                "feedback": data.get("feedback", ""),
                "strictness": str(self.strictness),
            },
        }


def score_f(value) -> Decimal:
    try:
        v = Decimal(str(value if value is not None else 0))
    except Exception:
        v = Decimal(0)
    return _q(max(Decimal(0), min(Decimal(100), v)))


def get_pronunciation_backend():
    """Pick the backend from PRONUNCIATION_BACKEND (default 'mock'), bound to the
    active AiModel row — env-driven exactly like AI_BACKEND / get_backend()."""
    name = getattr(settings, "PRONUNCIATION_BACKEND", "mock")
    ai_model = AiModel.active()
    if name == "gemini":
        return GeminiPronunciationBackend(ai_model)
    if name == "azure":
        return AzurePronunciationBackend(ai_model)
    return MockPronunciationBackend(ai_model)


def assess_attempt(attempt) -> None:
    """Assess an uploaded attempt and persist scores onto the row. The single
    trigger point and the future Celery task body (same contract idea as
    service.evaluate_submission). Runs SYNCHRONOUSLY today."""
    backend = get_pronunciation_backend()
    result = backend.assess(
        audio_path=attempt.audio_file.path,
        reference_text=attempt.drill.target_text,
    )
    attempt.overall_score = result["overall"]
    attempt.accuracy_score = result["accuracy"]
    attempt.fluency_score = result["fluency"]
    attempt.completeness_score = result["completeness"]
    attempt.prosody_score = result.get("prosody")
    attempt.word_results = result["words"]
    attempt.engine = result.get("engine", "")
    attempt.engine_metadata = result.get("metadata")
    attempt.save(update_fields=[
        "overall_score", "accuracy_score", "fluency_score",
        "completeness_score", "prosody_score", "word_results",
        "engine", "engine_metadata",
    ])

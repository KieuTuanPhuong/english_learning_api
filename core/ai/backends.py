"""AI grading backends.

============================== STUB vs REAL ==============================
MockBackend  -> DETERMINISTIC, no network. Default everywhere. Scores derive
                from text length + a keyword rubric so tests/seed are stable.
RealBackend  -> STUBBED. Documents exactly where the OpenAI Whisper STT and
                LLM rubric calls go. Raises until wired, so a misconfigured
                deploy fails loudly instead of silently mis-grading.
=========================================================================
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# pyrefly: ignore [missing-import]
from ..models import GradingStrictness

# Strictness -> multiplier applied to the deterministic mock score.
_STRICTNESS_FACTOR = {
    GradingStrictness.LENIENT: Decimal("1.10"),
    GradingStrictness.STANDARD: Decimal("1.00"),
    GradingStrictness.STRICT: Decimal("0.85"),
}


def _clamp(score: Decimal) -> Decimal:
    score = max(Decimal("0"), min(Decimal("100"), score))
    return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class BaseAIBackend:
    """Interface every backend implements. ``ai_model`` is the active ``AiModel``
    row (or ``None``); subclasses read ``endpoint_url`` / ``strictness`` from it."""

    def __init__(self, ai_model=None):
        self.ai_model = ai_model

    @property
    def strictness(self):
        return self.ai_model.strictness if self.ai_model else GradingStrictness.STANDARD

    def grade_writing(self, submission) -> dict:
        """Reading/Writing path. Returns ``{"score": Decimal, "comments": str}``."""
        raise NotImplementedError

    def transcribe_and_score_speaking(self, submission) -> dict:
        """Listening/Speaking path. Returns
        ``{"transcript": str, "score": Decimal, "comments": str}``."""
        raise NotImplementedError


class MockBackend(BaseAIBackend):
    """Deterministic stub — matches the repo's 'mock URLs, no AI' MVP stance."""

    # Cheap keyword rubric; presence nudges the score up.
    _GOOD_MARKERS = (
        "however", "therefore", "furthermore", "in conclusion",
        "for example", "on balance", "evidence", "argue",
    )

    def grade_writing(self, submission) -> dict:
        # docs.md §6 directive #2: Reading/Writing rely on text content.
        text = (submission.writing_text or "").strip()
        words = len(text.split())
        # Base: 50 at 0 words, +1 per word up to +40, capped.
        base = Decimal(50) + min(Decimal(words), Decimal(40))
        markers = sum(1 for m in self._GOOD_MARKERS if m in text.lower())
        base += Decimal(2 * markers)
        score = _clamp(base * _STRICTNESS_FACTOR[self.strictness])
        comments = (
            f"[AI · mock] Auto-evaluated {words} words "
            f"({markers} rubric markers found). Strictness: {self.strictness}. "
            "Structure is reasonable; vary sentence openings and add a concrete "
            "example to strengthen the argument."
        )
        return {"score": score, "comments": comments}

    def transcribe_and_score_speaking(self, submission) -> dict:
        # docs.md §6 directive #2: Listening/Speaking require a media URL.
        url = submission.audio_recording_url
        if not url:
            raise ValueError("Speaking submission has no audio_recording_url")
        # Deterministic pseudo-transcript + score derived from the URL length so
        # the same submission always grades identically (stable seed/tests).
        seed = len(url) % 30
        score = _clamp((Decimal(72) + Decimal(seed)) * _STRICTNESS_FACTOR[self.strictness])
        transcript = (
            "[AI · mock transcript] (Whisper STT would transcribe the audio here.)"
        )
        comments = (
            f"[AI · mock] Pronunciation scored from a stubbed transcript. "
            f"Strictness: {self.strictness}. Clear vowels; watch the falling "
            "intonation on statement endings."
        )
        return {"transcript": transcript, "score": score, "comments": comments}


class RealBackend(BaseAIBackend):
    """STUBBED production backend. Wire these two methods to ship live grading.

    Required at deploy time (NOT needed for the mock default):
      * AiModel.active().endpoint_url  -> LLM rubric-grading service URL
      * env OPENAI_API_KEY             -> Whisper STT + LLM auth
      * a requests/httpx client in requirements.txt (none today)
    """

    def grade_writing(self, submission) -> dict:
        # TODO(real): POST submission.writing_text (+ exercise.content_text /
        # prompt_text as rubric context) to self.ai_model.endpoint_url with an
        # LLM grading prompt parameterised by self.strictness; parse score+notes.
        raise NotImplementedError(
            "RealBackend.grade_writing is a stub. Set AI_BACKEND=mock, or "
            "implement the LLM call (endpoint_url + OPENAI_API_KEY)."
        )

    def transcribe_and_score_speaking(self, submission) -> dict:
        # TODO(real): 1) download submission.audio_recording_url,
        # 2) OpenAI Whisper STT -> transcript,
        # 3) LLM pronunciation/fluency rubric -> score+comments.
        raise NotImplementedError(
            "RealBackend.transcribe_and_score_speaking is a stub. Set "
            "AI_BACKEND=mock, or implement Whisper STT + LLM scoring."
        )

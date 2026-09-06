"""AI grading backends.

============================== STUB vs REAL ==============================
MockBackend   -> DETERMINISTIC, no network. Default everywhere. Scores derive
                 from text length + a keyword rubric so tests/seed are stable.
GeminiBackend -> LIVE. ``AI_BACKEND=gemini`` + ``GEMINI_API_KEY``. Writing is
                 rubric-graded from text; Speaking is transcribed AND scored in
                 one multimodal call (audio inline) — no Whisper needed.
RealBackend   -> STUBBED. Documents exactly where the OpenAI Whisper STT and
                LLM rubric calls go. Raises until wired, so a misconfigured
                deploy fails loudly instead of silently mis-grading.
=========================================================================
"""

from __future__ import annotations

import json
import textwrap
from decimal import Decimal, ROUND_HALF_UP

# pyrefly: ignore [missing-import]
from ..models import GradingStrictness, RubricTemplate
from . import gemini

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


# ============================== Gemini (live) ==============================
_GRADE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "NUMBER"},
        "comments": {"type": "STRING"},
        "criteria": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "code": {"type": "STRING"},
                    "band": {"type": "NUMBER"},
                    "note": {"type": "STRING"},
                },
                "required": ["code", "band", "note"],
            },
        },
    },
    "required": ["score", "comments", "criteria"],
}

_SPEAK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING"},
        **_GRADE_SCHEMA["properties"],
    },
    "required": ["transcript", "score", "comments", "criteria"],
}

_STRICTNESS_TEXT = {
    GradingStrictness.LENIENT: "lenient (reward effort; round borderline bands up)",
    GradingStrictness.STANDARD: "standard (official examiner calibration)",
    GradingStrictness.STRICT: "strict (round borderline bands down; penalise every error)",
}

_GRADER_SYSTEM = textwrap.dedent("""
    You are a certified English examiner. Grade the student's response to the
    exercise. Return "score" as a percentage 0-100 of the maximum achievable
    quality for the task (IELTS band 9 == 100, band 4.5 == 50; TOEIC/CEFR
    equivalents scale the same way). If a rubric with criteria is supplied,
    assess each criterion on the rubric's native scale in "criteria" (use the
    criterion "code"), and make "score" consistent with them; otherwise return
    an empty "criteria" list. "comments" is feedback addressed to the student:
    2 concrete strengths, the 2-3 most important problems with quoted
    examples and corrections, and one next step — at most ~200 words, plain
    English suited to the student's level. Apply the grading strictness given.
    Never invent content that is not in the response.
""").strip()

_SPEAKING_SYSTEM = _GRADER_SYSTEM + textwrap.dedent("""

    The response is an AUDIO recording. First transcribe it verbatim into
    "transcript" (keep hesitations like "um" and self-corrections; write
    "[inaudible]" where needed). Then grade fluency & coherence, lexical
    resource, grammatical range & accuracy, and pronunciation from what you
    hear, and include pronunciation observations in "comments". If the audio is
    silent or unrelated, transcribe what is there, score accordingly and say so.
""")


class GeminiBackend(BaseAIBackend):
    """Live grading through Gemini (``core/ai/gemini.py``)."""

    name = "gemini"

    def _task(self, submission) -> dict:
        ex = submission.exercise
        task = {
            "title": ex.title,
            "exercise_type": ex.exercise_type,
            "band": getattr(ex, "band", None),
            "topic": getattr(ex, "topic", None),
            "prompt": (ex.prompt_text or "")[:6000],
            "passage_or_context": (ex.content_text or "")[:8000] or None,
            "grading_strictness": _STRICTNESS_TEXT[self.strictness],
        }
        template = RubricTemplate.resolve_for(ex)
        if template is not None:
            task["rubric"] = {
                "name": template.name,
                "scale": f"{template.scale_min}-{template.scale_max} step {template.score_step}",
                "criteria": [
                    {
                        "code": c.code,
                        "name": c.name,
                        "description": c.description or "",
                        "bands": [
                            {"band": str(d.band_value), "descriptor": d.descriptor}
                            for d in c.band_descriptors.all()
                        ],
                    }
                    for c in template.criteria.all()
                ],
            }
        return task

    @staticmethod
    def _finish(data: dict, engine: str) -> dict:
        try:
            score = _clamp(Decimal(str(data.get("score", 0))))
        except Exception:  # non-numeric junk from a text model
            raise gemini.GeminiError(f"{engine} returned a non-numeric score: {data.get('score')!r}")
        comments = (data.get("comments") or "").strip()
        criteria = data.get("criteria") or []
        if criteria:
            lines = [f"- {c.get('code')}: {c.get('band')} — {c.get('note', '')}".rstrip(" —") for c in criteria]
            comments = comments + "\n\nRubric:\n" + "\n".join(lines)
        return {"score": score, "comments": f"[AI · {engine}] {comments}", "criteria": criteria}

    def grade_writing(self, submission) -> dict:
        text = (submission.writing_text or "").strip()
        if not text:
            raise ValueError("Submission has no writing_text to grade")
        user = (
            "TASK (JSON):\n" + json.dumps(self._task(submission), ensure_ascii=False, indent=1)
            + "\n\nSTUDENT RESPONSE:\n" + text[:12000]
        )
        data = gemini.generate_json(
            system=_GRADER_SYSTEM, user=user, schema=_GRADE_SCHEMA, temperature=0.2,
        )
        return self._finish(data, f"gemini:{gemini.model_name()}")

    def transcribe_and_score_speaking(self, submission) -> dict:
        url = submission.audio_recording_url
        if not url:
            raise ValueError("Speaking submission has no audio_recording_url")
        audio = gemini.resolve_audio(url)  # ValueError if unreachable
        user = (
            "TASK (JSON):\n" + json.dumps(self._task(submission), ensure_ascii=False, indent=1)
            + "\n\nThe student's spoken response is the attached audio."
        )
        data = gemini.generate_json(
            system=_SPEAKING_SYSTEM, user=user, schema=_SPEAK_SCHEMA,
            temperature=0.2, audio=audio,
        )
        result = self._finish(data, f"gemini:{gemini.model_name()}")
        result["transcript"] = (data.get("transcript") or "").strip()
        return result


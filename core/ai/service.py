"""AI evaluation orchestration.

evaluate_submission() is the single trigger point. It runs SYNCHRONOUSLY today
but is the natural .delay()/enqueue boundary when a broker is added — callers
never touch backends directly.

Not every submission needs a model. :func:`instant_result` settles the cases
a model call cannot improve on — an empty submission, or a Listening/Reading/
Quiz answered against an answer key — so those callers get their Feedback row
immediately instead of waiting minutes on a queued provider.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

# pyrefly: ignore [missing-import]
from ..models import (
    AiModel,
    ExerciseType,
    Feedback,
    SubmissionStatus,
    SubmissionType,
)
from .assist import AUTO_ENGINE
from .backends import LlmBackend, MockBackend, RealBackend

_AUDIO = {ExerciseType.SPEAKING, ExerciseType.LISTENING}
_RECEPTIVE = {SubmissionType.READING, SubmissionType.LISTENING, SubmissionType.QUIZ}


def get_backend():
    """Pick the backend from AI_BACKEND (default 'mock'), bound to the active
    AiModel row so it can read endpoint_url + strictness."""
    name = getattr(settings, "AI_BACKEND", "mock")
    ai_model = AiModel.active()
    if name in ("llm", "gemini"):
        return LlmBackend(ai_model)
    return RealBackend(ai_model) if name == "real" else MockBackend(ai_model)


def instant_result(submission) -> dict | None:
    """The result for a submission that needs no model call, or ``None``.

    * Nothing submitted (no essay, no recording, no answered question) scores
      0 — a model can only confirm that, minutes later.
    * A receptive submission whose exercise carries an answer key is marked by
      the key (``Submission.grade``); the model has nothing to add to it.

    The dict has the backend shape (``score``, ``comments``) plus ``engine``.
    """
    if not submission.has_response():
        return {
            "score": Decimal("0.00"),  # two decimals, like every backend score
            "comments": (
                f"[AI · {AUTO_ENGINE}] No response was submitted for this task, "
                "so it scores 0."
            ),
            "engine": AUTO_ENGINE,
        }
    if submission.submission_type in _RECEPTIVE and submission.answers:
        detail = submission.grade_detail()
        if detail["total"]:
            score = submission.grade()
            submission.save(update_fields=["auto_score"])
            return {
                "score": score,
                "comments": (
                    f"[AI · {AUTO_ENGINE}] Marked against the answer key: "
                    f"{detail['correct']} of {detail['total']} correct."
                ),
                "engine": AUTO_ENGINE,
            }
    return None


def grade_submission(submission) -> dict:
    """Dispatch on exercise type (docs.md §6 directive #2). Returns the raw
    backend dict; does NOT persist. Callers that want the no-model shortcuts
    go through :func:`evaluate_submission` (or :func:`instant_result`)."""
    backend = get_backend()
    ex_type = submission.exercise.exercise_type
    if ex_type in _AUDIO:
        return backend.transcribe_and_score_speaking(submission)
    # Reading / Writing / Quiz -> text rubric.
    return backend.grade_writing(submission)


def _persist(submission, result) -> Feedback:
    """Write the AI Feedback row (is_ai_generated=True, reviewer=None —
    docs.md §6 directive #1), mark the submission AI_GRADED and let a mock-test
    section pick the score up."""
    fb = Feedback.objects.create(
        submission=submission,
        reviewer=None,
        is_ai_generated=True,
        score=result["score"],
        comments=result["comments"],
    )
    # Advance lifecycle. Guard the attr so this file degrades safely
    # if the status field is not yet applied.
    if hasattr(submission, "status"):
        submission.status = SubmissionStatus.AI_GRADED
        submission.save(update_fields=["status"])

    # Mock-test Writing/Speaking sections convert this score into a band.
    # Imported lazily: mock_tests consumes the AI layer's output, it is not a
    # dependency of it. No-op for ordinary submissions.
    from ..mock_tests import on_feedback_created

    on_feedback_created(fb)
    return fb


def evaluate_submission(submission) -> Feedback:
    """Run AI grading and persist an AI Feedback row. Empty submissions and
    key-marked receptive ones are answered instantly (:func:`instant_result`);
    everything else goes to the active backend.

    This is the function a future Celery/RQ task would wrap. For MVP it appends a
    new Feedback row each call (Feedback is 1->many on Submission already;
    core/models.py related_name='feedback')."""
    result = instant_result(submission) or grade_submission(submission)
    return _persist(submission, result)


def transcribe_and_score_speaking(submission) -> dict:
    """Evaluate a speaking submission, write AI Feedback, and return the scoring
    dictionary. A submission without a recording scores 0 on the spot."""
    result = instant_result(submission) or get_backend().transcribe_and_score_speaking(submission)
    _persist(submission, result)
    return {
        "score": str(result["score"]),
        "comments": result["comments"],
        "transcript": result.get("transcript", ""),
    }

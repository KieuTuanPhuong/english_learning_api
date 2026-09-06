"""AI evaluation orchestration.

evaluate_submission() is the single trigger point. It runs SYNCHRONOUSLY today
but is the natural .delay()/enqueue boundary when a broker is added — callers
never touch backends directly.
"""

from __future__ import annotations

from django.conf import settings

# pyrefly: ignore [missing-import]
from ..models import (
    AiModel,
    ExerciseType,
    Feedback,
    SubmissionStatus,
)
from .backends import GeminiBackend, MockBackend, RealBackend

_AUDIO = {ExerciseType.SPEAKING, ExerciseType.LISTENING}


def get_backend():
    """Pick the backend from AI_BACKEND (default 'mock'), bound to the active
    AiModel row so it can read endpoint_url + strictness."""
    name = getattr(settings, "AI_BACKEND", "mock")
    ai_model = AiModel.active()
    if name == "gemini":
        return GeminiBackend(ai_model)
    return RealBackend(ai_model) if name == "real" else MockBackend(ai_model)


def grade_submission(submission) -> dict:
    """Dispatch on exercise type (docs.md §6 directive #2). Returns the raw
    backend dict; does NOT persist."""
    backend = get_backend()
    ex_type = submission.exercise.exercise_type
    if ex_type in _AUDIO:
        return backend.transcribe_and_score_speaking(submission)
    # Reading / Writing / Quiz -> text rubric.
    return backend.grade_writing(submission)


def evaluate_submission(submission) -> Feedback:
    """Run AI grading and persist an AI Feedback row (is_ai_generated=True,
    reviewer=None — docs.md §6 directive #1), then mark the submission AI_GRADED.

    This is the function a future Celery/RQ task would wrap. For MVP it appends a
    new Feedback row each call (Feedback is 1->many on Submission already;
    core/models.py related_name='feedback')."""
    result = grade_submission(submission)
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


def transcribe_and_score_speaking(submission) -> dict:
    """Evaluate speaking submission, write AI Feedback, and return scoring dictionary."""
    backend = get_backend()
    result = backend.transcribe_and_score_speaking(submission)
    Feedback.objects.create(
        submission=submission,
        reviewer=None,
        is_ai_generated=True,
        score=result["score"],
        comments=result["comments"],
    )
    if hasattr(submission, "status"):
        submission.status = SubmissionStatus.AI_GRADED
        submission.save(update_fields=["status"])
    return {
        "score": str(result["score"]),
        "comments": result["comments"],
        "transcript": result.get("transcript", ""),
    }

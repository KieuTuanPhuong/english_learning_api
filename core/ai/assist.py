"""AI coaching assistants (Gemini-backed, mockable).

Two features, both read-only over existing data:

1. ``review_feedback`` — a teacher's draft (or posted) feedback is reviewed by
   the model, which returns strengths, concrete recommendations and a suggested
   rewrite. The teacher stays in control: nothing is persisted here.
2. ``explain_mistakes`` — a student's submission is analysed and each mistake
   is explained in plain language with a correction and a tip. Views persist
   the result as an ``AiInsight`` row so re-opening the page is free.

Backend switch mirrors ``core/ai/backends.py``: ``AI_ASSIST_BACKEND=mock``
(deterministic, offline — the test default) or ``gemini`` (live call through
``core/ai/gemini.py``). Both backends receive the same context dict built by
``build_context``; the model never sees raw ORM objects.
"""

from __future__ import annotations

import json
import textwrap

from django.conf import settings

# pyrefly: ignore [missing-import]
from ..models import (
    RubricTemplate,
    SubmissionType,
    _blanks_match,
    blank_answer_key,
    normalize_answers,
    strip_blank_answers,
)
from . import gemini

_MAX_TEXT = 6000  # chars of student text sent to the model

MISTAKE_CATEGORIES = [
    "grammar", "vocabulary", "spelling", "punctuation", "coherence",
    "task_response", "comprehension", "inference", "detail", "other",
]
REVIEW_AREAS = [
    "specificity", "tone", "actionability", "accuracy", "coverage",
    "score_alignment", "language_level",
]

# --------------------------------------------------------------- schemas
REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "rating": {"type": "INTEGER"},
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "recommendations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "area": {"type": "STRING", "enum": REVIEW_AREAS},
                    "issue": {"type": "STRING"},
                    "suggestion": {"type": "STRING"},
                },
                "required": ["area", "issue", "suggestion"],
            },
        },
        "score_alignment": {"type": "STRING"},
        "suggested_comment": {"type": "STRING"},
    },
    "required": [
        "summary", "rating", "strengths", "recommendations",
        "score_alignment", "suggested_comment",
    ],
}

MISTAKES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "mistakes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "location": {"type": "STRING"},
                    "student_answer": {"type": "STRING"},
                    "correction": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": MISTAKE_CATEGORIES},
                    "explanation": {"type": "STRING"},
                    "tip": {"type": "STRING"},
                },
                "required": [
                    "location", "student_answer", "correction", "category",
                    "explanation", "tip",
                ],
            },
        },
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "practice_suggestions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "mistakes", "strengths", "practice_suggestions"],
}


# --------------------------------------------------------------- context
def _clip(text, limit=_MAX_TEXT):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def _receptive_breakdown(submission) -> list[dict]:
    """Per-question view of a reading/listening/quiz submission: what was
    asked, what the key is, what the student chose, and whether it matched.
    Reuses the exact matching rules of ``Submission.grade_detail``."""
    given_by_q = normalize_answers(submission.answers)
    rows = []
    for q in submission.exercise.questions.prefetch_related("options"):
        options = list(q.options.all())
        key_ids = {o.id for o in options if o.is_correct}
        blanks = blank_answer_key(q.text)
        given = given_by_q.get(q.id, {})
        row: dict = {
            "question_id": q.id,
            "order": q.order,
            "question": (
                strip_blank_answers(q.text).replace("[[]]", "___") if blanks else q.text
            ),
        }
        if options:
            row["options"] = [o.text for o in options]
            row["correct_options"] = [o.text for o in options if o.is_correct]
            chosen = [o.text for o in options if o.id in set(given.get("option_ids", ()))]
            row["student_answer"] = chosen
            row["is_correct"] = set(given.get("option_ids", ())) == key_ids if key_ids else None
        elif blanks:
            row["correct_answers"] = [alts[0] for alts in blanks]
            row["student_answer"] = given.get("text")
            row["is_correct"] = _blanks_match(blanks, given.get("text"), max_words=q.max_words)
        else:
            row["student_answer"] = given.get("text")
            row["is_correct"] = None  # open-ended; no key
        rows.append(row)
    return rows


def build_context(submission) -> dict:
    """Everything the model needs about one submission, as plain data."""
    exercise = submission.exercise
    ctx: dict = {
        "submission_id": submission.id,
        "submission_type": submission.submission_type,
        "exercise": {
            "title": exercise.title,
            "type": exercise.exercise_type,
            "band": getattr(exercise, "band", None),
            "topic": getattr(exercise, "topic", None),
            "prompt": _clip(exercise.prompt_text),
            "passage": _clip(exercise.content_text, 8000),
        },
        "auto_score": str(submission.auto_score) if submission.auto_score is not None else None,
    }
    if submission.submission_type in (SubmissionType.WRITING, SubmissionType.SPEAKING):
        ctx["writing_text"] = _clip(submission.writing_text)
        ctx["audio_recording_url"] = submission.audio_recording_url
    else:
        ctx["questions"] = _receptive_breakdown(submission)

    template = RubricTemplate.resolve_for(exercise)
    if template is not None:
        ctx["rubric"] = {
            "name": template.name,
            "scale": f"{template.scale_min}-{template.scale_max}",
            "criteria": [
                {"code": c.code, "name": c.name, "description": c.description or ""}
                for c in template.criteria.all()
            ],
        }
    annotations = [
        {
            "quoted_text": a.quoted_text,
            "category": a.category,
            "comment": a.comment,
            "suggested_correction": a.suggested_correction,
        }
        for a in submission.annotations.all()[:40]
    ]
    if annotations:
        ctx["teacher_annotations"] = annotations
    feedback_rows = [
        {
            "by": "ai" if f.is_ai_generated else "teacher",
            "score": str(f.score) if f.score is not None else None,
            "comments": _clip(f.comments, 1500),
        }
        for f in submission.feedback.all().order_by("id")[:10]
    ]
    if feedback_rows:
        ctx["existing_feedback"] = feedback_rows
    return ctx


# --------------------------------------------------------------- backends
class BaseAssistBackend:
    def review_feedback(self, ctx: dict, draft: dict) -> dict:
        raise NotImplementedError

    def explain_mistakes(self, ctx: dict) -> dict:
        raise NotImplementedError


class MockAssistBackend(BaseAssistBackend):
    """Deterministic, offline. Shapes match the Gemini schemas exactly so the
    frontend and tests never depend on a live key."""

    name = "mock"

    def review_feedback(self, ctx, draft):
        comments = (draft.get("comments") or "").strip()
        words = len(comments.split())
        recs = []
        if words < 20:
            recs.append({
                "area": "specificity",
                "issue": "The comment is very short and does not point to concrete parts of the response.",
                "suggestion": "Quote one sentence from the student's work and explain what to change and why.",
            })
        if not any(w in comments.lower() for w in ("try", "next", "practice", "should", "could")):
            recs.append({
                "area": "actionability",
                "issue": "No clear next step for the student.",
                "suggestion": "End with one specific action, e.g. 'Next time, write a topic sentence for each paragraph.'",
            })
        if draft.get("score") is None and not draft.get("criterion_scores"):
            recs.append({
                "area": "score_alignment",
                "issue": "No score was given, so the student cannot gauge progress.",
                "suggestion": "Add a score or rubric bands so the comment and the grade reinforce each other.",
            })
        rating = max(1, min(5, 2 + (words >= 20) + (words >= 60) + (not recs)))
        return {
            "summary": f"[AI · mock] Reviewed a {words}-word comment with {len(recs)} recommendation(s).",
            "rating": rating,
            "strengths": ["Feedback was provided promptly." ] + (["Comment has reasonable length."] if words >= 20 else []),
            "recommendations": recs,
            "score_alignment": "Mock backend cannot judge alignment; enable Gemini for a real assessment.",
            "suggested_comment": (
                comments + ("\n\n" if comments else "")
                + "Next step: pick one paragraph and rewrite it with a clear topic sentence."
            ),
        }

    def explain_mistakes(self, ctx):
        mistakes = []
        for q in ctx.get("questions", []):
            if q.get("is_correct") is False:
                key = q.get("correct_options") or q.get("correct_answers") or []
                mistakes.append({
                    "location": f"Question {q.get('order', q['question_id'])}",
                    "student_answer": json.dumps(q.get("student_answer")),
                    "correction": ", ".join(map(str, key)),
                    "category": "comprehension",
                    "explanation": "[AI · mock] The chosen answer does not match the key for this question.",
                    "tip": "Re-read the part of the passage that this question refers to before answering.",
                })
        text = ctx.get("writing_text") or ""
        if text and " go to " in f" {text} ":
            mistakes.append({
                "location": "go to",
                "student_answer": "go",
                "correction": "went / goes (check tense)",
                "category": "grammar",
                "explanation": "[AI · mock] Verb tense may not match the time reference.",
                "tip": "Underline time words (yesterday, last week) and match the verb tense to them.",
            })
        return {
            "summary": f"[AI · mock] Found {len(mistakes)} mistake(s) to review.",
            "mistakes": mistakes,
            "strengths": ["You completed the task."],
            "practice_suggestions": ["Review the explanations above, then retry a similar exercise."],
        }


_REVIEW_SYSTEM = textwrap.dedent("""
    You are an experienced English-language teacher trainer and assessment
    moderator. A teacher has written feedback on a student's English exercise.
    Review the TEACHER'S FEEDBACK (not the student's work) and help the teacher
    make it more useful. Judge: specificity (does it point to concrete evidence
    in the student's response?), tone (encouraging yet honest), actionability
    (clear next steps), accuracy (is the feedback linguistically correct?),
    coverage (does it address the main strengths and weaknesses of the
    response, including rubric criteria when given?), score alignment (does the
    score/band fit the quality of the response? NOTE: the holistic "score" is
    always a 0-100 percentage of maximum quality, separate from any rubric band
    scale — never flag a mismatch merely because the rubric uses bands; judge
    whether the percentage is fair), and language level (is the wording
    understandable for the student's level?).
    Be concise and concrete. Give 2-5 recommendations, most important first.
    "rating" is 1 (poor) to 5 (excellent). "suggested_comment" is an improved
    version of the teacher's comment written in the teacher's voice, ready to
    send to the student, at most ~180 words. Do not invent facts about the
    student's response that are not in the provided data.
""").strip()

_MISTAKES_SYSTEM = textwrap.dedent("""
    You are a patient English tutor. Analyse the student's submission to an
    English exercise and explain every real mistake in simple, friendly
    English a learner can understand. For receptive tasks (reading, listening,
    quiz) use the per-question data: explain WHY the correct answer is right
    and why the student's choice is wrong, referring to the passage or prompt.
    For writing tasks, find grammar, vocabulary, spelling, punctuation,
    coherence and task-response problems; quote the exact words in "location",
    give the corrected form and a one-sentence rule or tip. Order mistakes by
    importance. Do not pad: if the work is correct, return an empty mistakes
    list and say so in the summary. Use existing teacher annotations/feedback
    as hints but verify them yourself. Keep each explanation under 60 words.
    "practice_suggestions": 2-4 short, concrete follow-up activities.
""").strip()


class GeminiAssistBackend(BaseAssistBackend):
    name = "gemini"

    def review_feedback(self, ctx, draft):
        user = (
            "STUDENT SUBMISSION CONTEXT (JSON):\n"
            + json.dumps(ctx, ensure_ascii=False, indent=1)
            + "\n\nTEACHER'S FEEDBACK TO REVIEW (JSON):\n"
            + json.dumps(draft, ensure_ascii=False, indent=1)
        )
        data = gemini.generate_json(
            system=_REVIEW_SYSTEM, user=user, schema=REVIEW_SCHEMA, temperature=0.3,
        )
        data["rating"] = max(1, min(5, int(data.get("rating") or 3)))
        return data

    def explain_mistakes(self, ctx):
        user = "STUDENT SUBMISSION (JSON):\n" + json.dumps(ctx, ensure_ascii=False, indent=1)
        return gemini.generate_json(
            system=_MISTAKES_SYSTEM, user=user, schema=MISTAKES_SCHEMA, temperature=0.2,
        )


def get_assist_backend() -> BaseAssistBackend:
    name = getattr(settings, "AI_ASSIST_BACKEND", "mock")
    return GeminiAssistBackend() if name == "gemini" else MockAssistBackend()


def engine_label() -> str:
    backend = get_assist_backend()
    return f"gemini:{gemini.model_name()}" if backend.name == "gemini" else "mock"


# --------------------------------------------------------------- public API
def review_feedback(submission, *, score=None, comments="", criterion_scores=None) -> dict:
    """Review a teacher's (draft) feedback on ``submission``. ``criterion_scores``
    is a list of ``{"criterion": <RubricCriterion>|code, "score", "note"}``.
    Returns the schema dict plus ``engine``. Does not persist."""
    ctx = build_context(submission)
    draft = {
        # The holistic score is a 0-100 percentage, independent of any rubric
        # band scale in the context; say so or the model flags a false mismatch.
        "score": str(score) if score is not None else None,
        "score_scale": "0-100 percentage (not a rubric band)",
        "comments": _clip(comments, 4000),
    }
    if criterion_scores:
        draft["criterion_scores_scale"] = "rubric native band scale (see context.rubric.scale)"
        draft["criterion_scores"] = [
            {
                "criterion": getattr(row.get("criterion"), "code", row.get("criterion")),
                "score": str(row.get("score")),
                "note": row.get("note") or "",
            }
            for row in criterion_scores
        ]
    result = get_assist_backend().review_feedback(ctx, draft)
    result["engine"] = engine_label()
    return result


def explain_mistakes(submission) -> dict:
    """Explain the mistakes in ``submission``. Speaking submissions have no
    transcript to analyse yet, so they are rejected with ``ValueError``."""
    if submission.submission_type == SubmissionType.SPEAKING:
        raise ValueError(
            "Mistake explanation needs text; speaking submissions are not "
            "supported until transcription is available."
        )
    if submission.submission_type == SubmissionType.WRITING and not (submission.writing_text or "").strip():
        raise ValueError("This writing submission has no text to analyse.")
    if submission.submission_type not in (SubmissionType.WRITING,) and not submission.answers:
        raise ValueError("This submission has no answers to analyse.")
    ctx = build_context(submission)
    result = get_assist_backend().explain_mistakes(ctx)
    result["engine"] = engine_label()
    return result

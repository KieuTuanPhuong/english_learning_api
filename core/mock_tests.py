"""Mock-test attempt engine — state machine, server-authoritative timing, and
score conversion (docs/research/01-mock-tests.md §4.2/§4.3).

Every rule that must not be negotiable from the client lives here rather than in
a view: section ordering, the expiry clock, and the raw -> band/scaled mapping.
Views are thin wrappers that translate the exceptions below into HTTP codes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (
    AttemptMode,
    AttemptStatus,
    Exercise,
    ItemFlow,
    MockTestTemplate,
    OverallStrategy,
    PRODUCTIVE_SKILLS,
    Question,
    QuestionOption,
    RECEPTIVE_SKILLS,
    ScoreConversionTable,
    SectionAttempt,
    SectionStatus,
    SectionSubmission,
    Submission,
    TestAttempt,
    TestFormat,
    TestSection,
    TestSectionExercise,
)

# Network latency / last-keystroke allowance on top of the advertised duration.
# The client counts down to `expires_at`, which already includes it.
GRACE = timedelta(seconds=30)

# How long an expired-but-unsubmitted section is left alone before the sweeper
# force-finalizes it, so a student who reconnects can still press Submit.
SWEEP_AFTER = timedelta(minutes=10)


class MockTestError(Exception):
    """Domain rule violation. ``code`` is the machine-readable reason the web
    client switches on (notably ``section_expired``)."""

    def __init__(self, message, code="invalid_state"):
        super().__init__(message)
        self.code = code


class SectionExpired(MockTestError):
    def __init__(self, message="This section's time has run out."):
        super().__init__(message, code="section_expired")


# ---------------------------------------------------------------- authoring
@transaction.atomic
def duplicate_template(source: MockTestTemplate, author) -> MockTestTemplate:
    """Copy a template's structure into a new, unattempted one.

    A template that anyone has sat is frozen: ``SectionAttempt.section`` is
    PROTECTed, so replacing its sections would orphan real score reports. That
    is the correct rule, and it makes duplication the way to revise a test —
    copy, edit the copy, retire the original.

    Exercises are *shared*, not copied. They are independently owned content
    that a teacher may also use outside any test; forking one on every duplicate
    would scatter near-identical passages through the library. Import is the
    path that does copy them, because there the source content does not exist
    locally at all.
    """
    copy = MockTestTemplate.objects.create(
        format=source.format,
        title=f"{source.title} (copy)",
        description=source.description,
        difficulty_level=source.difficulty_level,
        created_by=author,
    )
    for section in source.sections.all():
        new_section = TestSection.objects.create(
            template=copy,
            skill=section.skill,
            title=section.title,
            order=section.order,
            duration_minutes=section.duration_minutes,
            instructions=section.instructions,
            item_flow=section.item_flow,
        )
        TestSectionExercise.objects.bulk_create([
            TestSectionExercise(
                section=new_section,
                exercise=item.exercise,
                order=item.order,
                weight=item.weight,
                prep_seconds=item.prep_seconds,
                max_record_seconds=item.max_record_seconds,
            )
            for item in section.items.all()
        ])
    return copy


def export_template(template: MockTestTemplate) -> dict:
    """Serialize a template into a portable document (see
    ``serializers.MockTestDocumentSerializer``). Exercises are inlined by
    content — answer keys included — so the result can be imported into an
    environment where none of these ids exist."""
    return {
        "format_slug": template.format.slug,
        "title": template.title,
        "description": template.description,
        "difficulty_level": template.difficulty_level,
        "sections": [
            {
                "skill": section.skill,
                "title": section.title,
                "order": section.order,
                "duration_minutes": section.duration_minutes,
                "instructions": section.instructions,
                "item_flow": section.item_flow,
                "items": [
                    {
                        "order": item.order,
                        "weight": item.weight,
                        "prep_seconds": item.prep_seconds,
                        "max_record_seconds": item.max_record_seconds,
                        "exercise": {
                            "title": item.exercise.title,
                            "exercise_type": item.exercise.exercise_type,
                            "prompt_text": item.exercise.prompt_text,
                            "content_text": item.exercise.content_text,
                            "audio_prompt_url": item.exercise.audio_prompt_url,
                            "questions": [
                                {
                                    "text": question.text,
                                    "order": question.order,
                                    "max_words": question.max_words,
                                    "options": [
                                        {
                                            "text": option.text,
                                            "is_correct": option.is_correct,
                                            "order": option.order,
                                        }
                                        for option in question.options.all()
                                    ],
                                }
                                for question in item.exercise.questions.all()
                            ],
                        },
                    }
                    for item in section.items.all()
                ],
            }
            for section in template.sections.all()
        ],
    }


@transaction.atomic
def import_templates(documents, author) -> list:
    """Create templates from exported documents — the bulk-import path.

    Atomic across the whole batch: a partially imported set of tests is worse
    than none, because the failure is invisible in a catalogue that looks full.
    """
    return [_import_one(document, author) for document in documents]


def _import_one(document, author) -> MockTestTemplate:
    fmt = TestFormat.objects.filter(slug=document["format_slug"]).first()
    if fmt is None:
        raise MockTestError(
            f"Unknown test format '{document['format_slug']}'. Seed the format "
            "before importing tests that score against it.",
            code="unknown_format",
        )

    template = MockTestTemplate.objects.create(
        format=fmt,
        title=document["title"],
        description=document.get("description"),
        difficulty_level=document.get("difficulty_level") or None,
        created_by=author,
    )
    for index, section_doc in enumerate(document["sections"]):
        section = TestSection.objects.create(
            template=template,
            skill=section_doc["skill"],
            title=section_doc["title"],
            order=section_doc.get("order", index),
            duration_minutes=section_doc["duration_minutes"],
            instructions=section_doc.get("instructions"),
            item_flow=section_doc.get("item_flow", ItemFlow.FREE),
        )
        for item_index, item_doc in enumerate(section_doc["items"]):
            exercise = _import_exercise(item_doc["exercise"], author)
            TestSectionExercise.objects.create(
                section=section,
                exercise=exercise,
                order=item_doc.get("order", item_index),
                weight=item_doc.get("weight", 1),
                prep_seconds=item_doc.get("prep_seconds"),
                max_record_seconds=item_doc.get("max_record_seconds"),
            )
    return template


def _import_exercise(exercise_doc, author) -> Exercise:
    """Always creates a fresh Exercise. Matching an existing one by title would
    quietly re-point an import at unrelated content that happens to share a
    name — and a wrong reading passage is a wrong test."""
    exercise = Exercise.objects.create(
        title=exercise_doc["title"],
        exercise_type=exercise_doc["exercise_type"],
        prompt_text=exercise_doc.get("prompt_text") or "",
        content_text=exercise_doc.get("content_text"),
        audio_prompt_url=exercise_doc.get("audio_prompt_url"),
        created_by=author,
    )
    for q_index, question_doc in enumerate(exercise_doc.get("questions") or []):
        question = Question.objects.create(
            exercise=exercise,
            text=question_doc["text"],
            order=question_doc.get("order", q_index),
            max_words=question_doc.get("max_words"),
        )
        QuestionOption.objects.bulk_create([
            QuestionOption(
                question=question,
                text=option_doc["text"],
                is_correct=option_doc.get("is_correct", False),
                order=option_doc.get("order", o_index),
            )
            for o_index, option_doc in enumerate(question_doc.get("options") or [])
        ])
    return exercise


# ---------------------------------------------------------------- attempts
@transaction.atomic
def start_attempt(
    template: MockTestTemplate, student, mode: str = AttemptMode.EXAM
) -> TestAttempt:
    """Create an attempt plus one `not_started` SectionAttempt per section.

    Retake policy is unlimited (research doc open question 6), but a student may
    hold only one *unfinished* attempt per template — reopening the catalog
    resumes it instead of silently abandoning half-finished work.

    ``mode`` is fixed at creation (see :class:`AttemptMode`). A resumed attempt
    keeps the mode it started with: an unfinished exam-order sitting must not
    quietly become free-order because the student re-entered through a
    different button.
    """
    sections = list(template.sections.all())
    if not sections:
        raise MockTestError("This test has no sections yet.", code="empty_template")

    existing = (
        TestAttempt.objects
        .filter(template=template, student=student,
                status=AttemptStatus.IN_PROGRESS)
        .first()
    )
    if existing:
        return existing

    if mode not in AttemptMode.values:
        raise MockTestError(f"Unknown attempt mode '{mode}'.", code="bad_mode")

    attempt = TestAttempt.objects.create(
        template=template, student=student, mode=mode,
    )
    SectionAttempt.objects.bulk_create([
        SectionAttempt(attempt=attempt, section=section) for section in sections
    ])
    return attempt


# ---------------------------------------------------------------- sections
@transaction.atomic
def start_section(attempt: TestAttempt, section_attempt_id: int) -> SectionAttempt:
    """Stamp the server clock on a section. Allowed only when the attempt is in
    progress, every lower-order section is completed, and no other section is
    running. The attempt row is locked so a double-clicked "Start section"
    cannot mint two clocks."""
    attempt = TestAttempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise MockTestError("This attempt is already finished.")

    target = _get_section_attempt(attempt, section_attempt_id)
    if target.status == SectionStatus.IN_PROGRESS:
        return target  # idempotent: re-issuing start returns the live clock
    if target.status == SectionStatus.COMPLETED:
        raise MockTestError("This section is already submitted.")

    siblings = list(
        attempt.sections.select_related("section").order_by("section__order", "id")
    )
    # One clock at a time, in both modes: two sections running at once would
    # mean two timers the student cannot both attend to, and free order is
    # about choosing what to sit next, not sitting several things at once.
    if any(sa.status == SectionStatus.IN_PROGRESS for sa in siblings):
        raise MockTestError("Finish the section in progress first.")
    if attempt.mode == AttemptMode.EXAM:
        for sibling in siblings:
            if sibling.section.order >= target.section.order:
                break
            if sibling.status != SectionStatus.COMPLETED:
                raise MockTestError("Sections must be taken in order.")

    now = timezone.now()
    target.status = SectionStatus.IN_PROGRESS
    target.started_at = now
    target.expires_at = (
        now + timedelta(minutes=target.section.duration_minutes) + GRACE
    )
    if target.draft_answers is None:
        target.draft_answers = normalize_draft(None)
    target.save(update_fields=[
        "status", "started_at", "expires_at", "draft_answers",
    ])
    return target


def autosave(attempt: TestAttempt, section_attempt_id: int, draft: dict) -> SectionAttempt:
    """Replace the section's draft envelope. Rejected once the clock is up so a
    late write cannot buy time — the last accepted draft is what gets graded.

    In a ``sequential`` section, a write to a part the student has not reached
    (or has already finished) is refused here rather than merely hidden by the
    client: part gating is an exam rule, and exam rules live server-side.
    """
    target = _get_section_attempt(attempt, section_attempt_id)
    if target.status != SectionStatus.IN_PROGRESS:
        raise MockTestError("This section is not in progress.")
    if target.expires_at and timezone.now() > target.expires_at:
        raise SectionExpired()

    incoming = normalize_draft(draft)
    section = target.section
    if section.item_flow == ItemFlow.SEQUENTIAL:
        stored = normalize_draft(target.draft_answers)
        # The cursor only ever moves forward, and only through advance_item():
        # a client cannot unlock the next part by editing its own draft.
        incoming["meta"]["completed_items"] = stored["meta"]["completed_items"]
        allowed = {str(eid) for eid in open_item_ids(section, stored)}
        for bucket in ("answers", "writing"):
            for key in incoming[bucket]:
                if key not in allowed:
                    raise MockTestError(
                        "That part of this section is not open.",
                        code="item_locked",
                    )
            # Finished parts keep whatever the student last saved for them.
            for key, value in stored[bucket].items():
                incoming[bucket].setdefault(key, value)

    target.draft_answers = incoming
    target.save(update_fields=["draft_answers"])
    return target


@transaction.atomic
def advance_item(
    attempt: TestAttempt, section_attempt_id: int, exercise_id: int
) -> SectionAttempt:
    """Close one part of a ``sequential`` section and open the next.

    Idempotent: re-sending an already-closed part is a no-op, so a double-tapped
    "Next part" cannot skip a recording. Closing a part is one-way — this is the
    "no going back" rule that makes a Listening section honest.
    """
    target = _get_section_attempt(attempt, section_attempt_id)
    if target.status != SectionStatus.IN_PROGRESS:
        raise MockTestError("This section is not in progress.")
    section = target.section
    if section.item_flow != ItemFlow.SEQUENTIAL:
        raise MockTestError("This section's parts are all open already.")

    draft = normalize_draft(target.draft_answers)
    done = draft["meta"]["completed_items"]
    if exercise_id in done:
        return target
    open_now = open_item_ids(section, draft)
    if exercise_id not in open_now:
        raise MockTestError(
            "That part of this section is not open.", code="item_locked"
        )

    done.append(exercise_id)
    target.draft_answers = draft
    target.save(update_fields=["draft_answers"])
    return target


@transaction.atomic
def submit_section(
    attempt: TestAttempt,
    section_attempt_id: int,
    draft: dict | None = None,
    recordings: dict | None = None,
) -> SectionAttempt:
    """Close a section: persist the final draft, create one ordinary Submission
    per exercise, grade the receptive ones, and convert the raw score.

    Accepted even after ``expires_at`` — late *writes* were already refused, so
    the stored draft is honest — but in that case the incoming ``draft`` is
    ignored in favour of what the server last accepted.
    """
    attempt = TestAttempt.objects.select_for_update().get(pk=attempt.pk)
    target = _get_section_attempt(attempt, section_attempt_id)
    if target.status == SectionStatus.COMPLETED:
        raise MockTestError("This section is already submitted.")
    if target.status != SectionStatus.IN_PROGRESS:
        raise MockTestError("Start this section before submitting it.")

    expired = bool(target.expires_at and timezone.now() > target.expires_at)
    if draft is not None and not expired:
        target.draft_answers = normalize_draft(draft)

    _finalize_section(target, recordings=recordings or {})
    _maybe_complete_attempt(attempt)
    return target


def _finalize_section(section_attempt: SectionAttempt, recordings: dict) -> None:
    """Create + grade Submissions for a section, then stamp its scores. Shared
    by the submit endpoint and the expiry sweeper."""
    section = section_attempt.section
    draft = normalize_draft(section_attempt.draft_answers)
    student = section_attempt.attempt.student

    correct = gradable = 0
    for item in section.items.select_related("exercise").all():
        exercise = item.exercise
        key = str(exercise.id)
        submission = Submission.objects.create(
            exercise=exercise,
            student=student,
            assignment=None,
            submission_type=exercise.exercise_type,
            answers=draft["answers"].get(key),
            writing_text=draft["writing"].get(key),
            audio_recording_url=recordings.get(key),
        )
        if section.skill in RECEPTIVE_SKILLS:
            detail = submission.grade_detail()
            submission.grade()
            submission.save(update_fields=["auto_score"])
            correct += detail["correct"]
            gradable += detail["total"]
        SectionSubmission.objects.create(
            section_attempt=section_attempt, submission=submission
        )

    now = timezone.now()
    section_attempt.status = SectionStatus.COMPLETED
    section_attempt.completed_at = now
    if section.skill in RECEPTIVE_SKILLS:
        section_attempt.raw_score = correct
        section_attempt.raw_max = gradable
        section_attempt.converted_score = convert_score(
            section_attempt.attempt.template.format, section.skill,
            correct, raw_max=gradable,
        )
    section_attempt.save(update_fields=[
        "status", "completed_at", "raw_score", "raw_max", "converted_score",
    ])


def expire_stale_sections(now=None) -> int:
    """Force-finalize sections whose clock ran out and were never submitted, so
    an abandoned attempt still produces a report. Driven by the
    ``expire_section_attempts`` management command (cron today, Celery beat
    later). Returns the number of sections finalized."""
    now = now or timezone.now()
    stale = (
        SectionAttempt.objects
        .filter(status=SectionStatus.IN_PROGRESS,
                expires_at__lt=now - SWEEP_AFTER)
        .select_related("section", "attempt", "attempt__template__format")
    )
    count = 0
    for section_attempt in stale:
        with transaction.atomic():
            _finalize_section(section_attempt, recordings={})
            _maybe_complete_attempt(section_attempt.attempt)
        count += 1
    return count


def _maybe_complete_attempt(attempt: TestAttempt) -> None:
    """Mark the attempt completed once every section is in, then compute the
    overall score if all sections already have one (receptive-only tests finish
    immediately; W/S tests wait for grading — see ``on_feedback_created``)."""
    if attempt.sections.exclude(status=SectionStatus.COMPLETED).exists():
        return
    attempt.status = AttemptStatus.COMPLETED
    attempt.completed_at = attempt.completed_at or timezone.now()
    attempt.save(update_fields=["status", "completed_at"])
    recompute_overall(attempt)


# ---------------------------------------------------------------- scoring
def convert_score(test_format, skill, raw, raw_max=None) -> Decimal | None:
    """Map a raw value to a band/scaled score through the format's conversion
    table. Tables are sparse by design (an IELTS table lists only the boundary
    raw counts), so a missing key falls back to the nearest *lower* key.

    ``raw_max`` is how many questions the section could actually award.
    Conversion tables are calibrated for real exam length (IELTS 40, TOEIC 100
    per section), so a shorter practice test is rescaled onto that domain
    first — otherwise 8 correct out of 10 would be looked up as "8 out of 40"
    and come back as band 3.5. Full-length sections are unaffected: the scale
    factor is then 1.
    """
    if raw is None:
        return None
    table = ScoreConversionTable.objects.filter(
        format=test_format, skill=skill
    ).first()
    if table is None or not isinstance(table.mapping, dict):
        return None

    keys = []
    for key in table.mapping:
        try:
            keys.append(int(key))
        except (TypeError, ValueError):
            continue
    if not keys:
        return None

    raw = int(raw)
    table_max = max(keys)
    if raw_max and int(raw_max) > 0 and int(raw_max) != table_max:
        raw = int(
            (Decimal(raw) / Decimal(int(raw_max)) * Decimal(table_max))
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

    candidates = [k for k in keys if k <= raw]
    if not candidates:
        return None
    try:
        return Decimal(str(table.mapping[str(max(candidates))]))
    except (KeyError, ArithmeticError, ValueError):
        return None


def on_feedback_created(feedback) -> None:
    """Fill a productive section's converted score once a Feedback row lands.

    Called from the teacher grading path and the AI evaluation path; a no-op for
    submissions that are not part of a mock test. Feedback score is 0-100 and
    converts through the same per-skill table machinery.
    """
    link = SectionSubmission.objects.filter(
        submission=feedback.submission
    ).select_related(
        "section_attempt__section", "section_attempt__attempt__template__format"
    ).first()
    if link is None or feedback.score is None:
        return

    section_attempt = link.section_attempt
    if section_attempt.section.skill not in PRODUCTIVE_SKILLS:
        return

    # A productive section can span several tasks, and they need not count
    # equally: IELTS Writing Task 2 is worth twice Task 1, so the section score
    # is the *weighted* mean of whatever has been graded so far —
    # (T1 + 2*T2) / 3 — using TestSectionExercise.weight (default 1).
    weights = {
        item.exercise_id: Decimal(item.weight)
        for item in section_attempt.section.items.all()
    }
    weighted_total = Decimal("0")
    weight_total = Decimal("0")
    for sibling in section_attempt.submissions.select_related("submission"):
        latest = sibling.submission.feedback.order_by("-created_at", "-id").first()
        if latest is None or latest.score is None:
            continue
        weight = weights.get(sibling.submission.exercise_id, Decimal("1"))
        weighted_total += Decimal(latest.score) * weight
        weight_total += weight
    if weight_total <= 0:
        return

    mean = weighted_total / weight_total
    section_attempt.converted_score = convert_score(
        section_attempt.attempt.template.format,
        section_attempt.section.skill,
        int(mean.to_integral_value(rounding=ROUND_HALF_UP)),
    )
    section_attempt.save(update_fields=["converted_score"])
    recompute_overall(section_attempt.attempt)


def recompute_overall(attempt: TestAttempt) -> Decimal | None:
    """Overall score once every section has a converted score, per the format's
    strategy: ``band_average`` = mean rounded to the nearest half band (the
    IELTS rule); ``scaled_sum`` = plain sum (TOEIC); ``mean_percent`` = plain
    mean to two decimals (custom formats, where half-band rounding would be a
    borrowed convention that means nothing). Left ``None`` while the report is
    still partial."""
    sections = list(attempt.sections.all())
    scores = [sa.converted_score for sa in sections]
    if not sections or any(score is None for score in scores):
        return None

    strategy = attempt.template.format.overall_strategy
    if strategy == OverallStrategy.SCALED_SUM:
        overall = sum(scores, Decimal("0"))
    elif strategy == OverallStrategy.MEAN_PERCENT:
        overall = sum(scores, Decimal("0")) / Decimal(len(scores))
    else:  # BAND_AVERAGE
        mean = sum(scores, Decimal("0")) / Decimal(len(scores))
        overall = (mean * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2

    attempt.overall_score = Decimal(overall).quantize(Decimal("0.01"))
    attempt.save(update_fields=["overall_score"])
    return attempt.overall_score


# ---------------------------------------------------------------- helpers
def normalize_draft(draft) -> dict:
    """Coerce an autosave payload into the canonical envelope, dropping anything
    unrecognised. Keys are exercise ids as strings (JSON has no int keys)."""
    draft = draft if isinstance(draft, dict) else {}
    answers = draft.get("answers")
    writing = draft.get("writing")
    meta = draft.get("meta")
    played = meta.get("audio_played") if isinstance(meta, dict) else None
    # Which parts the student has finished, in the order they finished them.
    # Only meaningful for a `sequential` section; ignored for `free` ones.
    # Order matters (it is the progress cursor), so this is NOT sorted like
    # `audio_played` — it is de-duplicated while keeping first appearance.
    done = meta.get("completed_items") if isinstance(meta, dict) else None
    return {
        "answers": {
            str(k): v for k, v in answers.items()
        } if isinstance(answers, dict) else {},
        "writing": {
            str(k): v for k, v in writing.items() if isinstance(v, str)
        } if isinstance(writing, dict) else {},
        "meta": {
            "audio_played": sorted({
                int(x) for x in played if isinstance(x, int)
            }) if isinstance(played, list) else [],
            "completed_items": _dedupe_ints(done),
        },
    }


def _dedupe_ints(values) -> list:
    """Ints from `values`, de-duplicated, first appearance wins, order kept."""
    if not isinstance(values, list):
        return []
    seen, out = set(), []
    for value in values:
        if isinstance(value, int) and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def open_item_ids(section, draft) -> list:
    """Exercise ids the student may write to right now.

    A ``free`` section opens everything at once — IELTS Reading really is three
    passages in one 60-minute block, and Writing really is two tasks the student
    flips between. A ``sequential`` section opens exactly one part: the first
    that is not in ``meta.completed_items``. Listening plays four recordings in
    order with no going back, and a Speaking interview does not let you answer
    Part 3 before Part 1.
    """
    items = list(section.items.all())
    if section.item_flow != ItemFlow.SEQUENTIAL:
        return [item.exercise_id for item in items]
    done = set(normalize_draft(draft)["meta"]["completed_items"])
    for item in items:
        if item.exercise_id not in done:
            return [item.exercise_id]
    return []  # every part finished; the section is ready to submit


def _get_section_attempt(attempt: TestAttempt, section_attempt_id: int) -> SectionAttempt:
    section_attempt = (
        attempt.sections
        .select_related("section", "attempt__template__format")
        .filter(pk=section_attempt_id)
        .first()
    )
    if section_attempt is None:
        raise MockTestError("Section not found on this attempt.", code="not_found")
    return section_attempt


def build_report(attempt: TestAttempt) -> dict:
    """Score-report payload. ``partial`` is true while any section is missing a
    converted score (typically Writing/Speaking awaiting grading)."""
    sections = list(
        attempt.sections.select_related("section").order_by("section__order", "id")
    )
    rows = []
    for section_attempt in sections:
        section = section_attempt.section
        rows.append({
            "section_attempt_id": section_attempt.id,
            "section_id": section.id,
            "title": section.title,
            "skill": section.skill,
            "status": section_attempt.status,
            "raw_score": section_attempt.raw_score,
            "raw_max": section_attempt.raw_max,
            "converted_score": section_attempt.converted_score,
            "pending_grading": (
                section_attempt.status == SectionStatus.COMPLETED
                and section_attempt.converted_score is None
            ),
            "submission_ids": [
                link.submission_id for link in section_attempt.submissions.all()
            ],
        })

    partial = not rows or any(row["converted_score"] is None for row in rows)
    return {
        "attempt_id": attempt.id,
        "template_id": attempt.template_id,
        "template_title": attempt.template.title,
        "format": attempt.template.format.slug,
        "status": attempt.status,
        "overall_score": attempt.overall_score,
        "partial": partial,
        # Conversion tables are prep-industry approximations (research doc
        # risk 3) — the UI is required to say so.
        "estimated": True,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "sections": rows,
        "server_time": timezone.now(),
    }

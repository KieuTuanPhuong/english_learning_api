# Research 01 — Mock Tests (IELTS / TOEIC, extensible)

Full-stack research doc. Frontend implementation detail lives in
`english-learning-web/docs/research/01-mock-tests-frontend.md`; this doc owns the API
contract, data model, and scoring design.

---

## 1. Overview & Goals

**What.** A timed, multi-section mock-test mode that simulates standardized English exams.
Launch formats: **IELTS Academic** (Listening / Reading / Writing / Speaking, band 0–9) and
**TOEIC Listening & Reading** (200 questions, scaled 10–990). The format layer is a data-driven
registry, so TOEFL, Cambridge, or an internal CEFR placement test can be added later without
schema changes.

**Who.**
- *Students* take mock tests (self-practice or class-assigned), see a score report.
- *Teachers* author test templates from existing exercises, grade Writing/Speaking sections,
  and review class results.
- *Admins* manage the format registry and score-conversion tables.

**Success criteria.**
1. A student can complete a full IELTS-style mock under realistic per-section time limits and
   receive Listening/Reading band scores immediately, Writing/Speaking bands after
   teacher/AI grading.
2. Section timing is server-authoritative: refresh, disconnect, or clock tampering cannot buy
   extra time; resume after disconnect restores saved answers.
3. Adding a new format (e.g. TOEIC) requires only new rows (format, sections, conversion
   tables) — no migration, no code fork per exam.
4. ≥90 % of the question-rendering and grading path is reused from the existing
   Exercise/Question/Submission machinery.

### Verified format facts (checked 2026-08)

| Format | Sections & timing | Scoring |
|---|---|---|
| IELTS | Listening 30 min (40 q), Reading 60 min (40 q), Writing 60 min (2 tasks), Speaking 11–14 min | Per-section band 0–9 in 0.5 steps; overall = mean of the four bands rounded to the nearest half band. https://www.pw.live/study-abroad/ielts/exams/ielts-exam-pattern , https://ielts.idp.com/results/scores |
| TOEIC L&R | Listening ~45 min / 100 q; Reading 75 min / 100 q (7 parts, 200 q total) | Raw → scaled per section, 5–495 each, total 10–990; format unchanged for 2026. https://www.etsglobal.org/dz/en/help-center/test-content/format-questions-toeic-listening-reading , https://990prep.com/en/guides/toeic-test-format-breakdown |

ETS does not publish official raw→scaled conversion tables; prep-industry tables are
approximations. That is exactly why conversion must be **data** (admin-editable rows), never
code constants.

---

## 2. Requirements Breakdown

### Functional

- **F1 Format registry** — `TestFormat` rows describe scoring strategy (band-average vs
  scaled-sum) and hold versioned score-conversion tables.
- **F2 Template authoring** — teachers compose a `MockTestTemplate` = ordered
  `TestSection`s; each section wraps one or more existing `Exercise` rows (an IELTS Reading
  section = 3 reading exercises, one per passage).
- **F3 Attempt lifecycle** — one `TestAttempt` per sitting; per-section state machine
  `not_started → in_progress → completed`, sections taken strictly in order, at most one
  section in progress.
- **F4 Server-authoritative timing** — starting a section stamps `started_at` and computes
  `expires_at = started_at + duration + grace`; writes after `expires_at` are rejected and the
  section auto-finalizes from the last autosaved draft.
- **F5 Autosave & resume** — receptive answers and writing drafts PATCHed to the server
  (debounced client-side); reconnect restores draft + remaining time.
- **F6 Grading** — on section submit, a normal `Submission` is created per exercise:
  receptive sections auto-grade via `Submission.grade()`; Writing/Speaking submissions enter
  the existing teacher/AI `Feedback` flow.
- **F7 Score conversion** — raw correct count → band/scaled score via per-format,
  per-section, versioned conversion tables. Productive scores (Feedback 0–100) map through a
  table as well.
- **F8 Score report** — partial-results state: L/R immediately, W/S "pending grading", overall
  computed once every section score exists.
- **F9 Listening integrity** — audio is played once; the "already played" flag is persisted
  server-side in the section draft so refresh cannot replay.

### Non-functional

- **N1 Integrity** — the test-runner serializer must never emit `QuestionOption.is_correct`
  or fill-blank answer keys embedded in `Question.text` (the `[[answer]]` convention from
  `english-learning-web/docs/READING_LISTENING_QUIZ_PLAN.md`). This is a stricter serializer
  than the current exercise detail.
- **N2 Write volume** — autosave ≤1 write / 10 s / student; single-row UPDATE on
  `section_attempts.draft_answers`, no per-answer rows.
- **N3 Timing tolerance** — 30 s server grace for network latency; client clock never trusted.
- **N4 Async-safe** — section submit of W/S must not block on AI evaluation; call through
  `core/ai/service.py:evaluate_submission()`, today synchronous, the documented future
  Celery boundary.
- **N5 Extensibility** — new format = data only (F1); new question interaction = frontend
  concern (existing `resolveQuestionType` convention).

---

## 3. Tools & Technology Choice

The build-vs-adopt decision is about the **assessment engine**: item banking, test assembly,
delivery, scoring.

| Candidate | What it is | Pros | Cons | Cost |
|---|---|---|---|---|
| **A. Build on existing models** (Exercise/Question/Submission + ~8 new tables) | Thin composition layer over the machinery this repo already has | Reuses working auto-grading (`Submission.grade()`), AI feedback loop, RBAC, quiz UI; full control of timing/state-machine semantics; no new infra or licenses; OpenAPI types flow to the frontend for free | We own conversion-table data entry and timer correctness; no import of commercial item banks | $0 |
| **B. QTI 3.0 item bank** (1EdTech standard) — author/store items as QTI XML, render via a QTI player | Industry interchange standard for assessment content; conformance program; portable items | Heavy spec (ASI XML, PCI, CSS vocabulary); no maintained Python/Django implementation — adopting it means an XML pipeline plus a JS delivery engine, duplicating the existing Question/Option model; conformance/certification overhead irrelevant at this scale | Spec free; engineering cost very high. https://www.imsglobal.org/spec/qti/v3p0/oview , https://www.1edtech.org/standards/qti |
| **C. TAO** (open-source QTI-native platform) | Full authoring/banking/delivery/reporting platform, 100M+ deliveries | Complete out of the box; QTI import/export | PHP monolith run beside Django; students would leave our UI (or we embed via LTI); duplicate user/RBAC/results stores; paid tiers (Ignite/Pro/Enterprise) for cloud | Core free (self-host ops cost); cloud tiers quote-based. https://sourceforge.net/software/product/TAO-Platform/ |
| **D. Assessment SaaS API** (e.g. Learnosity) | Hosted item bank + rendering + scoring APIs | Polished interactions, proctoring options, scale | Enterprise contact-sales pricing (no public rate card); per-learner costs unjustifiable for a coursework platform; our Question/Submission data would live outside PostgreSQL, breaking teacher grading + AI feedback integration | Quote-only, enterprise-tier. https://www.getapp.com/hr-employee-management-software/a/learnosity/ |
| **E. Open edX patterns** (inspiration only) | Django LMS with timed-exam subsystem (`edx-proctoring` timed attempts) | Proven Django prior art for attempt state machines & server deadlines | Adopting the platform itself is a migration, not a feature | Free to read |

**Recommendation: A — build on existing models**, borrowing the attempt/deadline shape from
E. Justification:

1. **The hard 80 % already exists.** Receptive auto-grading (`core/models.py:Submission.grade()`),
   productive grading with human + AI `Feedback` (`core/ai/service.py`), role permissions
   (`core/permissions.py`), and four question interactions in `components/quiz/` are live. B–D
   would each *replace* rather than reuse this, and none integrates with our
   `Feedback`/`AiModel` strictness pipeline.
2. **What's genuinely new is small and domain-specific**: a format registry, a section state
   machine with server timing, and conversion tables — a few hundred lines of Django, not an
   engine.
3. **Standards buy nothing here.** QTI's value is interchange between vendors; we neither
   import nor export item banks. If that ever changes, a QTI *export* command can be written
   against our models later without adopting the delivery spec.
4. **Cost & data locality.** SaaS options are quote-priced for institutional buyers, and moving
   items/results off PostgreSQL would break the teacher inbox, grade report CSV, and AI
   practice features that query `submissions` directly.

Supporting choices: keep DRF + drf-spectacular (frontend types regenerate via `yarn gen:api`);
timing via plain REST + server timestamps (Channels/WS is available in `core/consumers.py` but
is an enhancement, not a dependency — see §7).

---

## 4. Design (API)

### 4.1 New models (`core/models.py` conventions: explicit `db_table`, `TextChoices`)

```python
class SectionSkill(models.TextChoices):
    LISTENING = "listening", "Listening"
    READING = "reading", "Reading"
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"


class OverallStrategy(models.TextChoices):
    BAND_AVERAGE = "band_average", "Band average"   # IELTS: mean of section bands → nearest 0.5
    SCALED_SUM = "scaled_sum", "Scaled sum"          # TOEIC: sum of section scaled scores


class TestFormat(models.Model):
    """Registry row per exam flavour (ielts_academic, toeic_lr, ...). Data, not code."""
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    version = models.CharField(max_length=20, default="2026")  # pins conversion tables
    overall_strategy = models.CharField(max_length=20, choices=OverallStrategy.choices)
    score_precision = models.DecimalField(max_digits=4, decimal_places=2, default=0.5)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "test_formats"


class MockTestTemplate(models.Model):
    format = models.ForeignKey(TestFormat, on_delete=models.PROTECT, related_name="templates")
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    difficulty_level = models.CharField(
        max_length=20, choices=DifficultyLevel.choices, null=True, blank=True
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="mock_tests_created",
    )
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mock_test_templates"


class TestSection(models.Model):
    template = models.ForeignKey(
        MockTestTemplate, on_delete=models.CASCADE, related_name="sections"
    )
    skill = models.CharField(max_length=20, choices=SectionSkill.choices)
    title = models.CharField(max_length=100)          # "Listening", "Academic Reading", ...
    order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField()  # IELTS: 30/60/60/14; TOEIC: 45/75
    instructions = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "test_sections"
        ordering = ["order", "id"]
        unique_together = (("template", "order"),)


class TestSectionExercise(models.Model):
    """Composition link: a section is an ordered list of existing Exercises.
    IELTS Reading = 3 reading Exercises (one per passage); IELTS Writing = 2
    writing Exercises (Task 1 / Task 2). Naming follows class_students_lnk."""
    section = models.ForeignKey(TestSection, on_delete=models.CASCADE, related_name="items")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="test_sections")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "test_section_exercises_lnk"
        ordering = ["order", "id"]
        unique_together = (("section", "exercise"),)


class ScoreConversionTable(models.Model):
    """raw correct count (receptive) or Feedback score 0-100 (productive) → band/scaled.
    JSON mapping keyed by raw value: IELTS listening {"39": "9.0", "37": "8.5", ...};
    TOEIC listening {"100": "495", ...}. Admin-seeded; versioned via format.version."""
    format = models.ForeignKey(TestFormat, on_delete=models.CASCADE, related_name="conversions")
    skill = models.CharField(max_length=20, choices=SectionSkill.choices)
    mapping = models.JSONField()          # {str(raw_score): str(converted_score)}
    source_note = models.CharField(max_length=255, null=True, blank=True)  # provenance
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "score_conversion_tables"
        unique_together = (("format", "skill"),)


class AttemptStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"        # all sections submitted
    ABANDONED = "abandoned", "Abandoned"


class SectionStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"


class TestAttempt(models.Model):
    template = models.ForeignKey(MockTestTemplate, on_delete=models.PROTECT, related_name="attempts")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="test_attempts")
    status = models.CharField(
        max_length=20, choices=AttemptStatus.choices, default=AttemptStatus.IN_PROGRESS
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    overall_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "test_attempts"


class SectionAttempt(models.Model):
    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="sections")
    section = models.ForeignKey(TestSection, on_delete=models.PROTECT, related_name="attempts")
    status = models.CharField(
        max_length=20, choices=SectionStatus.choices, default=SectionStatus.NOT_STARTED
    )
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)   # started_at + duration + grace
    completed_at = models.DateTimeField(null=True, blank=True)
    # Autosave envelope: {"answers": {exercise_id: {...}}, "writing": {exercise_id: text},
    #                     "meta": {"audio_played": [exercise_id, ...]}}
    draft_answers = models.JSONField(null=True, blank=True)
    raw_score = models.PositiveIntegerField(null=True, blank=True)      # receptive: correct count
    converted_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "section_attempts"
        unique_together = (("attempt", "section"),)


class SectionSubmission(models.Model):
    """Links a SectionAttempt to the ordinary Submissions created at submit time
    (one per exercise in the section) — keeps Submission untouched."""
    section_attempt = models.ForeignKey(
        SectionAttempt, on_delete=models.CASCADE, related_name="submissions"
    )
    submission = models.OneToOneField(
        Submission, on_delete=models.CASCADE, related_name="section_link"
    )

    class Meta:
        db_table = "section_submissions_lnk"
```

### 4.2 State machine & timing rules (enforced in a service module `core/mock_tests.py`)

- `start_section` allowed only when: attempt `in_progress`, this section `not_started`, every
  lower-`order` section `completed`, and no other section `in_progress`. Sets
  `started_at=now()`, `expires_at=now() + duration + 30s grace`. Wrap in
  `select_for_update()` on the attempt row to kill double-start races.
- `autosave` (PATCH draft): allowed only while `in_progress` and `now() < expires_at`;
  otherwise **409 with code `section_expired`** so the client transitions to submit/locked UI.
- `submit_section`: allowed while `in_progress`. If `now() > expires_at`, still accepted but
  graded from `draft_answers` (late writes were already rejected, so the draft is honest).
  Creates Submissions (per exercise), grades, converts, marks section `completed`. A periodic
  sweep (management command `expire_section_attempts`, cron/Celery-later) force-finalizes
  sections whose `expires_at` passed >10 min ago, so abandoned attempts still produce reports.
- Resume: `GET attempt detail` returns per-section `status`, `expires_at`, `draft_answers`,
  and `server_time` — the client derives remaining time from server fields only.

### 4.3 Grading & conversion flow

1. **Receptive section** (listening/reading): for each exercise, create
   `Submission(exercise=…, student=…, submission_type=exercise.exercise_type,
   answers=draft_answers["answers"][exercise_id])` and call `Submission.grade()` — unchanged.
   `raw_score` = summed correct count across the section's exercises. `grade()` returns a
   percentage; add a sibling `Submission.grade_detail()` returning
   `{"correct": int, "total": int}` (same loop, no behavior change to `grade()`).
2. **Productive section** (writing/speaking): create pending Submissions with
   `writing_text` from the draft and `audio_recording_url` from the submit request
   (recordings stay out of the autosave envelope — frontend doc §7 risk 4). Optionally auto-trigger
   `core/ai/service.py:evaluate_submission()` per template flag (`use_ai_grading`,
   enhancement) — the existing `AiModel.active()` strictness applies untouched. When a
   `Feedback` row lands (teacher via `POST /api/feedback/` or AI), a small hook in the
   feedback create path updates `SectionAttempt.converted_score` through the productive
   conversion table (Feedback score 0–100 → band).
3. **Conversion**: `ScoreConversionTable.objects.get(format=…, skill=…).mapping[str(raw)]`.
   Missing key ⇒ nearest lower key (tables may be sparse).
4. **Overall**: when all sections have `converted_score` — `band_average`: mean rounded to
   nearest 0.5 (IELTS rounding, per https://ielts.idp.com/results/scores); `scaled_sum`: plain
   sum. Stored on `TestAttempt.overall_score`; report is "partial" until then.

### 4.4 Endpoints

Registered on the existing router / `config/urls.py` under `/api/`.

| Method | Path | Roles |
|---|---|---|
| GET | `/api/mock-tests/formats/` | any authenticated |
| GET/POST | `/api/mock-tests/templates/` | GET: all (students see `is_published` only); POST: teacher/admin |
| GET/PATCH/DELETE | `/api/mock-tests/templates/{id}/` | GET all (published), write: owner teacher/admin |
| POST | `/api/mock-tests/templates/{id}/attempts/` | student — creates TestAttempt + SectionAttempts |
| GET | `/api/mock-tests/attempts/me/` | student (own) |
| GET | `/api/mock-tests/attempts/{id}/` | owner student, class teacher, admin |
| POST | `/api/mock-tests/attempts/{id}/sections/{sid}/start/` | owner student |
| PATCH | `/api/mock-tests/attempts/{id}/sections/{sid}/answers/` | owner student (autosave; 409 after expiry) |
| POST | `/api/mock-tests/attempts/{id}/sections/{sid}/submit/` | owner student |
| GET | `/api/mock-tests/attempts/{id}/report/` | owner student, class teacher, admin |
| GET/PUT | `/api/mock-tests/formats/{id}/conversions/` | admin |

### 4.5 Serializer & permission notes

- **Runner serializer must be answer-blind.** New `TestRunnerQuestionSerializer` /
  `TestRunnerOptionSerializer` exclude `is_correct` and strip the answers from `[[…]]`
  fill-blank keys in `Question.text`, keeping empty `[[]]` markers in place (blank count
  stays implicit, so the frontend's `resolveQuestionType`/`FillBlankInput` work unmodified
  — see the frontend doc §4.4). The existing exercise serializers in
  `core/serializers.py` expose `is_correct` for authoring — acceptable for practice, not for
  mock tests. Do not reuse them for the runner payload.
- Permissions: reuse `IsActiveUser`, `IsStudent`, `IsTeacherOrAdmin`
  (`core/permissions.py`); attempt-report visibility mirrors
  `can_view_submission_feedback` (owner / class teacher / admin) — add
  `can_view_attempt(user, attempt)` beside it.
- drf-spectacular: annotate the new views so `yarn gen:api` gives the frontend typed
  `TestAttempt` / `SectionAttempt` shapes; include `server_time` in attempt detail responses.

### 4.6 AI-backend integration

No changes to `core/ai/backends.py` for this feature (doc 02's rubric work extends those
backends' return contract with `criterion_scores` separately; the mock-test conversion
consumes the normalized `Feedback.score` either way). Writing/Speaking mock submissions are ordinary
Submissions, so `MockBackend` grades them deterministically in dev and `RealBackend`
(Whisper + LLM rubric, currently stubbed) will grade them in production once wired. The only
addition is the productive conversion step (Feedback 0–100 → band) in the mock-test service,
downstream of `evaluate_submission()`.

---

## 5. Implementation Guidelines

**Phase 1 — data model + template authoring (backend only).**
1. Add models above to `core/models.py`; migrate.
2. `core/serializers.py`: format/template/section serializers (authoring exposes answers;
   runner serializers answer-blind).
3. `core/views.py`: `TestFormatViewSet`, `MockTestTemplateViewSet` (+ router registration in
   `config/urls.py`).
4. Seed IELTS + TOEIC `TestFormat` rows and draft conversion tables via a data migration or
   `core/management` command (`seed_test_formats`), marking `source_note` as approximate.

**Phase 2 — attempt engine (MVP core).**
5. `core/mock_tests.py` service: `start_attempt`, `start_section`, `autosave`,
   `submit_section`, `finalize_attempt` with the locking rules of §4.2; unit tests in
   `core/tests.py` (timing edge cases first: expiry, double-start, out-of-order start, resume).
6. Attempt endpoints (§4.4). Add `Submission.grade_detail()`.
7. `expire_section_attempts` management command.

**Phase 3 — receptive scoring + report.**
8. Conversion lookup + overall computation; `report` endpoint with `partial` flag.

**Phase 4 — productive sections.**
9. W/S submit path → pending Submissions; hook Feedback creation (in the existing feedback
   create view and `evaluate_submission`) to fill `converted_score`; report completes.

**Phase 5 — frontend** (see the frontend doc), then enhancements: WS timer push via
`core/consumers.py`, per-class assigned mock tests (an `Assignment`-like link), AI auto-grade
flag, TOEFL/CEFR format rows.

---

## 6. Integration with Existing Code

**Reused deliberately**
- `Exercise` / `Question` / `QuestionOption` — sections are ordered sets of existing
  exercises; authoring UI and item content need nothing new.
- `Submission.grade()` — the auto-grading semantics (exact set-match incl. multi-answer) are
  already correct for L/R sections; we add only a count-returning sibling.
- `Feedback` + `core/ai/service.py` — W/S grading, human and AI, strictness registry included.
- `core/permissions.py` role classes and the `can_view_*` helper pattern.
- drf-spectacular schema → `yarn gen:api` typed client; `components/quiz/*` renderers.

**Deliberately NOT reused**
- `Assignment` is *not* the attempt container: it is a class-scoped pointer with no state,
  timing, or per-section structure. Mock attempts are self-initiated (like practice
  submissions with `assignment=null`); class assignment of mock tests is a later link table.
- `StudentModuleProgress` — module completion percentage is unrelated to attempt state.
- Exercise-detail serializers for the runner (leak `is_correct`; see N1).
- One-Submission-per-attempt was rejected: a section spans several exercises and W/S grading
  is per-task (IELTS Task 1 vs Task 2), so per-exercise Submissions + `section_submissions_lnk`
  keep the teacher inbox (`/api/submissions/inbox/`) working unchanged.

---

## 7. Risks & Open Questions

1. **No real audio infra.** `audio_prompt_url` / `audio_recording_url` are mock strings; a real
   Listening section and Speaking recording upload require actual file storage — shared
   infrastructure, out of scope here, blocking full realism. Doc 04 (§3.3, Phase 0) defines
   that shared story (`FileField` + `MEDIA_ROOT` local disk now, `django-storages`/S3 later,
   server-side ffmpeg transcode); mock-test audio should ride the same pipeline once it lands.
2. **Synchronous AI eval.** `evaluate_submission()` runs in-request; a Speaking section
   auto-grade could time out on a real backend. Mitigation: keep AI trigger optional until the
   documented Celery boundary is implemented.
3. **Conversion-table provenance.** Official IELTS/TOEIC raw→score tables are unpublished;
   ours are approximations (`source_note`). Reports should label scores "estimated".
4. **Timer trust vs UX.** REST-only timing means the client countdown can drift from
   `expires_at` by clock skew; we return `server_time` for offset correction. Is a WS
   heartbeat (Channels already installed) worth it for MVP? Recommendation: no — revisit if
   drift complaints appear.
5. **Speaking realism.** Real IELTS Speaking is an interview; ours is prompted recording.
   Acceptable for practice? Product decision.
6. **Retake policy** — unlimited attempts per template, or cooldown? Affects the
   attempts-create endpoint validation; default: unlimited, latest attempt shown first.
7. **Question-type coverage.** IELTS matching/heading types don't map cleanly to
   MCQ/TF/fill-blank/short-answer; MVP restricts authoring to supported types, tracked as a
   frontend/authoring backlog item.

---

## 8. Frontend summary

The web app adds a catalog route (`/mock-tests`), a distraction-free full-screen runner
(`(test)` route group with its own layout: server-synced countdown from `expires_at` +
`server_time`, question palette, play-once audio, debounced autosave mutations), and a
report page with per-section band/scaled breakdown and partial states. It reuses
`components/quiz/QuestionCard` and friends plus `lib/exercises.ts` answer-state helpers, and
extends `lib/api.ts` / `lib/query-keys.ts`. Full detail:
`english-learning-web/docs/research/01-mock-tests-frontend.md`.

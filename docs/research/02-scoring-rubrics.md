# Feature 2 — Scoring Matrix (Rubrics) for Speaking & Writing Review

Research and design document. Companion frontend doc:
`english-learning-web/docs/research/02-scoring-rubrics-frontend.md`.

Sibling features: `01-mock-tests.md`, `03-writing-annotations.md`,
`04-pronunciation-practice.md`. Rubrics and inline annotations (doc 03) both
extend the submission-review flow around `Feedback`; §6 covers how they
coexist without touching each other's tables.

---

## 1. Overview & Goals

Today a teacher grades a writing essay or speaking recording with one number
and one text blob (`core/models.py: Feedback.score` 0–100 +
`Feedback.comments`). Real language assessment does not work that way: IELTS,
TOEIC and CEFR-aligned programmes all score productive skills on a **matrix of
criteria × bands**, each band carrying a published descriptor. A single
holistic number hides *why* a student got a 65 and makes per-skill progress
tracking impossible.

This feature adds **rubric templates as data** (`RubricTemplate` →
`RubricCriterion` → `RubricBandDescriptor`), seeded with real public rubrics
(IELTS Writing, IELTS Speaking, TOEIC Speaking/Writing proficiency scales, a
generic CEFR grid), plus **per-criterion scores** (`CriterionScore`) attached
to the existing `Feedback` row. The teacher grades by clicking cells in a
matrix; the overall score is computed by a per-template aggregation rule
(e.g. the IELTS mean-of-four convention) and normalized into the existing
`Feedback.score` 0–100 field so every current screen keeps working.

**Users**
- **Teacher / admin**: pick a rubric when authoring an exercise (or rely on
  the per-type default), grade speaking/writing submissions cell-by-cell with
  band descriptors on hover, still add free-text comments.
- **Student**: sees the criterion breakdown read-only on their submission —
  "Lexical Resource 5, Grammatical Range 6" is actionable; "62/100" is not.
- **AI backend**: `core/ai/` can emit the same `criterion_scores` structure on
  its `is_ai_generated` Feedback rows (Phase 3), so human and AI grades become
  directly comparable per criterion.

**Success criteria**
- A teacher grades a 4-criterion submission in ≤6 clicks, descriptors visible
  without leaving the screen.
- The server-computed overall matches the template's aggregation rule exactly
  (unit-tested, IELTS rounding included).
- Existing consumers of `Feedback.score` (inbox, dashboard, feedback cards,
  CSV grade report) need **zero changes** — they keep reading a 0–100 number.
- Criterion scores are queryable for analytics in one ORM filter (§1.1).

### 1.1 Analytics angle (why structured beats free-text)

Because every score row carries a stable `criterion.code`
(`lexical_resource`, `pronunciation`, …) shared across templates, a later
progress feature is a single query, no NLP over comment blobs:

```python
CriterionScore.objects.filter(
    feedback__submission__student=student,
    criterion__code="lexical_resource",
).order_by("feedback__created_at")
```

Rows, not richer comment text, convert grading effort teachers already spend
into longitudinal per-skill data for free.

---

## 2. Requirements Breakdown

### Functional
- **FR1** Admin-seeded rubric templates exist as data: template → ordered
  criteria → band descriptors per criterion (matrix cells).
- **FR2** Seed content (verified against official public sources, §3.1):
  IELTS Writing (Task Response, Coherence & Cohesion, Lexical Resource,
  Grammatical Range & Accuracy; bands 0–9), IELTS Speaking (Fluency &
  Coherence, Lexical Resource, Grammatical Range & Accuracy, Pronunciation;
  0–9), TOEIC Speaking (proficiency levels 1–8) and TOEIC Writing (levels
  1–9), and a generic CEFR grid (Range, Accuracy, Fluency, Coherence; A1–C2).
- **FR3** Rubric selection: exercise author may pin a template per `Exercise`;
  otherwise the active default template for the exercise's `exercise_type`
  applies; otherwise grading falls back to today's holistic form.
- **FR4** Teacher/admin posting feedback may include one `criterion_score` per
  criterion of the resolved template (all-or-nothing set); the server computes
  the rubric-native overall via the template's aggregation rule and writes the
  normalized 0–100 value into `Feedback.score`.
- **FR5** Per-template aggregation rules: IELTS-style *mean rounded down to
  the nearest 0.5* (default for both IELTS templates — see §3.1 for the
  verification caveat), plain mean, nearest-0.5 mean, and sum.
- **FR6** Feedback reads (`GET /api/submissions/{id}/feedback/`) embed the
  criterion scores + rubric-native overall for the existing audience
  (`core/permissions.py: can_view_submission_feedback`) — students included.
- **FR7** Rubric grading applies to productive submissions only
  (`submission_type` writing/speaking); receptive auto-scoring (`grade()`)
  is untouched. Free-text `comments` and per-criterion `note` remain —
  rubrics complement, not replace, prose feedback.
- **FR8** (Phase 3) AI backends can return `criterion_scores` in the same
  shape; `evaluate_submission()` persists them on the AI Feedback row.

### Non-functional
- **NFR1** No breaking change to the `Feedback` wire format — only additive,
  optional fields (the annotations feature, doc 03, makes the same promise;
  both must hold simultaneously).
- **NFR2** Templates referenced by scores are immutable in practice: retire
  via `is_active=False`, never delete (`PROTECT` on the score FK), so historic
  grades always render against the exact rubric used.
- **NFR3** Aggregation is server-authoritative; any client-side preview is
  display-only and must implement the identical rule.
- **NFR4** Seeding is idempotent (keyed by `slug`) and safe to run on every
  deploy, matching `core/management/commands/seed_demo.py` practice.
- **NFR5** No new external services or paid dependencies; pure
  Django/DRF/PostgreSQL.

---

## 3. Tools & Technology Choice

### 3.1 The rubric *content* (verified sources)

- **IELTS Writing band descriptors (public version, updated May 2023)** —
  Task Achievement (Task 1) / Task Response (Task 2), Coherence & Cohesion,
  Lexical Resource, Grammatical Range & Accuracy, each 0–9:
  https://takeielts.britishcouncil.org/sites/default/files/ielts_writing_band_descriptors.pdf
  and https://ielts.org/news-and-insights/ielts-writing-band-descriptors-and-key-assessment-criteria
- **IELTS Speaking band descriptors (public version)** — Fluency & Coherence,
  Lexical Resource, Grammatical Range & Accuracy, Pronunciation, each 0–9:
  https://idc.edu/IELTS-Speaking-Writing-Band-descriptors.pdf
- **IELTS rounding — verification result.** The *officially published* rule
  covers the overall band across the four skills: averages ending in .25
  round **up** to the half band, .75 **up** to the next whole band
  (https://ielts9.io/blog/how-to-calculate-ielts-band-score,
  https://www.pw.live/study-abroad/ielts/exams/ielts-writing-band-score-calculation).
  For a single Writing/Speaking score from the four criteria, IELTS states
  only that the criteria are **equally weighted**; the widely-reported
  examiner convention — mean rounded **down** to the nearest 0.5 (6+6+7+7 →
  6.5) — does not appear in the public descriptors (discussion:
  https://www.quora.com/In-IELTS-writing-are-scores-for-the-4-criteria-rounded-up-or-down).
  **Design consequence:** the aggregation rule is a *data field on the
  template*, not hard-coded — IELTS seeds default to `mean_down_half`, flip
  by data migration if official guidance emerges. Flagged again in §7.
- **TOEIC Speaking & Writing** — separate 0–200 scaled scores (increments of
  10); test takers are grouped into **8 proficiency levels for Speaking, 9
  for Writing**, each with a published descriptor. ETS does **not** publish a
  per-criterion analytic rubric — items are rated 0–3/0–4/0–5 then converted:
  https://www.ets.org/pdfs/toeic/toeic-speaking-writing-score-user-guide.pdf,
  https://www.ets.org/pdfs/toeic/toeic-speaking-writing-score-descriptors.pdf.
  **Design consequence:** the TOEIC seeds are *single-criterion* ("Overall
  proficiency") templates — a 1×N matrix the schema handles without special
  cases.
- **CEFR** — the Council of Europe's Table 3 "qualitative aspects of spoken
  language use" grid gives per-criterion descriptors (Range, Accuracy,
  Fluency, Coherence — we drop Interaction, which needs a live interlocutor)
  across A1–C2, mapped to band values 1–6:
  https://www.coe.int/en/web/common-european-framework-reference-languages/table-3-cefr-3.3-common-reference-levels-qualitative-aspects-of-spoken-language-use

### 3.2 The rubric *machinery*

| Candidate | What it is | Pros | Cons | Cost |
|---|---|---|---|---|
| **Custom Django models, informed by LMS standards** (recommended) | 4 small models + serializer extension in `core/` | Exact fit to `Feedback`/`Submission`/permissions; explicit `db_table` + `TextChoices` conventions carry over; aggregation rules are our Decimal code (testable); nothing to integrate | We write ~400 LOC of models/serializers/tests ourselves | Free |
| **1EdTech (IMS) Rubric spec / CASE rubrics** | XML-era ePortfolio Rubric spec v1.0 (2005) — `Rubric` / `DimensionOfQuality` / `RubricCell`; CASE v1.1 can exchange rubrics between systems — https://www.imsglobal.org/ep/epv1p0/imsrubric_specv1p0.html, https://www.imsglobal.org/spec/case/v1p1 | Battle-tested *vocabulary*: dimensions (criteria) × quality levels (bands) with cell descriptors — precisely our matrix; adopting its shape keeps a future CASE export trivial | It is a wire format, not software: no Django implementation to install; the ePortfolio spec is 2005 XML web-services machinery; full CASE adoption (URIs, frameworks, associations) is heavy interop plumbing we do not need between our own two apps | Free (spec) |
| **Canvas rubric schema as prior art** | Canvas LMS `Rubric` → `criteria[]` → `ratings[]` (description, long_description, points), assessments store per-criterion points+comments — https://canvas.instructure.com/doc/api/rubrics.html | The most widely deployed JSON rubric shape in education; validates our template→criterion→band→per-criterion-score design almost 1:1 (their `rating` = our band descriptor, their `RubricAssessment` = our `CriterionScore` set) | It is an API of a hosted LMS, not a library; adopting Canvas itself means LTI integration, external accounts, and moving grading out of our UI | Schema free to imitate; Canvas hosted plans are institutional contracts |
| **Moodle rubrics (advanced grading) as prior art** | Criteria × levels grid with per-level definition+score, used by `mod_assign` — https://docs.moodle.org/en/Rubrics | Proves the exact teacher UX we want (clickable matrix, level definitions); its "sum of levels mapped onto grade range" mirrors our normalization to 0–100 | PHP monolith internals; not extractable; Moodle's min-score normalization quirk is a known confusion source we deliberately avoid | Free (GPL), irrelevant as a dependency |
| **Embed an external LMS for grading (LTI)** | Outsource rubric grading to Canvas/Moodle via LTI | Zero rubric code to write | Splits the review flow across products; our `Feedback`/`Submission` models stop being the source of truth; annotations (doc 03) and the AI loop live in *our* review screen — fragmenting it kills the feature's point | Hosting/contract costs |

**Recommendation: custom models, standards-informed.** No maintained,
general-purpose Django rubric package exists — real-world rubric
implementations live inside LMS monoliths (Canvas, Moodle) or interop specs
(1EdTech). The domain logic we actually need (IELTS/TOEIC aggregation in
`Decimal`, normalization into `Feedback.score`, `can_view_submission_feedback`
audiences, AI-backend parity) is coupled to *our* models either way. We
therefore build custom but **steal the shape** that Canvas, Moodle and the
1EdTech spec all converged on independently — template → criteria → leveled
descriptors, with per-criterion assessments referencing the level — which
keeps a future CASE/Canvas-style export a serializer, not a migration. This is
the same custom-but-standards-vocabulary call doc 03 made with the W3C
annotation model, and it fits the repo's "one app, no new services" stance.

---

## 4. Design (Backend / API)

### 4.1 Models

Add to `core/models.py` after `Feedback`, following file conventions
(`TextChoices`, explicit `db_table`, nullable `SET_NULL` author FKs). No table
name collides with docs 01/03/04 (doc 01's `test_*`/`mock_test_templates`,
`writing_annotations`, `pronunciation_*`).

```python
class RubricAggregation(models.TextChoices):
    """How a full set of criterion scores becomes one overall value.
    Data-driven because the IELTS task-level rounding convention is not
    officially published (see research doc §3.1)."""
    MEAN_DOWN_HALF = "mean_down_half", "Mean, rounded DOWN to nearest 0.5 (IELTS convention)"
    MEAN_NEAREST_HALF = "mean_nearest_half", "Mean, rounded to nearest 0.5"
    MEAN = "mean", "Mean (2 decimal places)"
    SUM = "sum", "Sum of criterion scores"


class RubricTemplate(models.Model):
    """A named scoring matrix (e.g. 'IELTS Writing Task 2'). Retire with
    is_active=False — never delete, historic CriterionScores reference it."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True)  # seed idempotency key
    description = models.TextField(null=True, blank=True)
    exercise_type = models.CharField(max_length=20, choices=ExerciseType.choices)
    is_default_for_type = models.BooleanField(default=False)
    # Band scale shared by all criteria in the template.
    scale_min = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    scale_max = models.DecimalField(max_digits=5, decimal_places=1, default=9)
    score_step = models.DecimalField(max_digits=4, decimal_places=1, default=1)
    aggregation = models.CharField(
        max_length=20,
        choices=RubricAggregation.choices,
        default=RubricAggregation.MEAN_DOWN_HALF,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rubric_templates_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rubric_templates"
        constraints = [
            models.UniqueConstraint(
                fields=["exercise_type"],
                condition=models.Q(is_default_for_type=True, is_active=True),
                name="one_default_rubric_per_exercise_type",
            ),
        ]

    @classmethod
    def resolve_for(cls, exercise):
        """Pin on the exercise, else active default for its type, else None."""
        pinned = exercise.rubric_template
        if pinned and pinned.is_active:
            return pinned
        return cls.objects.filter(
            exercise_type=exercise.exercise_type,
            is_default_for_type=True, is_active=True,
        ).first()

    def aggregate(self, values):
        """Native-scale overall from a complete list of criterion Decimals."""
        if self.aggregation == RubricAggregation.SUM:
            return sum(values)
        mean = sum(values) / Decimal(len(values))
        if self.aggregation == RubricAggregation.MEAN_DOWN_HALF:
            return (mean * 2).quantize(Decimal("1"), rounding=ROUND_FLOOR) / 2
        if self.aggregation == RubricAggregation.MEAN_NEAREST_HALF:
            return (mean * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2
        return mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def normalize(self, overall):
        """Native overall -> the platform-wide 0-100 Feedback.score."""
        span = self.scale_max - self.scale_min
        return ((overall - self.scale_min) / span * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)


class RubricCriterion(models.Model):
    template = models.ForeignKey(
        RubricTemplate, on_delete=models.CASCADE, related_name="criteria"
    )
    name = models.CharField(max_length=100)   # "Grammatical Range & Accuracy"
    code = models.SlugField(max_length=50)    # "grammatical_range" — stable analytics key
    description = models.TextField(null=True, blank=True)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1)  # future-proof; all seeds 1 (IELTS: equal weighting)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rubric_criteria"
        ordering = ["order", "id"]
        unique_together = (("template", "code"),)


class RubricBandDescriptor(models.Model):
    """One matrix cell: the official wording for a criterion at a band."""

    criterion = models.ForeignKey(
        RubricCriterion, on_delete=models.CASCADE, related_name="band_descriptors"
    )
    band_value = models.DecimalField(max_digits=5, decimal_places=1)  # 7.0, or 6 for "Level 6 (C1)"
    label = models.CharField(max_length=50, blank=True)  # "Band 7" / "Level 8 (190–200)" / "C1"
    descriptor = models.TextField()

    class Meta:
        db_table = "rubric_band_descriptors"
        ordering = ["band_value", "id"]
        unique_together = (("criterion", "band_value"),)


class CriterionScore(models.Model):
    """A teacher's (or AI's) score for one criterion, attached to the existing
    Feedback row — Feedback stays the unit of 'one review pass'."""

    feedback = models.ForeignKey(
        Feedback, on_delete=models.CASCADE, related_name="criterion_scores"
    )
    # PROTECT: retiring a template must not orphan historic grades.
    criterion = models.ForeignKey(
        RubricCriterion, on_delete=models.PROTECT, related_name="scores"
    )
    score = models.DecimalField(max_digits=5, decimal_places=1)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "criterion_scores"
        unique_together = (("feedback", "criterion"),)
```

(`__str__` methods omitted for brevity; add them per file convention.)

One **additive** field on `Exercise` (author's rubric pin, FR3):

```python
    rubric_template = models.ForeignKey(
        "RubricTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="exercises",
    )
```

**Why nothing on `Feedback` changes:** the rubric-native overall is derivable
(`template.aggregate()` over ≤6 rows) and exposed as a read-only serializer
field; the normalized value goes into the *existing* `Feedback.score`, which
is why the inbox, dashboard, grade CSV and student feedback cards all keep
working untouched.

### 4.2 Endpoints

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/rubrics/` | any authenticated active user | List active templates, criteria + band descriptors nested |
| GET | `/api/rubrics/{id}/` | any authenticated active user | One template (inactive included, so old grades can render) |
| GET | `/api/exercises/{id}/rubric/` | any authenticated active user | Resolved template for this exercise (pin → type default → `null`); 200 with `null` body when none |
| POST | `/api/feedback/` | teacher, admin (unchanged) | Extended body: optional `criterion_scores: [{criterion_id, score, note}]` |
| GET | `/api/submissions/{id}/feedback/` | owner student / class teacher / reviewer / admin (unchanged) | Each row now embeds `criterion_scores`, `rubric_template_id`, `rubric_overall` |
| POST/PATCH | `/api/rubrics/…` (Phase 2) | admin | CRUD for custom templates; MVP manages content via seed command + Django admin |

Registration: `router.register("rubrics", views.RubricTemplateViewSet,
basename="rubric")` in `config/urls.py`; the resolver is a
`@action(detail=True, methods=["get"], url_path="rubric")` on the existing
`ExerciseViewSet`. `200 + null` (not 404) for "no rubric" keeps the frontend's
`apiFetch` happy — its non-2xx handling throws (`lib/api.ts: toApiError`).

### 4.3 Serializers (`core/serializers.py`)

Read side: three plain nested `ModelSerializer`s —
`RubricTemplateSerializer` (all template fields + `criteria`) →
`RubricCriterionSerializer` (`id/name/code/description/weight/order` +
`band_descriptors`) → `RubricBandDescriptorSerializer`
(`id/band_value/label/descriptor`); the viewset prefetches
`criteria__band_descriptors`.

Write side — extend the existing `FeedbackSerializer` (mirroring its
`submission_id = PrimaryKeyRelatedField(source=...)` convention):

```python
class CriterionScoreSerializer(serializers.ModelSerializer):
    criterion_id = serializers.PrimaryKeyRelatedField(
        source="criterion", queryset=RubricCriterion.objects.all()
    )

    class Meta:
        model = CriterionScore
        fields = ["id", "criterion_id", "score", "note"]


class FeedbackSerializer(serializers.ModelSerializer):
    # ... existing fields unchanged ...
    criterion_scores = CriterionScoreSerializer(many=True, required=False)
    rubric_template_id = serializers.SerializerMethodField()
    rubric_overall = serializers.SerializerMethodField()  # native scale, "6.5"
```

`validate()` additions when `criterion_scores` is present:
1. Submission is productive (`submission_type` writing/speaking).
2. All criteria belong to **one** template, and that template equals
   `RubricTemplate.resolve_for(submission.exercise)` — no grading Writing
   against a Speaking rubric.
3. The set is **complete** (every criterion of the template exactly once —
   the `unique_together` guards duplicates at the DB, the serializer gives
   the friendly 400).
4. Each `score` is within `[scale_min, scale_max]` and a multiple of
   `score_step` (Decimal modulo).

`create()` wraps in `transaction.atomic()`: create `Feedback`, bulk-create the
score rows, then set `feedback.score = template.normalize(template.aggregate(values))`
— any client-supplied `score` is **ignored** when criterion scores are present
(server-authoritative, NFR3). Without `criterion_scores` the serializer
behaves exactly as today.

### 4.4 Views & permissions

- `RubricTemplateViewSet(ReadOnlyModelViewSet)`: list filters
  `is_active=True`; retrieve does not (historic render, NFR2). Permissions:
  `_perms()` (any authenticated active user — students need it to read their
  own breakdowns' wording). `@extend_schema` on both actions so
  drf-spectacular → `yarn gen:api` picks everything up.
- `ExerciseViewSet.rubric` action: `_perms()`, response
  `RubricTemplateSerializer(allow_null=True)`.
- `FeedbackViewSet` is **unchanged** — same `IsTeacherOrAdmin` gate, and the
  existing `Submission.status` mutators (`perform_create`, `core/views.py:676`,
  and the AI path `core/ai/service.py: evaluate_submission`) are untouched —
  rubrics add none, which also preserves doc 03's invariant.
- `SubmissionViewSet.feedback` action gains only a
  `prefetch_related("criterion_scores__criterion__template")`.

### 4.5 AI-backend integration (Phase 3)

Follows the established pattern (`core/ai/backends.py`): extend the return
contract of `BaseAIBackend.grade_writing()` /
`transcribe_and_score_speaking()` with an optional key —

```python
"criterion_scores": [
    {"code": "lexical_resource", "score": Decimal("6.0"), "note": "..."},
    ...
]
```

- `MockBackend` derives deterministic per-criterion values from what it
  already computes (word count → task_response, `_GOOD_MARKERS` hits →
  coherence/lexical) — still no network, still seed/test-stable.
- `RealBackend`'s LLM prompt gains the resolved template's criteria + band
  descriptors as rubric context (descriptors are *exactly* what an LLM grader
  needs) and returns one score per `code`; unknown codes are dropped,
  incomplete sets fall back to holistic-only feedback rather than erroring.
- `core/ai/service.py: evaluate_submission()` maps `code → criterion` and
  persists rows on the AI `Feedback` (`is_ai_generated=True, reviewer=None`).
  It stays the future Celery enqueue boundary — rubrics change nothing there.

Because AI and teacher fill the *same* structure, "AI band 5.5 vs teacher
band 6.0 on Pronunciation" comparisons come free — useful later for
calibrating against `AiModel.strictness`.

### 4.6 Seeding

New idempotent command `core/management/commands/seed_rubrics.py`
(`update_or_create` keyed on `RubricTemplate.slug` / criterion `code` / band
`band_value`), invoked at the end of `seed_demo.py`. Seed set:

| slug | exercise_type | criteria | bands | aggregation | default? |
|---|---|---|---|---|---|
| `ielts-writing-task2` | writing | task_response, coherence_cohesion, lexical_resource, grammatical_range | 0–9 step 1 | mean_down_half | ✔ writing |
| `ielts-speaking` | speaking | fluency_coherence, lexical_resource, grammatical_range, pronunciation | 0–9 step 1 | mean_down_half | ✔ speaking |
| `toeic-writing-proficiency` | writing | overall_proficiency (1×9 matrix) | 1–9 step 1 | mean | |
| `toeic-speaking-proficiency` | speaking | overall_proficiency (1×8 matrix) | 1–8 step 1 | mean | |
| `cefr-general` | speaking | range, accuracy, fluency, coherence | 1–6 step 1 (labels A1–C2) | mean_nearest_half | |

Descriptor texts come from the public documents in §3.1 (public versions are
distributable for exactly this purpose; the sources are recorded in each
template's `description`).

---

## 5. Implementation Guidelines

**Phase 1 — MVP (seeded rubrics, teacher grades via matrix, student reads)**
1. `core/models.py`: add the four models + `RubricAggregation` +
   `Exercise.rubric_template` (§4.1); `makemigrations core && migrate`.
2. `core/management/commands/seed_rubrics.py`: templates per §4.6 (IELTS ×2
   first; TOEIC/CEFR can land in the same PR or the next); call it from
   `seed_demo.py`.
3. `core/serializers.py`: rubric read serializers; extend
   `FeedbackSerializer` per §4.3; expose `rubric_template_id` on
   `ExerciseSerializer` (write via `PrimaryKeyRelatedField`, matching the
   `submission_id` convention).
4. `core/views.py`: `RubricTemplateViewSet`; `rubric` action on
   `ExerciseViewSet`; prefetch in `SubmissionViewSet.feedback`;
   `@extend_schema` everywhere so `/api/schema/` stays authoritative.
5. `config/urls.py`: `router.register("rubrics", ...)`.
6. `core/admin.py`: register `RubricTemplate` (criterion inline) and
   `RubricCriterion` (band-descriptor inline) — MVP content management.
7. `core/tests.py`: aggregation table-tests (IELTS 6,6,7,7 → 6.5; 5,6,6,6 →
   5.5; nearest-half and sum variants), normalization bounds, serializer
   validation matrix (wrong template, incomplete set, off-step score,
   receptive submission), RBAC (student cannot post scores, student *can*
   read own breakdown), resolve_for fallback chain, template-retirement
   PROTECT behavior.
8. Frontend Phase 1 (see frontend doc): `yarn gen:api`, grading matrix +
   student breakdown.

**Phase 2 — Authoring & operations**
9. Admin CRUD for templates (`POST/PATCH /api/rubrics/…`, `IsAdmin`);
   `is_active` toggle instead of delete; copy-on-write duplicate once any
   score references a template.
10. Exercise-authoring support: `rubric_template_id` at creation works after
    step 3, but the exercise endpoint is create+delete only today
    (`lib/api.ts` records PATCH 405) — re-pinning post-creation is Phase 2
    backend work.
11. Grade-report CSV: per-criterion columns where a class shares a template.

**Phase 3 — AI parity & analytics**
12. §4.5 contract in `MockBackend`, prompt scaffolding in `RealBackend`,
    persistence in `evaluate_submission()`.
13. Per-skill progress endpoint (e.g. `/api/progress/criteria/?code=...`)
    feeding a student radar/trend chart — the payoff of §1.1.

**Explicit non-goals:** per-question rubrics on receptive exercises
(auto-graded, doc 01's territory); weighted aggregation UI (field exists,
seeds all use 1); rubric versioning beyond activate/retire; grading multiple
templates on one submission.

---

## 6. Integration with Existing Code

**Reused**
- `Feedback` as the review-pass container: `CriterionScore` hangs off it —
  one review = one Feedback = at most one matrix; `is_ai_generated`
  distinguishes AI matrices for free (§4.5).
- `Feedback.score` as the normalized 0–100 platform score — inbox
  (`SubmissionInboxSerializer`), dashboard, `formatScore(...)/100` cards and
  the grade CSV keep working with zero changes.
- `Submission.status` keeps its existing mutators
  (`FeedbackViewSet.perform_create` and the AI path
  `core/ai/service.py: evaluate_submission`) — rubrics add none; the
  `_broadcast_class` `feedback_posted` event fires unchanged.
- `can_view_submission_feedback` (`core/permissions.py`) defines who reads
  breakdowns — same audience as feedback because they *are* feedback.
- `ExerciseType` ties templates to the existing writing/speaking taxonomy;
  `Exercise.created_by` inbox scoping is untouched.
- `core/ai/` backend switch (`settings.AI_BACKEND`, `AiModel.active()`,
  strictness) — rubric grading extends the existing return dicts.
- Conventions: explicit `db_table`, `TextChoices`, `SET_NULL` author FKs,
  `submission_id`-style PK fields, `@extend_schema` → drf-spectacular →
  `yarn gen:api` typed frontend.

**Coexistence with doc 03 (writing annotations)**
- Disjoint tables (`rubric_*`/`criterion_scores` vs `writing_annotations`);
  both are additive around `Feedback` and both preserve the
  `perform_create`-mutates-status invariant, so the features can ship in
  either order. On the review screen they are complementary passes: inline
  annotations explain *where*, the matrix explains *how much per skill*.

**Deliberately NOT reused**
- `Question`/`QuestionOption` to model criteria/bands: superficially similar
  (parent + ordered options) but semantically wrong — options are answer keys
  with `is_correct`, bands are descriptive levels; `Submission.grade()`
  set-matching has no meaning here. Overloading them would poison both the
  quiz flow and the analytics keys.
- `Feedback.comments` as a JSON container for criterion scores: same
  first-class-rows argument as doc 03 §6 — scores need FK integrity,
  per-criterion uniqueness, and SQL aggregability (§1.1).
- `AiModel.strictness` as a rubric selector: strictness tunes *severity*
  within a rubric; the template defines the rubric. Kept orthogonal.
- Frontend quiz components (`components/quiz/*`): question/option renderers,
  nothing matrix-shaped (see frontend doc §6).

---

## 7. Risks & Open Questions

| # | Risk / question | Mitigation |
|---|---|---|
| 1 | **IELTS task-level rounding is not officially published** (§3.1): mean-rounded-down is examiner folklore, and examiners are also instructed to judge holistically, not purely arithmetically. | Aggregation is a data field per template; tests pin current behavior; a data migration flips the rule without schema change. UI labels the overall "computed", and the teacher's free-text comments remain the holistic channel. |
| 2 | Template edited after grades exist → historic matrices re-render against changed wording. | Operational rule + Phase 2 enforcement: templates referenced by any `CriterionScore` become read-only in the API (copy-on-write duplicate instead); `PROTECT` on the score FK already blocks deletion. |
| 3 | Normalized 0–100 vs native bands confuses users ("why does band 6.5 show 72.22?"). | Serializer exposes `rubric_overall` (native) alongside `score`; the UI leads with the native band and shows /100 as secondary. Accepted trade-off for keeping every existing score consumer unchanged. |
| 4 | Speaking review depends on audio playback, but `audio_recording_url` is a mock string (no real storage backend). | Matrix grading works regardless (teacher may have heard the student live). Real playback quality is gated on the platform-wide file-storage work — defined in doc 04 §3.3 (`FileField` + `MEDIA_ROOT` local disk now, `django-storages`/S3 later) — not on this feature. |
| 5 | TOEIC 1×N holistic templates make a degenerate "matrix". | Schema-supported by design (§3.1); the frontend renders 1-row matrices as a level picker. Do not invent unofficial TOEIC sub-criteria. |
| 6 | Complete-set requirement (FR4) blocks a teacher who wants to score only two criteria. | MVP keeps all-or-nothing (aggregation rules assume complete sets). If partial grading is demanded, add `allow_partial` to the template and define the aggregation semantics then — additive. |
| 7 | Two default templates for one type (constraint race / bad seed). | Conditional `UniqueConstraint` (§4.1) enforces at the DB; seed command asserts it. |
| 8 | drf-spectacular schema for the nullable `rubric` action and nested writes needs care (`allow_null`, request/response components) or `gen:api` types degrade to `unknown`. | Explicit `@extend_schema(request=..., responses=...)` on every new action; frontend doc includes a types-verification step after `yarn gen:api`. |

---

## 8. Frontend summary

The web implementation — clickable criteria×bands matrix (`RubricMatrix`),
band-descriptor popovers, client-side aggregation preview, audio player
placement on the speaking review screen, read-only student breakdown,
TanStack Query keys, zod schemas, phased steps — is specified in
**`english-learning-web/docs/research/02-scoring-rubrics-frontend.md`**. It
consumes exactly the contract in §4 via regenerated `lib/types/api.d.ts`
(`yarn gen:api`), touching `lib/api.ts`, `lib/query-keys.ts`, `lib/hooks.ts`,
new `components/rubric/*` files, and `app/(app)/submissions/[id]/page.tsx`.

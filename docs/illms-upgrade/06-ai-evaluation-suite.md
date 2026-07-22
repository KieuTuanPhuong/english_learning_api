# 06 — AI evaluation suite (AiModel config, async-ready grading worker, Whisper/LLM mockable)

**Status:** Proposed · **Depends on:** [01 — Model foundations](./01-model-foundations.md) (`Feedback.is_ai_generated`, `Submission.status` + `SubmissionStatus` TextChoices, `Exercise.content_text` / `Exercise.created_by`), [04 — Grading, evaluations & inbox](./04-grading-evaluations-inbox.md) (status transitions, `AI_Graded` state) · **Spec refs:** docs.md §1.5 (Multimodal AI Evaluation Suite); §3.4 UC-17/18 (AI automated feedback), UC-20/21 (infrastructure & AI configuration); §3.2 UC-09 (practice speaking via Whisper); §6 directive #1 (nullable evaluator) & #2 (exercise-type content dispatching); RBAC matrix rows 12 (Autonomous AI Skill Practice Area) & 13 (Manage AI Endpoints & Grading Strictness)

**Estimated blast radius:** `core/models.py` (+1 model `AiModel`), `core/serializers.py` (+2 serializers), `core/views.py` (+1 ViewSet, +2 actions on `SubmissionViewSet`), `core/ai/` (NEW package: `__init__.py`, `backends.py`, `service.py`), `config/urls.py` (+1 router registration), `config/settings.py` (+1 tag, +`AI_BACKEND` env read), `core/admin.py` (+1 registration), `core/management/commands/seed_demo.py` (wipe + seed 1 `AiModel` row), 1 migration.

---

## Goal

Add the multimodal AI evaluation layer described in docs.md §1.5/§3.4: an admin-configurable `AiModel` registry (UC-20/21, RBAC row 13), a pluggable AI grading **service layer** (`grade_writing`, `transcribe_and_score_speaking`) that writes AI `Feedback` rows with `is_ai_generated=True` and no reviewer (UC-17/18, docs.md §6 directive #1), and a student-facing "Autonomous AI Skill Practice Area" that returns immediate AI feedback (UC-09, RBAC row 12). Following the repo's realism policy (mock URLs, no live AI), the default backend is a **deterministic stub**; a `RealBackend` stub documents exactly where the OpenAI Whisper STT + LLM calls go, selectable via `AI_BACKEND=mock|real`.

## Why (gap)

Today the API has **no AI path at all**:

- `core/views.py:431` `FeedbackViewSet` is the only way to create `Feedback`, and `perform_create` hard-sets `serializer.save(reviewer=self.request.user)` (line 440) — every feedback row is attributed to a human teacher/admin. There is no way to produce the `is_ai_generated=True, reviewer=None` rows docs.md §3.4 UC-17/18 and §6 directive #1 require.
- `core/views.py:389` `SubmissionViewSet` auto-grades only **receptive** skills via `submission.grade()` (the `_RECEPTIVE` set on line 400). Productive skills (writing/speaking) get no automated score — exactly the gap the AI suite fills.
- There is **no model** for docs.md §4 `ai_models` and no endpoint for UC-20/21 "dynamic updating of active AI ML model endpoints". `core/models.py` ends at `Feedback` (line 337) with no `AiModel`.
- There is **no service abstraction** for Whisper/LLM. `core/` has no `ai/` package; `config/settings.py` has no `AI_BACKEND` setting.
- RBAC row 12 (student AI practice) has no home: students can only `create` plain submissions (`SubmissionViewSet.get_permissions`, `core/views.py:393`) and never receive AI feedback.

This file closes all of the above **additively** — no existing endpoint, field, table, or the login response shape changes.

> **Dependency note:** This file consumes fields introduced by 01 and 04 — `Feedback.is_ai_generated` (BooleanField, default `False`), `Submission.status` (`SubmissionStatus` TextChoices with member `AI_GRADED = "ai_graded", "AI Graded"`), and `Exercise.content_text`. If 01/04 are not yet applied, apply them first. This file does **not** define those fields (see ownership map).

---

## Changes (file by file)

### `core/models.py`

Add the `AiModel` model (docs.md §4 `ai_models` + §5 Table 11). Per SCOPE, add a **grading-strictness** field (RBAC row 13 names "Grading Strictness") as a constrained choice. Append **after** the `Feedback` class (end of file, after line 357). Keep the `db_table` snake-case-plural convention and `SET_NULL` for the user FK (mirrors `Class.teacher` line 108-111, `LearningModule.created_by` line 164-167).

```python
# ---------- AI model registry (docs.md §4 ai_models / §5 Table 11) ----------
class GradingStrictness(models.TextChoices):
    """Admin-tunable rubric severity (RBAC row 13). The AI backend maps this to
    its scoring curve; the mock backend uses it to scale the deterministic score."""
    LENIENT = "lenient", "Lenient"
    STANDARD = "standard", "Standard"
    STRICT = "strict", "Strict"


class AiModel(models.Model):
    """A configurable multimodal-evaluation engine target. Admins register/edit
    rows here (UC-20/21); the AI service layer reads the active row to decide
    which endpoint/version/strictness to evaluate with. Only one row is treated
    as the live model — see ``active()`` below."""

    model_name = models.CharField(max_length=100)
    endpoint_url = models.CharField(max_length=255)
    version_identifier = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    strictness = models.CharField(
        max_length=20,
        choices=GradingStrictness.choices,
        default=GradingStrictness.STANDARD,
    )
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ai_models_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_models"

    def __str__(self):
        return f"{self.model_name} ({self.version_identifier})"

    @classmethod
    def active(cls):
        """The live model row, or ``None`` if none configured. Most-recently
        updated active row wins, so toggling ``is_active`` on a newer row swaps
        engines without disrupting in-flight evaluations (docs.md §5 Table 11)."""
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()
```

> `score`/`comments` on `Feedback` are already nullable decimal/text (`core/models.py:345-348`); AI rows reuse them as-is. `is_ai_generated` comes from file 01.

### `core/ai/__init__.py` (NEW package)

Marks `core/ai/` as a package and re-exports the public service entry points so callers do `from core.ai import evaluate_submission`.

```python
"""AI evaluation suite (docs.md §1.5/§3.4).

Mockable by design: ``AI_BACKEND=mock`` (default) uses a deterministic stub so
the API shape matches docs.md without live OpenAI/Whisper keys. ``AI_BACKEND=real``
selects the documented (stubbed) HTTP backend. See ``backends.py`` for the
stub-vs-real boundary and the env/keys a production deploy needs.
"""

from .service import evaluate_submission, get_backend, grade_submission  # noqa: F401
```

### `core/ai/backends.py` (NEW — the stub-vs-real boundary)

The clean interface (`grade_writing`, `transcribe_and_score_speaking`) plus a deterministic `MockBackend` (default) and a clearly-stubbed `RealBackend`. Honors docs.md §6 directive #2: branch on `exercise.exercise_type`.

```python
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
```

### `core/ai/service.py` (NEW — orchestration + Feedback writer)

Selects the backend from settings + the active `AiModel`, dispatches on `exercise_type`, writes the `Feedback` row (`is_ai_generated=True`, `reviewer=None`), and advances `Submission.status` to `AI_GRADED` (file 04 lifecycle). **Synchronous for MVP**, but isolated behind one function so it can move to a worker later (see Deferred).

```python
"""AI evaluation orchestration.

evaluate_submission() is the single trigger point. It runs SYNCHRONOUSLY today
but is the natural .delay()/enqueue boundary when a broker is added — callers
never touch backends directly.
"""

from __future__ import annotations

from django.conf import settings

from ..models import (
    AiModel,
    ExerciseType,
    Feedback,
    SubmissionStatus,  # from file 01
)
from .backends import MockBackend, RealBackend

_AUDIO = {ExerciseType.SPEAKING, ExerciseType.LISTENING}


def get_backend():
    """Pick the backend from AI_BACKEND (default 'mock'), bound to the active
    AiModel row so it can read endpoint_url + strictness."""
    name = getattr(settings, "AI_BACKEND", "mock")
    ai_model = AiModel.active()
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
    core/models.py:339 related_name='feedback')."""
    result = grade_submission(submission)
    fb = Feedback.objects.create(
        submission=submission,
        reviewer=None,
        is_ai_generated=True,          # field from file 01
        score=result["score"],
        comments=result["comments"],
    )
    # Advance lifecycle (file 04). Guard the attr so this file degrades safely
    # if the 01/04 status field is not yet applied.
    if hasattr(submission, "status"):
        submission.status = SubmissionStatus.AI_GRADED
        submission.save(update_fields=["status"])
    return fb
```

### `core/serializers.py`

Add an `AiModelSerializer` (admin CRUD) and a tiny request serializer for the student practice endpoint. Follow the `*_id` source-alias idiom (`updated_by_id`) used throughout (`teacher_id` line 73-76, `student_id` line 86, etc.). Append after `FeedbackSerializer` (after line 234). Import `AiModel` into the existing `from .models import (...)` block (`Exercise` and `Submission` are already imported, lines 16/21).

```python
# ---------- AI model registry ----------
class AiModelSerializer(serializers.ModelSerializer):
    # Server-set on write to the acting admin (view does perform_create/update);
    # surfaced read-only on the wire as updated_by_id.
    updated_by_id = serializers.PrimaryKeyRelatedField(
        source="updated_by", read_only=True
    )

    class Meta:
        model = AiModel
        fields = [
            "id", "model_name", "endpoint_url", "version_identifier",
            "is_active", "strictness", "updated_by_id", "updated_at",
        ]
        read_only_fields = ["id", "updated_by_id", "updated_at"]


# ---------- AI practice (RBAC row 12, UC-09) ----------
class AiPracticeSerializer(serializers.Serializer):
    """Ad-hoc student practice against an exercise — no Assignment required.
    Mirrors SubmissionSerializer's writable payload fields."""
    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all()
    )
    writing_text = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    audio_recording_url = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=500
    )
```

> `AiPractice` returns existing `SubmissionSerializer` + `FeedbackSerializer` shapes (see the view), so no new response serializer is needed.

### `core/views.py`

**(a)** New imports — add `AiModel` to the `from .models import (...)` block (lines 30-43) and import the service:

```python
from .models import (
    AiModel,        # NEW
    Assignment,
    # … existing imports unchanged …
)
from .ai import evaluate_submission   # NEW
```

**(b)** New `AiModelViewSet` (admin-only CRUD, UC-20/21, RBAC row 13). Full ModelViewSet under the `ai` tag; every action gated by `IsAdmin` via the repo's `_perms()` helper (`core/views.py:50-51`). Add after `FeedbackViewSet` (after line 440):

```python
# ============================================================ AI models
@extend_schema(tags=["ai"])
class AiModelViewSet(viewsets.ModelViewSet):
    """Admin-managed multimodal-evaluation engine registry (docs.md UC-20/21,
    RBAC row 13). Toggling is_active/strictness reconfigures the live AI grader."""

    queryset = AiModel.objects.all().order_by("id")
    serializer_class = s.AiModelSerializer

    def get_permissions(self):
        return _perms(IsAdmin)   # all actions admin-only

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
```

**(c)** AI-evaluate trigger + student practice — two `@action`s on the **existing** `SubmissionViewSet` (`core/views.py:389`). The class today is `mixins.CreateModelMixin + GenericViewSet`; the new actions add behavior without changing `create`/`me`/`feedback`. Drop these inside `SubmissionViewSet` (e.g. after the `feedback` action, line 427):

```python
    @extend_schema(
        tags=["ai"],
        summary="Request AI feedback for a submission (UC-17/18)",
        request=None,
        responses={201: s.FeedbackSerializer},
    )
    @action(detail=True, methods=["post"], url_path="ai-evaluate")
    def ai_evaluate(self, request, pk=None):
        """Teacher/Admin triggers AI grading on an existing submission. Runs the
        active backend SYNCHRONOUSLY (MVP) and writes an is_ai_generated Feedback
        row (reviewer=None). Structured to move to an async worker later."""
        if request.user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        submission = self.get_object()
        try:
            fb = evaluate_submission(submission)
        except (ValueError, NotImplementedError) as exc:
            # ValueError -> missing media (directive #2); NotImplementedError ->
            # RealBackend not wired. Surface as 400 rather than 500.
            raise ValidationError(str(exc))
        return Response(
            s.FeedbackSerializer(fb).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["ai"],
        summary="Autonomous AI skill practice (RBAC row 12, UC-09)",
        request=s.AiPracticeSerializer,
        responses={201: inline_serializer(
            name="AiPracticeResult",
            fields={
                "submission": s.SubmissionSerializer(),
                "feedback": s.FeedbackSerializer(),
            },
        )},
    )
    @action(
        detail=False, methods=["post"], url_path="ai-practice",
        permission_classes=_BASE + [IsStudent],
    )
    def ai_practice(self, request):
        """Student practices an exercise off-assignment and gets immediate AI
        feedback via the mock backend. Creates a Submission (assignment=None),
        derives submission_type from the exercise, then evaluates it."""
        ser = s.AiPracticeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        exercise = ser.validated_data["exercise"]
        submission = Submission.objects.create(
            exercise=exercise,
            student=request.user,
            assignment=None,
            submission_type=exercise.exercise_type,   # values align (settings note)
            writing_text=ser.validated_data.get("writing_text"),
            audio_recording_url=ser.validated_data.get("audio_recording_url"),
        )
        try:
            fb = evaluate_submission(submission)
        except (ValueError, NotImplementedError) as exc:
            raise ValidationError(str(exc))
        return Response(
            {
                "submission": s.SubmissionSerializer(submission).data,
                "feedback": s.FeedbackSerializer(fb).data,
            },
            status=status.HTTP_201_CREATED,
        )
```

> `ExerciseType` and `SubmissionType` share identical member *values* (writing/speaking/reading/listening/quiz — see the `ENUM_NAME_OVERRIDES` rationale at `config/settings.py:124-129`), so assigning `exercise.exercise_type` straight into `submission_type` is safe.
> `permission_classes` on the `@action` uses `_BASE + [IsStudent]` — these are permission **classes** (not the instances `_perms()` returns); DRF instantiates them, so do **not** wrap them in `_perms()` here. `_BASE` is defined at `core/views.py:47`; `IsStudent` is already imported (`core/views.py:44`).

### `core/permissions.py`

**No change.** Reuse existing `IsActiveUser`, `IsAdmin` (AiModel CRUD + admin ai-evaluate) and `IsStudent` (ai-practice). The teacher-or-admin gate on `ai-evaluate` is enforced inline in the action (mirrors the inline role check in `ExerciseViewSet.submissions`, `core/views.py:380-381`).

### `config/urls.py`

Register the new ViewSet on the existing `DefaultRouter` (after line 27). Bare-array list behavior and routing convention are inherited from the router.

```python
router.register("ai-models", views.AiModelViewSet, basename="ai-model")
```

> The `ai-evaluate` / `ai-practice` actions are auto-routed by the router under the already-registered `submissions` prefix (line 25) → `POST /api/submissions/{id}/ai-evaluate/` and `POST /api/submissions/ai-practice/`. No extra `path()` entries.

### `config/settings.py`

**(a)** Read the backend selector from env (default `mock`). Add near the other `os.getenv` settings (e.g. after `CORS_ALLOW_CREDENTIALS`, line 159):

```python
# AI evaluation suite (docs.md §1.5). 'mock' = deterministic stub (default, no
# keys). 'real' = documented OpenAI Whisper STT + LLM backend (stubbed in
# core/ai/backends.py:RealBackend). A real deploy also needs OPENAI_API_KEY and
# an HTTP client (requests/httpx — not in requirements.txt yet).
AI_BACKEND = os.getenv("AI_BACKEND", "mock")
```

**(b)** Add the `ai` tag to `SPECTACULAR_SETTINGS["TAGS"]` (after the `progress` entry, line 141):

```python
        {"name": "progress", "description": "Per-student module progress"},
        {"name": "ai", "description": "AI evaluation: model config, AI feedback, student practice"},
```

> `GradingStrictness` is a new TextChoices and will surface as a new enum component in the schema. Its member values are distinct from the shared `SkillType` set, so it needs **no** `ENUM_NAME_OVERRIDES` entry (`config/settings.py:127-129`).

### `core/admin.py`

Register `AiModel` (the migration's whole point — admins manage AI config from `/admin/`). Add `AiModel` to the `from .models import (...)` block (lines 12-25) and append a registration after `FeedbackAdmin` (line 189):

```python
@admin.register(AiModel)
class AiModelAdmin(admin.ModelAdmin):
    list_display = (
        "id", "model_name", "version_identifier",
        "is_active", "strictness", "updated_by", "updated_at",
    )
    list_filter = ("is_active", "strictness")
    search_fields = ("model_name", "version_identifier")
    raw_id_fields = ("updated_by",)
    readonly_fields = ("updated_at",)
```

### `core/management/commands/seed_demo.py`

Wipe `AiModel` (the command deletes children-first at the top of `handle`, lines 46-55) and seed one active default model. Add `AiModel`, `GradingStrictness` to the `from core.models import (...)` block (lines 18-34).

Wipe — add alongside the other `.delete()` calls, before `User.objects.all().delete()` (line 55). Order is harmless either way since `updated_by` is `SET_NULL`, but keep it above the User wipe to delete the rows outright:

```python
        AiModel.objects.all().delete()
```

Seed — after the Users block so `admin` exists (e.g. after line 98):

```python
        # ---------- AI model (docs.md §4 ai_models) ----------
        self.stdout.write("Seeding AI model config…")
        AiModel.objects.create(
            model_name="ILLMS Mock Evaluator",
            endpoint_url="https://mock.ai/illms/evaluate",
            version_identifier="mock-v1",
            is_active=True,
            strictness=GradingStrictness.STANDARD,
            updated_by=admin,
        )
```

Add a count line to the summary block (near line 359):

```python
        self.stdout.write(f"  AiModels:     {AiModel.objects.count():>4}")
```

> Optional but recommended: after the Feedback seed block (line 296-325), also seed one **AI** feedback row to exercise the `is_ai_generated=True, reviewer=None` shape, e.g.
> `Feedback.objects.create(submission=submissions[6], reviewer=None, is_ai_generated=True, score=Decimal("79.00"), comments="[AI · mock] Auto-evaluated essay; vary sentence openings.")`.
> This is additive demo data; the existing teacher-feedback rows (which set `reviewer=teachers[...]`) are unchanged. `Decimal` is already imported (line 12).

---

## Migrations

TextChoices = plain varchar; no native enum / `ALTER TYPE`. After 01 and 04 are applied:

```bash
python manage.py makemigrations core   # creates ai_models table + strictness varchar column
python manage.py migrate
```

- **New table only** (`ai_models`) plus its own `strictness` varchar column — no alteration of existing tables, so no backfill and zero risk to current data.
- `GradingStrictness` ships in the same migration as the table; nothing to backfill (the column has a `default`).
- Migration numbering continues from the existing `0001_initial` / `0002_…`; this will be `0003_…` (or higher if 01/04 land first). `DEFAULT_AUTO_FIELD = BigAutoField` (`config/settings.py:169`) gives `AiModel.id` a `bigint` PK automatically.
- Re-seed to get the default model row + (optional) AI feedback row: `python manage.py seed_demo`.

---

## RBAC / permissions

| Capability | Endpoint | Role | Enforced by |
|---|---|---|---|
| Manage AI endpoints & strictness (RBAC row 13, UC-20/21) | `GET/POST/PUT/PATCH/DELETE /api/ai-models/` | Admin | `AiModelViewSet.get_permissions → _perms(IsAdmin)` |
| Request AI feedback on a submission (UC-17/18) | `POST /api/submissions/{id}/ai-evaluate/` | Teacher or Admin | inline role check in `ai_evaluate` (mirrors `ExerciseViewSet.submissions`) |
| Autonomous AI skill practice (RBAC row 12, UC-09) | `POST /api/submissions/ai-practice/` | Student | `@action(permission_classes=_BASE + [IsStudent])` |

- Reuses existing `IsActiveUser` (suspended/inactive users get 403 — already global via `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]`, `config/settings.py:106-109`), `IsAdmin`, `IsStudent`. **No new permission class.**
- AI `Feedback` rows are `reviewer=None, is_ai_generated=True`; clients/teachers must use `is_ai_generated` to attribute them and never assume a reviewer (docs.md §6 directive #1).
- **Contract preservation:** existing `FeedbackViewSet` (human feedback, `reviewer=request.user`, `core/views.py:439-440`) is untouched. The login response shape (`{access_token, refresh_token, token_type:"bearer"}`) and all current endpoints are unchanged — every addition is a new route or a new `@action`.

---

## Acceptance criteria & smoke test

- [ ] `python manage.py makemigrations core && python manage.py migrate` creates `ai_models` with `strictness`; no changes to existing tables.
- [ ] `AiModel` appears in `/admin/` and is editable by the admin superuser.
- [ ] `python manage.py seed_demo` runs clean and reports an `AiModels: 1` line.
- [ ] `/api/docs/` shows an **ai** tag with `ai-models` CRUD, `submissions/{id}/ai-evaluate/`, `submissions/ai-practice/`.
- [ ] Admin can CRUD `/api/ai-models/`; a teacher or student gets 403 on write.
- [ ] Teacher `POST /ai-evaluate/` on a writing submission creates a `Feedback` row with `reviewer_id: null` and a deterministic score; the submission status becomes `AI_Graded` (file 04).
- [ ] Student `POST /ai-practice/` returns `{submission, feedback}` with AI feedback; teacher/admin get 403.
- [ ] Speaking practice with no `audio_recording_url` returns **400** (directive #2), not 500.
- [ ] With `AI_BACKEND=real` (unset keys), AI endpoints return **400** with the "RealBackend … is a stub" message — never a silent wrong grade.

Smoke test (httpie; seeded users, password `password123`):

```bash
API=http://localhost:8000

# --- Admin: configure the AI model (RBAC row 13) ---
ADMIN=$(http --form POST $API/api/auth/login email=admin@english.app password=password123 | jq -r .access_token)
http GET  $API/api/ai-models/ "Authorization:Bearer $ADMIN"          # bare array, seeded row
http POST $API/api/ai-models/ "Authorization:Bearer $ADMIN" \
     model_name="GPT-4o Grader" endpoint_url="https://mock.ai/v2" \
     version_identifier="v2" is_active:=true strictness=strict
# -> 201, updated_by_id == admin id

# --- Teacher: AI-evaluate an existing submission (UC-17/18) ---
TEACH=$(http --form POST $API/api/auth/login email=emma.teacher@english.app password=password123 | jq -r .access_token)
# pick a writing submission id from an exercise the teacher can see (exercise 8 = persuasive essay):
SUB=$(http GET $API/api/exercises/8/submissions/ "Authorization:Bearer $TEACH" | jq -r '.[0].id')
http POST $API/api/submissions/$SUB/ai-evaluate/ "Authorization:Bearer $TEACH"
# -> 201 Feedback: { "reviewer_id": null, "score": "<num>", "comments": "[AI · mock] …" }

# --- Student: autonomous AI practice (RBAC row 12 / UC-09) ---
STU=$(http --form POST $API/api/auth/login email=alice.student@english.app password=password123 | jq -r .access_token)
# Writing practice (exercise 4 = a writing exercise in the seed):
http POST $API/api/submissions/ai-practice/ "Authorization:Bearer $STU" \
     exercise_id:=4 writing_text="However, on balance the evidence shows that uniforms help."
# -> 201 { "submission": {...}, "feedback": { "reviewer_id": null, "comments":"[AI · mock] …" } }
# Speaking practice (exercise 1 = a speaking exercise; mock transcript):
http POST $API/api/submissions/ai-practice/ "Authorization:Bearer $STU" \
     exercise_id:=1 audio_recording_url="https://mock.cdn/recordings/alice-practice.m4a"
# Speaking practice with NO audio -> 400:
http POST $API/api/submissions/ai-practice/ "Authorization:Bearer $STU" exercise_id:=1
# -> 400 "Speaking submission has no audio_recording_url"

# --- Negative RBAC ---
http POST $API/api/ai-models/ "Authorization:Bearer $TEACH" model_name=x endpoint_url=y version_identifier=z   # 403
http POST $API/api/submissions/ai-practice/ "Authorization:Bearer $TEACH" exercise_id:=4 writing_text=hi        # 403
```

Shell check (deterministic mock + nullable evaluator, docs.md §6 directive #1):

```python
# python manage.py shell
from core.models import Submission
from core.ai import evaluate_submission
sub = Submission.objects.filter(submission_type="writing", writing_text__isnull=False).first()
fb = evaluate_submission(sub)
assert fb.is_ai_generated is True and fb.reviewer_id is None
assert evaluate_submission(sub).score == fb.score   # deterministic across runs
```

---

## Deferred / out of scope

- **Async worker.** `evaluate_submission()` runs synchronously; it is the single enqueue boundary. To make it a background task: add Celery + Redis (or RQ / django-q) — **none are in `requirements.txt` today** — wrap the call in a `@shared_task`, and have `ai_evaluate`/`ai_practice` return `202 Accepted` + poll. docs.md §3.4 "asynchronous worker" is satisfied *interface-wise* now; the broker is the deferred part.
- **Real Whisper / LLM.** `RealBackend` is a documented stub. Wiring needs `OPENAI_API_KEY`, an HTTP client (requests/httpx), audio download from `audio_recording_url`, and a grading prompt parameterised by `strictness` — out of scope per the realism policy.
- **WebSocket live speaking** (docs.md §1.5, §6 directive #4): real-time ASGI/Channels streaming is a separate concern; `config/asgi.py` is still plain `get_asgi_application()`. Not touched here.
- **Auto-evaluate on submit.** This file keeps AI grading **explicit** (`ai-evaluate` / `ai-practice`) and does not change `SubmissionViewSet.perform_create` (`core/views.py:404-408`). Auto-triggering AI on every writing/speaking submission can be layered on later by calling `evaluate_submission()` from `perform_create` for productive types.
- **Strictness as a numeric curve / per-exercise override.** Modeled as a 3-value choice on `AiModel`; a continuous temperature or per-exercise strictness is a future enhancement.
- **`study_materials` (file 03), `system_logs` (file 07)**, and the `Exercise.content_text` / `Submission.status` / `Feedback.is_ai_generated` field *definitions* (files 01/04) — this file only **consumes** them; it does not define them (see ownership map).

# 04 — Grading & evaluations: submission status lifecycle, AI attribution, teacher inbox

**Status:** Proposed · **Depends on:** 01 (Submission.status + SubmissionStatus TextChoices, Feedback.is_ai_generated, Exercise.created_by), 02 (feedback-read ownership guard), 06 (AI grading sets Feedback.is_ai_generated=True) · **Spec refs:** docs.md §3.3 UC-14/15 (Multimodal Review Suite), §3.4 UC-17/18 (AI Automated Feedback), §4 `submission_status` enum + `evaluations` table, §5 Table 8/10, §6 directive #1 (Nullable Evaluator Handling); RBAC matrix rows 5 (Teacher Grading Dashboard), 10 (Grade Submissions & Leave Remarks), 11 (Review Graded Results); UI_FLOW_BRIEF J4 + screen #23 (Submissions inbox)

**Estimated blast radius:** `core/views.py` (FeedbackViewSet.perform_create, SubmissionViewSet: new `inbox` action + import of SubmissionStatus and Q), `core/serializers.py` (new `SubmissionInboxSerializer`). No model migrations in THIS file (fields come from 01). No change to `config/urls.py` (inbox is an `@action` on the already-registered `submissions` router). No new spectacular tag. No frontend-contract breakage.

---

## Goal
Wire the grading workflow to docs.md `evaluations` semantics. When a `Feedback` row is created, flip the parent `Submission.status` (defined in file 01) to `SubmissionStatus.GRADED` (stored `"graded"`) for human teachers or `SubmissionStatus.AI_GRADED` (stored `"ai_graded"`) for AI-authored feedback (`is_ai_generated=True`, set by file 06), honoring docs.md §6 directive #1 (nullable evaluator). Add a teacher **submissions inbox** (`GET /api/submissions/inbox/`) that aggregates submissions across the teacher's *owned* exercises — including self-practice submissions where `assignment` is null — annotated graded/ungraded and filterable by status/class/exercise. This satisfies RBAC rows 5/10/11 and UI_FLOW_BRIEF J4 (login → inbox → filter "ungraded" → grade → next).

## Why (gap)
- **No status lifecycle.** `Submission` (core/models.py:277) has no `status` field today; grading state is only *implicitly* derivable from whether a `Feedback` row exists. docs.md §4 defines `submission_status {Pending, Graded, AI_Graded}` with `[default: 'Pending']` on `submissions.status`. File 01 adds the field + `SubmissionStatus` TextChoices, storing **lowercase** values (`pending`/`graded`/`ai_graded`) with the docs.md spellings kept as human labels — so all wire/filter values in this file are lowercase. **This file makes the status actually transition.** Right now `FeedbackViewSet.perform_create` (core/views.py:439-440) just does `serializer.save(reviewer=self.request.user)` and never touches the submission.
- **No teacher inbox.** The only teacher-facing list of submissions is per-exercise: `ExerciseViewSet.submissions` → `GET /api/exercises/{id}/submissions/` (core/views.py:378-384), which returns `Submission.objects.filter(exercise=ex)` for *one* exercise and does not scope to ownership (any teacher can read any exercise's submissions). UI_FLOW_BRIEF screen #23 + J4 require a *cross-exercise* queue scoped to the teacher's own exercises, with a graded/ungraded flag and status/class/exercise filters. Nothing like that exists.
- **AI attribution not honored.** docs.md §6 directive #1: never assume `evaluator_id` is NOT NULL — inspect `is_ai_generated`. `Feedback.reviewer` is already nullable (core/models.py:341-344), but `perform_create` unconditionally stamps `reviewer=self.request.user`, so an AI-graded path (file 06) needs a code branch that leaves `reviewer` null and sets `status=AI_Graded`. This file defines that branch point.
- **Self-practice submissions silently dropped by naive joins.** Seed data creates `assignment=None` submissions (seed_demo.py:285-292: a writing self-practice by Alice on exercise index 3, a speaking self-practice by Bob on exercise index 9). Any inbox built by joining `Submission → Assignment → Assignment.klass` would silently exclude these. docs.md RBAC row 12 (Autonomous AI Skill Practice Area) explicitly produces assignment-less submissions, so the inbox MUST scope by exercise ownership, not by assignment/class.

---

## Changes (file by file)

### core/views.py — FeedbackViewSet.perform_create (status flip + AI attribution)
Replace the one-line `perform_create` (core/views.py:439-440) with a version that flips the parent submission's status based on `Feedback.is_ai_generated` (field from file 01; set True only by the AI path in file 06). Human-authored feedback → `Graded` + `reviewer` set; AI-authored feedback → `AI_Graded` + `reviewer` left **null** (docs.md §6 #1). Score validation 0–100 and reviewer auto-set are confirmed below.

Current `FeedbackViewSet` for context (unchanged parts elided):

```python
# ============================================================ Feedback
@extend_schema(tags=["feedback"])
class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Feedback.objects.all().order_by("id")
    serializer_class = s.FeedbackSerializer

    def get_permissions(self):
        return _perms(IsTeacherOrAdmin)

    def perform_create(self, serializer):
        # was: serializer.save(reviewer=self.request.user)
        # AI-authored feedback (is_ai_generated=True, set only by file 06's AI
        # grading path) must leave evaluator/reviewer NULL per docs.md §6 #1.
        is_ai = serializer.validated_data.get("is_ai_generated", False)
        if is_ai:
            feedback = serializer.save(reviewer=None)
            new_status = SubmissionStatus.AI_GRADED
        else:
            feedback = serializer.save(reviewer=self.request.user)
            new_status = SubmissionStatus.GRADED

        sub = feedback.submission
        sub.status = new_status
        sub.save(update_fields=["status"])
```

> Notes:
> - `is_ai_generated` is a **NEW** `Feedback` field defined in **file 01** (BooleanField, `default=False`). Adding it to `FeedbackSerializer.fields` is also file 01's job. Over the human teacher POST path it stays `False`, so existing teacher grading is unchanged — `Graded` + `reviewer=<teacher>` exactly as before, plus the new status flip.
> - `SubmissionStatus` is the **NEW** TextChoices defined in **file 01** on `core/models.py`. Import it here alongside the existing `SubmissionType` import (see import edit below). **Use the enum members, never literal strings** — file 01 stores **lowercase** values (`PENDING = "pending"`, `GRADED = "graded"`, `AI_GRADED = "ai_graded"`; the title-case `"Pending"/"Graded"/"AI_Graded"` from docs.md §4 are the *human labels*, not the stored varchar). So `SubmissionStatus.GRADED` and `SubmissionStatus.AI_GRADED` evaluate to `"graded"`/`"ai_graded"`, and `?status=` filters / wire-facing assertions must use the lowercase stored values. This file references the members so it stays correct regardless; the smoke tests below use the stored values explicitly.
> - Through the normal browsable-API / frontend POST, `is_ai_generated` defaults False because nothing sets it; only file 06's internal AI grading call passes `is_ai_generated=True`. **Optional hardening (recommended):** make `is_ai_generated` read-only in the public serializer and have file 06 set it directly on the model in its own service call, so an external client can't self-attribute AI grading. Decide in file 06; this file only consumes the flag.

### core/views.py — imports
`SubmissionStatus` must be importable in views. Extend the existing model import block (core/views.py:30-43):

```python
from .models import (
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    StudentModuleProgress,
    Submission,
    SubmissionStatus,   # NEW — TextChoices defined in file 01
    SubmissionType,
    User,
    UserRole,
)
```

Add `Q` to the Django imports at the top of views.py (used by the inbox queryset):

```python
from django.db.models import Q   # NEW
```

### core/views.py — SubmissionViewSet.inbox (NEW teacher inbox action)
Add a `detail=False` action to the already-router-registered `SubmissionViewSet` (core/views.py:388-427). Endpoint: `GET /api/submissions/inbox/`. Returns submissions across the requesting teacher's **owned** exercises, each annotated with graded/ungraded + status, filterable by `status`, `class_id`, and `exercise_id` query params. Admins see all submissions (RBAC row 10 grants Admin everything Teacher has).

**Ownership model:** primary = `Exercise.created_by` (NEW field from file 01). Because pre-existing seed exercises were created before `created_by` existed and may be null, fall back to `module.created_by` (existing, core/models.py:164-167) — the teacher who owns the parent module. The OR of those two covers both new and legacy rows.

**CRITICAL:** scope by exercise ownership, NOT by `Assignment.klass`. Joining through assignment drops every self-practice submission (`assignment IS NULL`, seed_demo.py:285-292). The queryset below filters on `exercise__created_by` / `exercise__module__created_by`, so assignment-less submissions owned by the teacher are included.

```python
    @extend_schema(
        summary="Teacher inbox: submissions across the teacher's own exercises",
        description=(
            "Aggregates submissions for every exercise the requesting teacher "
            "owns (Exercise.created_by, with module.created_by fallback), "
            "including self-practice submissions where assignment is null. "
            "Admins see all submissions. Optional filters: status, class_id, "
            "exercise_id."
        ),
        parameters=[
            OpenApiParameter(
                "status", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by submission status (stored values): pending | graded | ai_graded",
                enum=list(SubmissionStatus.values),
            ),
            OpenApiParameter(
                "class_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Filter to submissions whose assignment targets this class",
            ),
            OpenApiParameter(
                "exercise_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Filter to a single exercise",
            ),
        ],
        responses={200: s.SubmissionInboxSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="inbox")
    def inbox(self, request):
        user = request.user
        if user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")

        qs = (
            Submission.objects.all()
            .select_related("exercise", "student", "assignment")
            .prefetch_related("feedback")
            .order_by("-submitted_at", "-id")
        )

        # Ownership scope — teachers see only their own exercises' submissions.
        # Admins (row 10) see everything, so skip the ownership filter for them.
        if user.role == UserRole.TEACHER:
            qs = qs.filter(
                Q(exercise__created_by=user)
                | Q(exercise__module__created_by=user)
            )

        # --- Optional filters ---
        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in SubmissionStatus.values:
                raise ValidationError({"status": "Invalid submission status"})
            qs = qs.filter(status=status_param)

        exercise_id = request.query_params.get("exercise_id")
        if exercise_id:
            qs = qs.filter(exercise_id=exercise_id)

        class_id = request.query_params.get("class_id")
        if class_id:
            # Filters via the assignment's class. Self-practice submissions
            # (assignment IS NULL) have no class, so a class_id filter
            # intentionally excludes them.
            qs = qs.filter(assignment__klass_id=class_id)

        qs = qs.distinct()   # Q-join across exercise+module FKs can duplicate rows
        return Response(s.SubmissionInboxSerializer(qs, many=True).data)
```

> The exact queryset requested in scope, isolated:
> ```python
> Submission.objects.filter(
>     Q(exercise__created_by=user) | Q(exercise__module__created_by=user)
> ).distinct()
> ```
> For admin, drop the `.filter(...)` entirely (all submissions). The `Q(...module__created_by...)` leg is the legacy fallback; once file 01's backfill stamps `Exercise.created_by` on every row, the first leg alone suffices, but keep both for safety.

### core/serializers.py — SubmissionInboxSerializer (NEW)
The plain `SubmissionSerializer` (core/serializers.py:200-217) is fine for a single submission, but the inbox needs the derived **graded/ungraded** flag, the **status**, and lightweight latest-evaluation attribution honoring docs.md §6 #1. Add a read-only serializer that extends the existing one rather than duplicating its field list. It reuses the existing `*_id` source-alias idiom.

```python
class SubmissionInboxSerializer(SubmissionSerializer):
    """Read-only inbox row: existing submission fields + grading-state
    annotations derived from Feedback. Honors docs.md §6 directive #1 —
    grading attribution is decided by is_ai_generated, never by assuming a
    teacher reviewer exists."""

    # NOTE: `status` is NOT declared/added here. File 01 already surfaces it on
    # the parent `SubmissionSerializer` (added to Meta.fields + read_only_fields),
    # so it is inherited automatically. Re-declaring it or appending it to
    # `fields` again would list "status" twice and is wrong.
    is_graded = serializers.SerializerMethodField()
    grading_source = serializers.SerializerMethodField()    # "teacher" | "ai" | None
    latest_score = serializers.SerializerMethodField()
    student_name = serializers.CharField(
        source="student.full_name", read_only=True
    )

    class Meta(SubmissionSerializer.Meta):
        # Parent already includes "status" (file 01); only append the new keys.
        fields = SubmissionSerializer.Meta.fields + [
            "is_graded", "grading_source",
            "latest_score", "student_name",
        ]

    def _latest_feedback(self, obj):
        # `feedback` is prefetched in the inbox queryset; sort in Python so the
        # prefetch cache is reused (no extra query per row).
        fbs = list(obj.feedback.all())
        return max(fbs, key=lambda f: (f.created_at, f.id)) if fbs else None

    def get_is_graded(self, obj):
        return bool(obj.feedback.all())

    def get_grading_source(self, obj):
        fb = self._latest_feedback(obj)
        if fb is None:
            return None
        # docs.md §6 #1: decide by is_ai_generated, not by reviewer presence.
        return "ai" if fb.is_ai_generated else "teacher"

    def get_latest_score(self, obj):
        fb = self._latest_feedback(obj)
        return str(fb.score) if (fb and fb.score is not None) else None
```

> - `SubmissionSerializer.Meta.fields` (core/serializers.py:212-216) already includes `auto_score`, so receptive auto-graded submissions surface their numeric score in the inbox without extra work.
> - `is_graded` derives from `Feedback` existence per scope ("annotated graded/ungraded — derive from Feedback existence"). `status` (the persisted lifecycle value) is shown alongside it; for receptive auto-graded items the two can legitimately differ (see "receptive submissions" decision below).
> - `grading_source` returns `"ai"` purely from `is_ai_generated`, so an AI evaluation with a null `reviewer` is attributed correctly and never triggers a teacher-profile lookup (docs.md §6 #1). `is_ai_generated` is the NEW Feedback field from file 01.
> - This serializer is **additive** — it does not change `SubmissionSerializer`, so the existing `GET /api/submissions/me/`, `GET /api/exercises/{id}/submissions/`, and POST contracts are untouched.

### Receptive auto-graded submissions — status decision
`SubmissionViewSet.perform_create` (core/views.py:404-408) calls `submission.grade()` for receptive types (reading/listening/quiz), setting `auto_score` but creating **no** `Feedback` row. Decision for this file:

- **Recommended (shipped here):** leave receptive submissions at the default `status` (`SubmissionStatus.PENDING`, stored `"pending"`) as far as the lifecycle enum is concerned, and let the inbox surface them via the `auto_score` field + `is_graded=False`. This keeps `graded`/`ai_graded` meaning "an `evaluations`/`Feedback` row exists" (true to docs.md Table 10), and avoids inventing a fourth status that docs.md §4 doesn't define. No change to `perform_create`.
- **Alternative (defer):** treat a non-null `auto_score` as terminal by flipping `status=SubmissionStatus.GRADED` inside `perform_create` after `grade()`. Only do this if the frontend's "ungraded" filter should exclude auto-graded receptive work. Do not implement speculatively — it is a frontend-driven decision. If chosen, the one-line edit is:

```python
    def perform_create(self, serializer):
        submission = serializer.save(student=self.request.user)
        if submission.submission_type in self._RECEPTIVE:
            submission.grade()
            # OPTIONAL: mark receptive auto-grade terminal (see file 04 note)
            # submission.status = SubmissionStatus.GRADED
            submission.save(update_fields=["auto_score"])  # add "status" if uncommented
```

### config/settings.py — SPECTACULAR_SETTINGS
No new tag required. The inbox action lives on `SubmissionViewSet` (tag `submissions`, already registered at settings.py:139) and the status-flip lives on `FeedbackViewSet` (tag `feedback`, settings.py:140). **No edit needed** — stated explicitly so the implementer does not add a redundant tag.

### core/admin.py
File 01 owns adding `status` to `SubmissionAdmin.list_display` / `list_filter` and `is_ai_generated` to `FeedbackAdmin`. **No admin edit in this file.** Cross-ref only — when file 01 lands, verify `status` appears in `SubmissionAdmin.list_filter` so admins can triage from `/admin/` too.

### core/management/commands/seed_demo.py
File 01 owns backfilling `Submission.status` and `Feedback.is_ai_generated` in the seed. **No seed edit in this file.** Cross-ref: file 01's seed should set `status=SubmissionStatus.GRADED` (stored `"graded"`) on the submissions that receive a `Feedback` row (seed_demo.py:296-325 lists exactly which submission indices get feedback) and leave the rest at the default `SubmissionStatus.PENDING` (stored `"pending"`), so the inbox demo shows a realistic graded/ungraded mix. Optionally file 06 seeds one `is_ai_generated=True` evaluation with `reviewer=None` to demo the `ai_graded` path + null-evaluator attribution.

---

## Migrations
**None in this file.** All schema changes (`Submission.status`, `Feedback.is_ai_generated`, `Exercise.created_by`) are defined and migrated by **file 01**. `SubmissionStatus` is a `TextChoices` → plain `varchar`; adding/using it is a model edit + `makemigrations`, with **no `ALTER TYPE`** (the old SQLAlchemy native-enum procedure in REFACTOR_PLAN.md does NOT apply).

This file's code is pure view/serializer logic and adds zero columns. Sequence: land file 01's migration first (`python manage.py makemigrations core && python manage.py migrate`), then apply this file's view/serializer edits. No data migration here; the inbox's `module__created_by` fallback covers any row whose `Exercise.created_by` file 01 could not backfill.

---

## RBAC / permissions
- **Inbox** (`GET /api/submissions/inbox/`): Teacher + Admin only. Enforced by an in-action role check (`user.role not in (TEACHER, ADMIN) → PermissionDenied`), matching the existing pattern in `ExerciseViewSet.submissions` (core/views.py:380-381). Teachers are further scoped to their own exercises by the queryset `Q(exercise__created_by=user) | Q(exercise__module__created_by=user)`; Admins skip the ownership filter (RBAC row 10 grants Admin the Teacher grading capability platform-wide). The default action permissions on `SubmissionViewSet.get_permissions` already require `IsAuthenticated + IsActiveUser` via `_perms()` (core/views.py:393-396), so suspended users are rejected upstream — **do not re-implement** (`IsActiveUser`, permissions.py:9). Maps to RBAC rows 5 + 10.
- **Grade / leave remarks** (`POST /api/feedback/`): unchanged — `FeedbackViewSet.get_permissions` already returns `_perms(IsTeacherOrAdmin)` (core/views.py:436-437). RBAC row 10. The new `perform_create` adds no new permission surface; the only new behavior is the status flip and the AI-attribution branch.
- **Review graded results** (RBAC row 11, Student): served by the existing `SubmissionViewSet.feedback`/`me` actions — out of scope here except for the read-ownership guard, which is **file 02's** job. **Cross-ref file 02:** `SubmissionViewSet.feedback` (core/views.py:423-427) currently lets any authenticated user read any submission's feedback via `self.get_object()` with no ownership check. Do NOT fix it here — file 02 owns that guard. This file's `is_graded`/`grading_source`/`latest_score` annotations are read-only and do not change who can read what.
- **No new permission classes.** Reuse `IsActiveUser`, `IsTeacherOrAdmin` and the `_perms(...)` helper exactly as the rest of the codebase does.

## Acceptance criteria & smoke test
Checklist:
- [ ] `POST /api/feedback/` by a teacher sets `Submission.status = "graded"` (stored value of `SubmissionStatus.GRADED`), `Feedback.reviewer = <teacher>`, `Feedback.is_ai_generated = False`.
- [ ] An AI-authored Feedback (`is_ai_generated=True`, exercised by file 06) sets `Submission.status = "ai_graded"` (stored value of `SubmissionStatus.AI_GRADED`) and leaves `Feedback.reviewer = NULL` (docs.md §6 #1).
- [ ] Score outside 0–100 is rejected (see validation note below).
- [ ] `GET /api/submissions/inbox/` as a teacher returns only submissions for that teacher's own exercises, **including** the seeded self-practice (assignment-null) submissions when the teacher owns the relevant exercise/module.
- [ ] `GET /api/submissions/inbox/` as an admin returns all submissions.
- [ ] `?status=Pending`, `?exercise_id=...`, `?class_id=...` filters narrow results; an invalid `status` returns HTTP 400.
- [ ] Each inbox row carries `status`, `is_graded`, `grading_source` (`teacher`/`ai`/null), `latest_score`, `auto_score`, `student_name`.
- [ ] Existing endpoints (`/api/submissions/me/`, `/api/exercises/{id}/submissions/`, login response shape `{access_token, refresh_token, token_type:"bearer"}`) are byte-for-byte unchanged.

Score validation (0–100) — confirm/enforce. docs.md `evaluations.score` is `decimal(5,2)` and UC-14/15 says "0-100". `FeedbackSerializer` (core/serializers.py:221-233) does **not** declare `score` explicitly — ModelSerializer auto-generates it from `Feedback.score` (`DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)`, core/models.py:345-347), which has **no min/max**, so 0–100 is NOT enforced today (only `max_digits=5` caps it at 999.99). File 01 owns the `FeedbackSerializer` field set; ask file 01 (or add there) to declare `score` explicitly and bound it:
```python
# core/serializers.py — FeedbackSerializer (field-level edit owned by file 01)
score = serializers.DecimalField(
    max_digits=5, decimal_places=2,
    min_value=Decimal("0"), max_value=Decimal("100"),
    required=False, allow_null=True,
)
```
(`Decimal` is already imported at core/serializers.py:7; `ProgressUpsertSerializer` at lines 124-129 uses the identical idiom.) If file 01 has not yet bounded it when this file lands, the inbox/status work still functions — this is a validation-tightening note, flagged so it isn't lost.

Smoke (replace `$ADMIN`/`$EMMA`/`$ALICE`; seeded users, password `password123`; Emma teaches classes 0/1 and owns modules 1/2 → exercises incl. the seeded self-practice writing by Alice on exercise index 3):
```bash
BACKEND=http://localhost:8000

# --- tokens ---
EMMA=$(curl -s -X POST $BACKEND/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"emma.teacher@english.app","password":"password123"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
ADMIN=$(curl -s -X POST $BACKEND/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@english.app","password":"password123"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 1) Teacher inbox — only Emma's exercises; self-practice (assignment=null) included.
curl -s $BACKEND/api/submissions/inbox/ -H "Authorization: Bearer $EMMA" | python -m json.tool
#   -> expect rows with assignment_id=null present (Alice's exercise-3 self-practice
#      belongs to module 1 / created_by Emma); each row has status + is_graded + grading_source.

# 2) Filter to ungraded only. NOTE: stored status values are lowercase
#    (pending/graded/ai_graded) — file 01 owns the enum; ?status=Pending would
#    match nothing.
curl -s "$BACKEND/api/submissions/inbox/?status=pending" -H "Authorization: Bearer $EMMA" | python -m json.tool

# 3) Invalid status -> 400.
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BACKEND/api/submissions/inbox/?status=Bogus" -H "Authorization: Bearer $EMMA"   # -> 400

# 4) Grade a submission -> status flips to Graded, reviewer = Emma.
#    Pick an ungraded submission id from step 2 (e.g. SID).
SID=<ungraded-submission-id-from-step-2>
curl -s -X POST $BACKEND/api/feedback/ -H "Authorization: Bearer $EMMA" \
  -H 'Content-Type: application/json' \
  -d "{\"submission_id\":$SID,\"score\":\"88.00\",\"comments\":\"Good work.\"}" | python -m json.tool
#   -> reviewer_id = Emma's id, is_ai_generated=false (default).
curl -s "$BACKEND/api/submissions/inbox/?status=graded" -H "Authorization: Bearer $EMMA" \
  | python -c 'import sys,json;print([r["id"] for r in json.load(sys.stdin)])'
#   -> SID now appears here.

# 5) Out-of-range score rejected (after file 01 bounds it) -> 400.
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BACKEND/api/feedback/ \
  -H "Authorization: Bearer $EMMA" -H 'Content-Type: application/json' \
  -d "{\"submission_id\":$SID,\"score\":\"150.00\"}"   # -> 400

# 6) Admin sees all submissions (no ownership scope).
curl -s $BACKEND/api/submissions/inbox/ -H "Authorization: Bearer $ADMIN" \
  | python -c 'import sys,json;print("admin count:",len(json.load(sys.stdin)))'

# 7) Student is forbidden.
ALICE=$(curl -s -X POST $BACKEND/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"alice.student@english.app","password":"password123"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -o /dev/null -w '%{http_code}\n' $BACKEND/api/submissions/inbox/ \
  -H "Authorization: Bearer $ALICE"   # -> 403
```

Shell check that the inbox truly includes assignment-null submissions (regression guard against the "join only through Assignment" bug):
```python
# python manage.py shell
from core.models import Submission, User
from django.db.models import Q
emma = User.objects.get(email="emma.teacher@english.app")
qs = Submission.objects.filter(
    Q(exercise__created_by=emma) | Q(exercise__module__created_by=emma)
).distinct()
print("total in Emma's inbox:", qs.count())
print("self-practice (assignment is null) in inbox:",
      qs.filter(assignment__isnull=True).count())   # must be > 0 given seed data
```

## Deferred / out of scope
- **AI grading itself** (OpenAI Whisper / LLM rubric grading, the `is_ai_generated=True` write path, `ai_models` config) → **file 06**. This file only *consumes* the flag and provides the `AI_Graded` branch point.
- **Field/enum/migration definitions** for `Submission.status`, `SubmissionStatus`, `Feedback.is_ai_generated`, `Exercise.created_by`, and the `FeedbackSerializer` score bound + `is_ai_generated` field → **file 01**.
- **Feedback-read ownership guard** on `SubmissionViewSet.feedback` (students/teachers reading arbitrary submissions' feedback) → **file 02**.
- **Pagination/sorting/search** on the inbox — list endpoints currently return bare arrays (pagination disabled); inbox follows suit. If the grading queue grows large, add cursor pagination later (a global change, out of this file's additive scope).
- **"Next ungraded auto-loads"** UX (UI_FLOW_BRIEF J4) is a frontend concern; the `?status=Pending` filter + `-submitted_at` ordering give the frontend everything it needs to pull the next item.
- **Editing/revoking an evaluation** (re-grade, delete feedback → revert status to `Pending`) — `FeedbackViewSet` is create-only (`CreateModelMixin`). Re-grade semantics and status-revert-on-delete are a future enhancement; today a second `POST /api/feedback/` simply adds another row and `_latest_feedback` wins.
- **CSV/PDF grade export** (UC-16 / RBAC row 16) — separate reporting concern, not part of the grading lifecycle.

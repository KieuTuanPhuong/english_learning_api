# 05 — Dashboards & analytical report export (CSV/PDF)
**Status:** Proposed · **Depends on:** 01 (Submission `status` field aids ungraded detection) ; 04 (reuse the teacher ungraded/inbox queryset) · **Spec refs:** docs.md §2 RBAC rows 4 (Student Dashboard), 5 (Teacher Dashboard), 16 (Export Analytical Progress Reports) ; §3.2 UC-05 ; §3.3 UC-11, UC-16 ; REFACTOR_PLAN.md §3.5 (aggregate read-only endpoints, no model)
**Estimated blast radius:** `core/views.py` (two new APIViews + a small module-level helper), `core/serializers.py` (no change — inline response serializers stay in views per repo idiom), `config/urls.py` (two new non-router paths), `config/settings.py` (two new SPECTACULAR tags). NO model, NO migration, NO admin, NO seed change.

## Goal
Add **role-aware dashboards** and **analytical report export** as pure read-only aggregates over existing tables — no new model, mirroring REFACTOR_PLAN.md §3.5. `GET /api/dashboard/` returns a different shape per role (student / teacher / admin), satisfying RBAC rows 4 & 5 and UC-05 / UC-11. `GET /api/reports/grades?class_id=…` streams a class grade report as CSV (UC-16, RBAC row 16), with a deferred-but-hooked PDF path. This closes the "dashboards / reporting" gap: the API currently exposes only per-resource list endpoints, so a frontend must fan out many calls and aggregate client-side.

## Why (gap)
Today there is **no aggregate endpoint at all**. To build a dashboard the Next.js client must stitch together:
- `GET /api/classes/` (filtered per role in `ClassViewSet.get_queryset`, `core/views.py:170`),
- `GET /api/classes/{id}/assignments/` (`ClassViewSet.assignments`, `core/views.py:290`),
- `GET /api/progress/me` (`ProgressViewSet.me`, `core/views.py:481`),
- `GET /api/submissions/me` (`SubmissionViewSet.me`, `core/views.py:414`),
- `GET /api/exercises/{id}/submissions` (`ExerciseViewSet.submissions`, `core/views.py:378`),
- `GET /api/submissions/{id}/feedback` (`SubmissionViewSet.feedback`, `core/views.py:423`).

docs.md requires server-side aggregation: UC-05 ("aggregate completion percentages, pending homework deadline ledgers, recent grading remarks"), UC-11 ("pending grading queues across active class cohorts"), and an admin view (§3.4). UC-16 requires a downloadable `.csv` / `.pdf` grade report — there is **no export endpoint** and `csv`/`reportlab` are not used anywhere.

The "ungraded" signal: a submission is **ungraded iff it has no `Feedback` row** (`Feedback` FK `submission`, `related_name="feedback"`, `core/models.py:338-339`). File 01 adds `Submission.status` (`SubmissionStatus` TextChoices) which makes this explicit; **this file treats `status` as an optional accelerator and still derives the truth from `Feedback` existence**, so it works whether or not 01 has landed yet. The canonical teacher "ungraded inbox" scope is owned by **file 04** (REFACTOR_PLAN.md §3.4: submissions across a teacher's owned exercises, *including self-practice `assignment IS NULL`*). Note that **file 04 ships the inbox as a DRF action (`SubmissionViewSet.inbox`), not as a reusable selector function**, and there is **no `core/selectors.py`** in this repo. So this file does **not** import a helper from 04; instead it defines its own small queryset helper here that mirrors file 04's exact ownership scope — `Q(exercise__created_by=teacher) | Q(exercise__module__created_by=teacher)` — keeping the two in lock-step by convention. The dashboard derives "ungraded" via `feedback__isnull=True` (absence of a `Feedback` row), which is the same truth file 04's `is_graded` flag exposes.

> ⚠️ Contract note: both endpoints are **additive** — new URLs, no router registration, no change to any existing path, field, or the login response shape. No frontend contract is touched. List-style arrays inside the dashboard payload follow the repo's "bare arrays" convention (the top-level dashboard object is a bespoke aggregate, not a paginated list).

## Changes (file by file)

### core/views.py
Add two `APIView`s at the end of the file plus one module-level helper. They reuse the existing `_perms(...)` stack, `IsTeacherOrAdmin`, `UserRole`, and the model imports already present at `core/views.py:30-43`.

**1. Imports to add** (top of file, alongside existing imports — `timezone` is already imported at `core/views.py:8`):

```python
import csv

from django.db.models import Count, Q
from django.http import HttpResponse
```

> If **file 04 has already landed**, `from django.db.models import Q` is present (file 04 adds it for its inbox queryset) — merge, don't duplicate the import line. `Count` and `csv`/`HttpResponse` are new here either way.

**2. Teacher ungraded helper — define here, mirroring file 04's scope.**
File 04 does **not** export a reusable function — its inbox is the action `SubmissionViewSet.inbox`, which serializes a `Response` and is not importable as a queryset. There is **no `core/selectors.py`** in this repo, so do **not** write `from .selectors import …`. Instead define this small module-level helper in `core/views.py` (next to `_perms`). Keep its ownership filter **byte-identical** to file 04's inbox queryset so the dashboard count and the inbox never disagree.

```python
def _ungraded_for_teacher(teacher):
    """Submissions on the teacher's OWN exercises that have NO Feedback yet.

    Ownership scope is identical to file 04's SubmissionViewSet.inbox:
    primary = Exercise.created_by (NEW field, file 01); legacy fallback =
    module.created_by (existing, core/models.py:164) for seed rows whose
    Exercise.created_by is still null. Self-practice (assignment IS NULL) is
    included because we scope by exercise ownership, not by Assignment.klass.

    "Ungraded" = absence of a Feedback row (feedback__isnull=True). This is the
    same truth file 04's `is_graded` flag exposes; Submission.status (file 01)
    is a denormalised accelerator and is intentionally NOT relied on here, so
    this works whether or not 01 has landed.
    """
    return (
        Submission.objects
        .filter(
            Q(exercise__created_by=teacher)
            | Q(exercise__module__created_by=teacher)
        )
        .filter(feedback__isnull=True)
        .distinct()   # Q-OR across exercise + module FKs can duplicate rows
    )
```

> WHY both legs of the `Q(...)`: against the seeded dataset every exercise has `Exercise.created_by = NULL` (it is a NEW field added by file 01 with no backfill yet), so the `exercise__created_by=teacher` leg alone returns **zero** rows and `ungraded_submission_count` would be 0 — failing the acceptance test below. The `exercise__module__created_by=teacher` leg is what actually matches against seed data (e.g. Emma owns modules 1 & 2 via `LearningModule.created_by`). Keep both legs permanently; once file 01 backfills `created_by` the first leg also matches, and the `.distinct()` collapses any duplicates. This mirrors file 04's "CRITICAL: scope by exercise ownership, NOT by `Assignment.klass`" note exactly.

**3. Dashboard view** — one role-aware endpoint (`GET /api/dashboard/`). Permission = any active authenticated user (`_perms()`), content branches on `request.user.role`.

```python
# ============================================================ Dashboard
@extend_schema(
    tags=["dashboard"],
    summary="Role-aware dashboard aggregate (student / teacher / admin)",
    description=(
        "Returns a different payload shape per role. Student: due/upcoming "
        "assignments, in-progress modules, recent feedback. Teacher: class & "
        "student counts, ungraded-submission count, recent activity. Admin: "
        "user counts by role and platform totals + recent rows."
    ),
    responses={200: inline_serializer(
        name="Dashboard",
        fields={
            "role": drf_serializers.CharField(),
            "generated_at": drf_serializers.DateTimeField(),
            "data": drf_serializers.DictField(),
        },
    )},
)
class DashboardView(APIView):
    # IMPORTANT: assign the permission *classes* (not instances) at class level.
    # `_perms()` returns instantiated permissions and is only valid as the return
    # of get_permissions(); putting it here would make DRF try to call already-
    # instantiated objects and raise TypeError. `_BASE` is [IsAuthenticated,
    # IsActiveUser] (core/views.py:47) — exactly the dashboard's "any active user".
    permission_classes = _BASE

    def get(self, request):
        user = request.user
        if user.role == UserRole.STUDENT:
            data = self._student(user)
        elif user.role == UserRole.TEACHER:
            data = self._teacher(user)
        else:  # admin
            data = self._admin()
        return Response({
            "role": user.role,
            "generated_at": timezone.now(),
            "data": data,
        })

    # ---- Student (UC-05, RBAC row 4) ----
    def _student(self, user):
        now = timezone.now()
        # Assignments on classes the student is enrolled in (Assignment.klass),
        # not yet past due, soonest first. Enrollment via ClassStudent
        # (related_name "students" on Class -> ClassStudent.student).
        due = (
            Assignment.objects
            .filter(klass__students__student=user, due_date__gte=now)
            .order_by("due_date")[:10]
        )
        in_progress = (
            StudentModuleProgress.objects
            .filter(student=user, completion_percentage__lt=100)
            .order_by("-last_accessed_at")[:10]
        )
        recent_feedback = (
            Feedback.objects
            .filter(submission__student=user)
            .order_by("-created_at")[:5]
        )
        return {
            "due_assignments": s.AssignmentSerializer(due, many=True).data,
            "in_progress_modules": s.ProgressSerializer(in_progress, many=True).data,
            "recent_feedback": s.FeedbackSerializer(recent_feedback, many=True).data,
        }

    # ---- Teacher (UC-11, RBAC row 5) ----
    def _teacher(self, user):
        own_classes = Class.objects.filter(teacher=user)          # Class.teacher
        enrolled = ClassStudent.objects.filter(klass__teacher=user)
        ungraded_qs = _ungraded_for_teacher(user)
        recent = ungraded_qs.order_by("-submitted_at")[:10]
        return {
            "class_count": own_classes.count(),
            "enrolled_student_count": enrolled.values("student").distinct().count(),
            "ungraded_submission_count": ungraded_qs.count(),
            "classes": s.ClassSerializer(own_classes.order_by("id"), many=True).data,
            "recent_ungraded": s.SubmissionSerializer(recent, many=True).data,
        }

    # ---- Admin (§3.4) ----
    def _admin(self):
        by_role = {
            row["role"]: row["n"]
            for row in User.objects.values("role").annotate(n=Count("id"))
        }
        return {
            "user_counts_by_role": {
                UserRole.ADMIN: by_role.get(UserRole.ADMIN, 0),
                UserRole.TEACHER: by_role.get(UserRole.TEACHER, 0),
                UserRole.STUDENT: by_role.get(UserRole.STUDENT, 0),
            },
            "totals": {
                "classes": Class.objects.count(),
                "modules": LearningModule.objects.count(),
                "exercises": Exercise.objects.count(),
                "submissions": Submission.objects.count(),
                "feedback": Feedback.objects.count(),
            },
            "recent_users": s.UserSerializer(
                User.objects.order_by("-created_at")[:5], many=True
            ).data,
            "recent_submissions": s.SubmissionSerializer(
                Submission.objects.order_by("-submitted_at")[:5], many=True
            ).data,
            "recent_feedback": s.FeedbackSerializer(
                Feedback.objects.order_by("-created_at")[:5], many=True
            ).data,
        }
```

Notes grounding each query:
- `Assignment.klass` is the FK (`core/models.py:255`); the student-enrollment path `klass__students__student=user` uses `Class.students` (`related_name`, `core/models.py:125`) → `ClassStudent.student`.
- `StudentModuleProgress.completion_percentage` / `last_accessed_at` exist (`core/models.py:184-187`).
- `Feedback.created_at` (`core/models.py:349`), `Submission.submitted_at` (`core/models.py:297`), `User.created_at` (`core/models.py:90`) are the existing timestamp columns ordered on — no new columns needed.
- Serializers reused as-is: `AssignmentSerializer`, `ProgressSerializer`, `FeedbackSerializer`, `ClassSerializer`, `SubmissionSerializer`, `UserSerializer` (all in `core/serializers.py`). They already emit the `*_id` wire aliases, so the dashboard payload is consistent with the rest of the API.

**4. Report export view** — `GET /api/reports/grades?class_id=…`, CSV MVP with a deferred PDF hook. Permission = `IsTeacherOrAdmin`, with teacher class-ownership scoping enforced in the handler (mirrors `ClassViewSet._require_class_owner`, `core/views.py:209`, and the ownership check in `ClassViewSet.update`, `core/views.py:196`).

```python
# ============================================================ Reports
@extend_schema(
    tags=["reports"],
    summary="Export a class grade report (CSV; PDF deferred)",
    parameters=[
        OpenApiParameter(
            "class_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True,
            description="Class to report on. Teachers may only export their own.",
        ),
        OpenApiParameter(
            "format", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            enum=["csv", "pdf"], default="csv",
            description="csv (default, implemented) or pdf (deferred — 501 unless reportlab hook enabled).",
        ),
    ],
    responses={
        (200, "text/csv"): OpenApiTypes.BINARY,
        403: OpenApiResponse(description="Not your class"),
        404: OpenApiResponse(description="Class not found"),
        501: OpenApiResponse(description="PDF export not enabled"),
    },
)
class GradeReportView(APIView):
    # Class-level = permission *classes*; equivalent to _perms(IsTeacherOrAdmin)
    # but without the premature instantiation _perms() does (see DashboardView).
    permission_classes = [*_BASE, IsTeacherOrAdmin]

    def get(self, request):
        class_id = request.query_params.get("class_id")
        if not class_id:
            raise ValidationError({"class_id": "Required"})
        cls = Class.objects.filter(pk=class_id).first()
        if not cls:
            raise NotFound("Class not found")
        # Teacher scoping: own classes only; admin: any. (Class.teacher_id)
        if request.user.role == UserRole.TEACHER and cls.teacher_id != request.user.id:
            raise PermissionDenied("Not your class")

        rows = self._rows(cls)
        fmt = request.query_params.get("format", "csv").lower()
        if fmt == "pdf":
            return self._pdf(cls, rows)   # deferred hook (see below)
        return self._csv(cls, rows)

    # --- Aggregate: one row per submission for an assignment in this class ---
    def _rows(self, cls):
        # Submissions whose assignment belongs to this class. Latest feedback
        # score per submission = the "grade"; auto_score covers receptive items.
        subs = (
            Submission.objects
            .filter(assignment__klass=cls)
            .select_related("student", "exercise")
            .prefetch_related("feedback")
            .order_by("student__full_name", "submitted_at")
        )
        out = []
        for sub in subs:
            fb = sub.feedback.order_by("-created_at").first()
            out.append({
                "student_id": sub.student_id,
                "student_name": sub.student.full_name,
                "student_email": sub.student.email,
                "exercise": sub.exercise.title,
                "submission_type": sub.submission_type,
                "auto_score": sub.auto_score if sub.auto_score is not None else "",
                "feedback_score": fb.score if fb and fb.score is not None else "",
                "graded": "yes" if fb else "no",
                "is_ai_generated": (
                    # Feedback.is_ai_generated added in file 06; default False if absent.
                    getattr(fb, "is_ai_generated", False) if fb else ""
                ),
                "submitted_at": sub.submitted_at.isoformat(),
            })
        return out

    _COLUMNS = [
        "student_id", "student_name", "student_email", "exercise",
        "submission_type", "auto_score", "feedback_score", "graded",
        "is_ai_generated", "submitted_at",
    ]

    def _csv(self, cls, rows):
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = (
            f'attachment; filename="grades_class_{cls.pk}.csv"'
        )
        writer = csv.DictWriter(resp, fieldnames=self._COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return resp

    def _pdf(self, cls, rows):
        """DEFERRED. CSV is the MVP deliverable (docs.md UC-16 lists '.csv or .pdf').
        To enable: pip install reportlab, set REPORTS_PDF_ENABLED=true, and
        implement a reportlab table here returning content_type='application/pdf'.
        Until then return 501 so the contract advertises the capability without a
        hard dependency (mirrors the repo 'mock, no external service' stance)."""
        from django.conf import settings as dj_settings
        if not getattr(dj_settings, "REPORTS_PDF_ENABLED", False):
            return Response(
                {"detail": "PDF export not enabled; use format=csv."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        raise NotImplementedError  # implement reportlab table when enabling
```

Grounding:
- `Submission.assignment` → `Assignment.klass` (`core/models.py:278, 255`) ties submissions to a class; self-practice (`assignment IS NULL`) is intentionally excluded from a *class* report.
- `Submission.feedback` reverse accessor (`related_name="feedback"`, `core/models.py:339`); `Feedback.score` / `created_at` (`core/models.py:345, 349`).
- `Feedback.is_ai_generated` is added by **file 06**; this report reads it via `getattr(..., False)` so it works before/after 06.
- `cls.teacher_id` ownership check matches `ClassViewSet.update` (`core/views.py:196`) and `_require_class_owner` (`core/views.py:209`).
- `auto_score` (`core/models.py:294`) included so receptive (reading/listening/quiz) grades appear even without `Feedback`.
- `ValidationError`, `NotFound`, `PermissionDenied`, `OpenApiParameter`, `OpenApiResponse`, `OpenApiTypes`, `inline_serializer`, `drf_serializers`, `status`, `Response`, `APIView`, `extend_schema` are all already imported at `core/views.py:9-26`.

### core/serializers.py
**No new ModelSerializer required.** The dashboard reuses existing serializers; its envelope is bespoke and declared inline via `inline_serializer(name="Dashboard", …)` in the view (repo idiom — see the existing `inline_serializer(name="TokenPair", …)` at `core/views.py:79`). The CSV report bypasses serializers entirely (stdlib `csv` → `HttpResponse`). Leave `core/serializers.py` untouched.

### config/urls.py
Register the two endpoints as **plain `path()`s** (not router routes — they are singletons/non-CRUD). `from core import views` is already imported at `config/urls.py:18`. Add to `urlpatterns`, before the router include so they take precedence under `/api/`:

```python
urlpatterns = [
    path("", root),
    path("admin/", admin.site.urls),
    path("api/auth/", include(auth_patterns)),
    path("api/dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("api/reports/grades", views.GradeReportView.as_view(), name="report-grades"),
    path("api/", include(docs_patterns)),
    path("api/", include(router.urls)),
]
```

> Note: `reports/grades` has no trailing slash to keep query-string usage clean (`?class_id=`); the dashboard keeps the trailing slash to match the router-style siblings. Both are stable additions and do not collide with any registered router prefix (`users/classes/modules/exercises/submissions/feedback/progress`, `config/urls.py:21-27`).

### config/settings.py
Add two tags to `SPECTACULAR_SETTINGS["TAGS"]` (the list at `config/settings.py:133-142`) so the new endpoints group correctly in `/api/docs/`:

```python
    "TAGS": [
        # ... existing entries ...
        {"name": "progress", "description": "Per-student module progress"},
        {"name": "dashboard", "description": "Role-aware dashboard aggregates (student/teacher/admin)"},
        {"name": "reports", "description": "Analytical grade-report export (CSV; PDF deferred)"},
    ],
```

Optional env flag for the deferred PDF path (only when PDF is enabled — add near the bottom of settings; `os` is already imported at `config/settings.py:8`):

```python
# Reports — PDF export is deferred behind reportlab; CSV is always available.
REPORTS_PDF_ENABLED = os.getenv("REPORTS_PDF_ENABLED", "false").lower() == "true"
```

### core/admin.py
**No change.** These are aggregate read-only endpoints with no backing model, so there is nothing to register.

### core/management/commands/seed_demo.py
**No change required.** The existing seed already produces the data the dashboards aggregate over: enrolled students with `StudentModuleProgress` (`seed_demo.py:329-346`), due `Assignment`s (all `due_date=now + timedelta(days=…)`, so all "upcoming", `seed_demo.py:222-235`), graded **and ungraded** submissions (feedback is created for only a subset of `submissions[...]` indices at `seed_demo.py:296-325`, leaving several submissions with no `Feedback` row → the teacher dashboard's `ungraded_submission_count` is non-zero), and recent `Feedback`. Smoke-test against the seeded dataset directly.

## Migrations
**None.** This feature adds no model and no field — it is pure read aggregation over existing columns (REFACTOR_PLAN.md §3.5: "no model"). Do **not** run `makemigrations`. (The dependency fields — `Exercise.created_by`, `Submission.status`, `Feedback.is_ai_generated` — are migrated by files 01 and 06; this file only *reads* them defensively via `getattr`/selector indirection so it neither requires nor blocks those migrations.)

## RBAC / permissions
| Endpoint | Roles allowed | Permission classes | Scoping |
|---|---|---|---|
| `GET /api/dashboard/` | any active user (Student / Teacher / Admin) | `permission_classes = _BASE` → `[IsAuthenticated, IsActiveUser]` (classes) | content branches on `request.user.role`; each branch self-scopes (student sees own enrollments/progress/feedback; teacher sees own classes/exercises) |
| `GET /api/reports/grades` | Teacher, Admin | `permission_classes = [*_BASE, IsTeacherOrAdmin]` (classes) | teacher → `cls.teacher_id == request.user.id` enforced in handler (403 otherwise); admin → any class |

- Reuses existing `IsActiveUser`, `IsTeacherOrAdmin`, and the `_BASE` permission-class list — **no new permission class**. Note: `_perms()` (which *instantiates* permissions) is for `get_permissions()` return values, not class-level `permission_classes`; these APIViews assign the permission **classes** directly via `_BASE` (see the view code). Suspended/inactive users are already rejected by `IsActiveUser` (`core/permissions.py:9`); do not re-implement.
- RBAC matrix alignment: row 4 (Student Dashboard: Admin+Student) and row 5 (Teacher Dashboard: Admin+Teacher) are both satisfied by the single role-branching endpoint — an admin hitting `/api/dashboard/` gets the admin payload (the superset view), consistent with "Admin = X" on every dashboard row. Row 16 (Export Reports: Admin+Teacher) maps exactly to `GradeReportView`'s `IsTeacherOrAdmin`.

## Acceptance criteria & smoke test
Checklist:
- [ ] `GET /api/dashboard/` as a **student** returns `data.due_assignments` (array), `data.in_progress_modules`, `data.recent_feedback`; no teacher/admin keys.
- [ ] `GET /api/dashboard/` as a **teacher** returns numeric `class_count`, `enrolled_student_count`, `ungraded_submission_count` (> 0 against seed), plus `classes` and `recent_ungraded` arrays.
- [ ] `GET /api/dashboard/` as **admin** returns `user_counts_by_role` (admin/teacher/student), `totals`, and recent rows.
- [ ] `GET /api/dashboard/` as a **suspended** user → 403 (via `IsActiveUser`).
- [ ] `GET /api/reports/grades?class_id=1` as the **owning teacher** or **admin** → `200`, `Content-Type: text/csv`, `Content-Disposition: attachment`, header row + data rows.
- [ ] Same URL as a **non-owning teacher** → 403; as a **student** → 403 (`IsTeacherOrAdmin`).
- [ ] `…&format=pdf` → 501 (until `REPORTS_PDF_ENABLED`).
- [ ] No new migration is created (`python manage.py makemigrations core` reports "No changes detected").
- [ ] `/api/schema/` validates and shows `dashboard` + `reports` tags (`python manage.py spectacular --validate --file /dev/null`).

Reseed + run:

```bash
cd /Users/macbook/USTH/english-learning-api
./venv/bin/python manage.py seed_demo
./venv/bin/python manage.py runserver 8000 &
```

Token helper (login response is `{access_token, refresh_token, token_type:"bearer"}` — unchanged):

```bash
login () { curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"password123\"}" | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }

STU=$(login alice.student@english.app)
TEA=$(login emma.teacher@english.app)
ADM=$(login admin@english.app)
SUS=$(login luna.student@english.app)   # seeded as SUSPENDED (seed_demo.py:94)
```

Student dashboard (UC-05):
```bash
curl -s localhost:8000/api/dashboard/ -H "Authorization: Bearer $STU" | python -m json.tool
# expect: "role":"student", data.due_assignments[], data.in_progress_modules[], data.recent_feedback[]
```

Teacher dashboard (UC-11) — Emma owns classes 1 & 2 (`seed_demo.py:103-106`) and has ungraded submissions:
```bash
curl -s localhost:8000/api/dashboard/ -H "Authorization: Bearer $TEA" | python -m json.tool
# expect: class_count>=2, enrolled_student_count>0, ungraded_submission_count>0
```

Admin dashboard:
```bash
curl -s localhost:8000/api/dashboard/ -H "Authorization: Bearer $ADM" | python -m json.tool
# expect: user_counts_by_role {admin:1, teacher:3, student:12}, totals{...}
```

Suspended → 403:
```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/dashboard/ -H "Authorization: Bearer $SUS"
# expect: 403
```

CSV export (UC-16) — owning teacher:
```bash
curl -s -D - "localhost:8000/api/reports/grades?class_id=1" -H "Authorization: Bearer $TEA" -o grades.csv | grep -i 'content-type\|content-disposition'
head grades.csv
# expect headers: text/csv ; attachment; filename="grades_class_1.csv" ; CSV header row present
```

Forbidden (David does not own class 1 — class 1 is Emma's, `seed_demo.py:103`):
```bash
DAV=$(login david.teacher@english.app)
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/api/reports/grades?class_id=1" -H "Authorization: Bearer $DAV"   # expect 403
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/api/reports/grades?class_id=1" -H "Authorization: Bearer $STU"   # expect 403 (student)
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/api/reports/grades?class_id=1&format=pdf" -H "Authorization: Bearer $ADM"  # expect 501
```

Admin can export any class:
```bash
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/api/reports/grades?class_id=4" -H "Authorization: Bearer $ADM"  # expect 200
```

ORM sanity (manage.py shell), proving the ungraded count derives from Feedback existence and matches `_ungraded_for_teacher`'s exact scope:
```bash
./venv/bin/python manage.py shell -c "
from core.models import User, Submission
from django.db.models import Q
emma = User.objects.get(email='emma.teacher@english.app')
qs = (Submission.objects
      .filter(Q(exercise__created_by=emma) | Q(exercise__module__created_by=emma))
      .filter(feedback__isnull=True)
      .distinct())
print('ungraded for Emma:', qs.count())
"
```
(Note: `exercise__created_by` matches nothing until file 01 backfills it; the `exercise__module__created_by` leg is what hits the seed rows — hence both legs in the helper.)

## Deferred / out of scope
- **PDF export** — only the CSV path is implemented. The `format=pdf` branch returns 501 behind `REPORTS_PDF_ENABLED`; growing it means `pip install reportlab`, adding it to `requirements.txt`, and rendering a table in `GradeReportView._pdf` (the hook is already in place). docs.md UC-16 says "`.csv` or `.pdf`" — CSV satisfies the MVP.
- **The canonical teacher inbox endpoint** (`GET /api/submissions/inbox/`, the action `SubmissionViewSet.inbox`) is owned by **file 04**. This file does not re-implement that endpoint; it only computes an *ungraded count + recent slice* for the dashboard via the local `_ungraded_for_teacher` helper, whose ownership filter is deliberately byte-identical to file 04's inbox queryset. If file 04 ever changes that ownership scope, update `_ungraded_for_teacher` to match (they are kept in sync by convention, not by a shared import — there is no `core/selectors.py`).
- **Dashboard write/refresh, caching, pagination, date-range filters** — none added; payloads are capped with `[:N]` slices and computed on each request (acceptable at MVP scale; promote to cached/materialised aggregates later, and to a real `ActivityLog` table per REFACTOR_PLAN.md §7 if "recent activity" needs history).
- **Per-class analytics charts / distributions** beyond the flat grade CSV (mean/median/band breakdowns) — out of scope; the CSV gives the frontend the raw rows to chart client-side.
- **`Submission.status` / `Feedback.is_ai_generated` denormalisation** — read defensively here but defined and migrated by files 01 and 06; do not add them in this file.

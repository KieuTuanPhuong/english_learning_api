# 07 — Admin operations & audit — SystemLog, activity monitoring, user management

**Status:** Proposed · **Depends on:** 01 (migration mechanics, seed conventions, conventions), 02 (admin-only `status` write), 04 (Submission `status` field) · **Spec refs:** docs.md §2 RBAC rows 14 ("Manage User Directory & Permissions") & 17 ("Monitor System Health & Performance") ; docs.md §3.4 UC-19 (Manage Users), UC-20/21 (Infrastructure & AI Configuration) ; docs.md §4 DBML `Table system_logs` ; docs.md §5 "Table 12: system_logs"
**Estimated blast radius:** `core/models.py`, `core/serializers.py`, `core/views.py`, `config/urls.py`, `config/settings.py`, `core/admin.py`, `core/management/commands/seed_demo.py`, plus one new migration (`core/migrations/0007_systemlog.py` — the exact number depends on what 01/02/03/04/05/06 generate first; see Migrations).

## Goal
Give Admins governance and observability over the platform: a NEW `SystemLog` audit table that records administrative actions (especially user status changes), a read-only admin **activity feed** (`GET /api/admin/activity/`), and a **system health** probe (`GET /api/admin/health/`). This implements docs.md's `system_logs` table (§4 DBML, §5 Table 12), satisfies RBAC row 14 (manage user directory + permissions, with traceability) and row 17 (monitor system health), and realizes UC-19 (searchable user directory + status mutations that are audited) and UC-20/21 (infrastructure monitoring). User management itself already exists on `UserViewSet`; this file makes status changes **auditable** and adds **search/filter**, plus the two admin-only read endpoints.

## Why (gap)
Today the repo has **no** audit trail, **no** health endpoint, and **no** way to see cross-entity admin activity. Grep confirms `SystemLog` / `system_logs` / `/api/admin/` appear nowhere under `core/` or `config/`.

What exists today vs what docs.md requires:

| docs.md requirement | Current code | This file adds |
|---|---|---|
| `system_logs` table (§4 DBML, §5 Table 12) — audit admin actions + status changes | (nothing) | NEW `SystemLog` model, `db_table = "system_logs"` |
| UC-19 — admin status mutations (suspend/activate) must be auditable ("permission auditing") | `UserViewSet.update` / `.me` change `status` (admin-gated; row 14) but write **no audit record** | a minimal additive hook in `UserViewSet` writes a `SystemLog` row whenever an admin changes a user's `status` |
| UC-19 — directory "searchable by profile attributes" | `UserViewSet` lists all users unfiltered; admin uses `/admin/` search instead | `?role=`, `?status=`, `?search=` query params on `GET /api/users/` |
| RBAC row 17 / UC-20/21 — monitor server CPU/Memory, DB query latency | (nothing) | `GET /api/admin/health/` — DB connectivity + latency timing + entity counts (stdlib only; live CPU/mem deferred — see below) |
| §5 Table 12 rationale — "absolute governance and traceability over … permission changes" | (nothing) | `GET /api/admin/activity/` — recent `SystemLog` rows merged with recent cross-entity rows (users/submissions/feedback) |

The repo idioms this file matches are already established: FKs surfaced as `*_id` via `PrimaryKeyRelatedField(source=...)` (`core/serializers.py` `ClassSerializer.teacher_id`, `FeedbackSerializer.reviewer_id`); the shared permission helper `_perms(*extra)` (`core/views.py:50`); explicit `db_table` on every model; `IsAdmin` from `core/permissions.py`; admin status-write already gated in `UserViewSet.me` (`core/views.py:137` — `data.pop("status")` for non-admins) and admin-only `update`/`partial_update`/`destroy`/`list` (`core/views.py:120-122`). **File 02 is responsible for ensuring non-admins cannot set `status`; this file relies on that and only adds the audit side-effect.**

### Why a model field, not a Django-admin LogEntry
Django's built-in `admin.LogEntry` only records actions taken through `/admin/`, not through the DRF API. docs.md's `system_logs` is an app-level audit ledger keyed to API-driven status changes, so it must be its own model written from `UserViewSet`. This is additive — the Django `/admin/` `LogEntry` keeps working untouched.

## Changes (file by file)

### core/models.py
Add a new `SystemLog` model after `Feedback` (the last model; if 03/06 also append models, ordering between the new models does not matter). Fields map 1:1 to docs.md §4 / §5 Table 12: `admin` (nullable FK to `User`, `SET_NULL`, `related_name="system_logs"` exactly as SCOPE requires — survives admin deletion so the audit trail is preserved), `action_description` (`TextField`, the docs.md `action_description text [not null]`), `target_status` (nullable `CharField(max_length=50)` matching `varchar(50)` — the new status a target user was set to, e.g. `"suspended"`), `timestamp` (`auto_now_add=True`).

This is an **append-only audit log** — no `updated_at`, and admin code only ever creates rows.

```python
# ---------- System Log (admin audit) ----------
class SystemLog(models.Model):
    """Administrative audit record (docs.md `system_logs`, §5 Table 12).

    Append-only ledger of admin actions — primarily user status changes
    (suspend/activate) and other governance events. ``admin`` is the actor
    (nullable + SET_NULL so the trail survives if that admin is deleted);
    ``target_status`` records the new status applied to a target user (e.g.
    "suspended"), null for non-status actions. The rationale (docs.md §5
    Table 12) is traceability over permission/status changes.
    """

    admin = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="system_logs",
    )
    action_description = models.TextField()
    target_status = models.CharField(max_length=50, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "system_logs"
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        return f"SystemLog {self.pk}: {self.action_description[:50]}"
```

Notes:
- `User` is defined far above `Feedback` in this file, so the FK reference resolves directly (no string reference needed).
- `ordering = ["-timestamp", "-id"]` gives newest-first; list/activity endpoints still return bare arrays (pagination is off globally), so this only affects order, not shape.
- `target_status` is a free `CharField` (not a `choices=` enum) on purpose: it mirrors whatever `UserStatus` value was applied without coupling the audit table to that enum, and it can also hold `null` for non-status admin actions logged later.

### core/serializers.py
Add `SystemLog` to the `from .models import (...)` block, then add `SystemLogSerializer` and two small read-only serializers used to compose the activity feed and health payload. `admin_id` is read-only (server-stamped at write time, mirroring `FeedbackSerializer.reviewer_id`). The activity-feed serializer is a plain `serializers.Serializer` (not model-bound) because the feed is a heterogeneous, normalized list assembled in the view.

```python
# (add SystemLog to the existing models import; the others are already imported)
from .models import (
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    Question,
    QuestionOption,
    StudentModuleProgress,
    Submission,
    SystemLog,   # NEW
    User,
    UserRole,
)
```

```python
# ---------- System Log / Admin activity ----------
class SystemLogSerializer(serializers.ModelSerializer):
    """Audit record. ``admin_id`` is set server-side (the acting admin) and is
    read-only, mirroring ``FeedbackSerializer.reviewer_id``."""

    admin_id = serializers.PrimaryKeyRelatedField(source="admin", read_only=True)

    class Meta:
        model = SystemLog
        fields = ["id", "admin_id", "action_description", "target_status", "timestamp"]
        read_only_fields = fields


class ActivityItemSerializer(serializers.Serializer):
    """One normalized row in the admin activity feed (read-only).

    Heterogeneous source rows (system logs, new users, new submissions, new
    feedback) are mapped to a common shape in the view, so this is a plain
    Serializer, not a ModelSerializer."""

    type = serializers.CharField()        # "system_log" | "user" | "submission" | "feedback"
    id = serializers.IntegerField()
    summary = serializers.CharField()
    timestamp = serializers.DateTimeField()


class HealthSerializer(serializers.Serializer):
    """System health snapshot (read-only) — see AdminHealthView."""

    status = serializers.CharField()                  # "ok" | "degraded"
    database = serializers.CharField()                # "ok" | "error"
    db_latency_ms = serializers.FloatField()
    counts = serializers.DictField(child=serializers.IntegerField())
    server_time = serializers.DateTimeField()
```

### core/views.py
Three changes, in order of importance.

**(1) The audit hook in `UserViewSet` — the only place app code outside this feature is touched.** `UserViewSet` already exists (`core/views.py:116`) and already gates `status` writes to admins (the `update`/`partial_update` path is admin-only via `get_permissions`; the `me` path strips `status` for non-admins at `core/views.py:137`). We add a tiny module-level helper and call it from both write paths **only when an admin changes a target user's `status`**. Keep it minimal and additive — no behavior changes, just a side-effect write.

Add `SystemLog` to the models import and add the helper:

```python
# (add SystemLog to the existing models import)
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
    SubmissionType,
    SystemLog,   # NEW
    User,
    UserRole,
)
```

```python
def _log_status_change(admin, target, new_status):
    """Write a SystemLog audit row for an admin-driven user status change.
    Only called from admin-gated write paths in UserViewSet."""
    SystemLog.objects.create(
        admin=admin,
        action_description=(
            f"Set status of user {target.id} ({target.email}) to '{new_status}'"
        ),
        target_status=new_status,
    )
```

In `UserViewSet.update` (currently `core/views.py:150-158`), capture the old status before saving and log if it changed:

```python
    def update(self, request, *args, **kwargs):
        # Admin-only edit of an arbitrary user (mirrors admin_update_user).
        obj = self.get_object()
        old_status = obj.status                      # NEW
        ser = s.UserUpdateSerializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        # Audit any admin-driven status change (UC-19; docs.md §5 Table 12).
        if "status" in ser.validated_data and obj.status != old_status:   # NEW
            _log_status_change(request.user, obj, obj.status)             # NEW
        return Response(s.UserSerializer(obj).data)
```

In `UserViewSet.me` (currently `core/views.py:130-142`), the non-admin `status` strip already happens at `data.pop("status")`; add an audit write for the admin case. (An admin editing their *own* status via `me` is unusual but possible; logging it is correct and harmless.)

```python
        ser = s.UserUpdateSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        old_status = request.user.status                                  # NEW
        if "status" in data and request.user.role != UserRole.ADMIN:
            data.pop("status")
        for field, value in data.items():
            setattr(request.user, field, value)
        request.user.save()
        if "status" in data and request.user.status != old_status:       # NEW
            _log_status_change(request.user, request.user, request.user.status)  # NEW
        return Response(s.UserSerializer(request.user).data)
```

> Contract note: `data.pop("status")` already guarantees a non-admin never reaches the second `if`, so only admin-initiated changes are logged. No request/response shape changes — the audit write is a pure side-effect. **This is the only edit to pre-existing view logic; everything else below is new additive code.**

**(2) Add `?role=` / `?status=` / `?search=` filtering to the user directory (UC-19).** `UserViewSet` currently lists every user via the class-level `queryset`. Override `get_queryset` to apply simple query params (no `django-filter` dependency — it is not in `requirements.txt`). Additive: with no params the list is identical to today, so the frontend contract is preserved.

```python
@extend_schema(tags=["users"])
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("id")
    serializer_class = s.UserSerializer

    def get_queryset(self):
        qs = User.objects.all().order_by("id")
        if self.action != "list":
            return qs
        role = self.request.query_params.get("role")
        status_ = self.request.query_params.get("status")
        search = self.request.query_params.get("search")
        if role:
            qs = qs.filter(role=role)
        if status_:
            qs = qs.filter(status=status_)
        if search:
            from django.db.models import Q
            qs = qs.filter(Q(email__icontains=search) | Q(full_name__icontains=search))
        return qs

    # ... existing get_permissions / me / retrieve / update / partial_update unchanged
    # (plus the audit hook from change (1) inside update() and me())
```

Document the params on the list action so they appear in `/api/docs/`:

```python
    @extend_schema(
        summary="List users (admin) — filterable by role/status/search",
        parameters=[
            OpenApiParameter("role", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Filter by role: admin | teacher | student"),
            OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Filter by status: active | suspended | inactive"),
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Case-insensitive match on email or full_name"),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
```

**(3) Two admin-only read views: activity feed + health.** Implemented as `APIView`s (single GET each, not resources), mounted under `/api/admin/`. Both gate on the base stack + `IsAdmin`.

```python
# ============================================================ Admin ops & audit
@extend_schema(tags=["admin"])
class AdminActivityView(APIView):
    """Recent admin activity feed (read-only, Admin only).

    Merges recent SystemLog audit rows with recent cross-entity rows
    (new users, submissions, feedback), normalized to a common shape and
    sorted newest-first. RBAC row 14; docs.md §5 Table 12 (traceability)."""

    permission_classes = _BASE + [IsAdmin]

    @extend_schema(
        summary="Recent admin activity (audit logs + new users/submissions/feedback)",
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY,
                             description="Max rows to return (default 50, max 200)"),
        ],
        responses={200: s.ActivityItemSerializer(many=True)},
    )
    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50

        items = []
        for log in SystemLog.objects.all().order_by("-timestamp")[:limit]:
            items.append({
                "type": "system_log", "id": log.id,
                "summary": log.action_description, "timestamp": log.timestamp,
            })
        for u in User.objects.all().order_by("-created_at")[:limit]:
            items.append({
                "type": "user", "id": u.id,
                "summary": f"User registered: {u.full_name} ({u.role})",
                "timestamp": u.created_at,
            })
        for sub in (
            Submission.objects.select_related("student").order_by("-submitted_at")[:limit]
        ):
            items.append({
                "type": "submission", "id": sub.id,
                "summary": (
                    f"Submission #{sub.id} by user {sub.student_id} "
                    f"({sub.submission_type})"
                ),
                "timestamp": sub.submitted_at,
            })
        for fb in Feedback.objects.order_by("-created_at")[:limit]:
            items.append({
                "type": "feedback", "id": fb.id,
                "summary": f"Feedback #{fb.id} on submission {fb.submission_id}",
                "timestamp": fb.created_at,
            })

        items.sort(key=lambda i: i["timestamp"], reverse=True)
        items = items[:limit]
        return Response(s.ActivityItemSerializer(items, many=True).data)


@extend_schema(tags=["admin"])
class AdminHealthView(APIView):
    """System health probe (read-only, Admin only).

    MVP uses stdlib only: a DB connectivity check with latency timing plus
    entity counts. Live CPU/memory metrics (psutil) are deferred — psutil is
    not in requirements.txt. RBAC row 17; docs.md UC-20/21."""

    permission_classes = _BASE + [IsAdmin]

    @extend_schema(
        summary="System health: DB connectivity, latency, entity counts",
        responses={200: s.HealthSerializer},
    )
    def get(self, request):
        import time
        from django.db import connection

        db_ok = True
        start = time.perf_counter()
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        except Exception:
            db_ok = False
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        counts = {
            "users": User.objects.count(),
            "classes": Class.objects.count(),
            "submissions": Submission.objects.count(),
            "feedback": Feedback.objects.count(),
            "system_logs": SystemLog.objects.count(),
        }
        payload = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "db_latency_ms": latency_ms,
            "counts": counts,
            "server_time": timezone.now(),
        }
        return Response(s.HealthSerializer(payload).data)
```

> Note: `_BASE`, `IsAdmin`, `APIView`, `timezone`, `OpenApiParameter`, `OpenApiTypes`, `extend_schema`, and `Response` are **already imported** at the top of `core/views.py` (lines 7-47). `permission_classes = _BASE + [IsAdmin]` is used here (instead of `get_permissions` returning `_perms(...)`) because these are `APIView`s with a single method — DRF expects a list of permission **classes** on `permission_classes`, and `_BASE` is exactly `[IsAuthenticated, IsActiveUser]` (a list of classes). `_perms(...)` returns *instances*, which is the ViewSet idiom; do not mix the two.

### config/urls.py
The activity + health endpoints are **not** resourceful collections, so register them as plain paths under `/api/admin/` rather than on the `DefaultRouter`. Add to the existing patterns:

```python
admin_ops_patterns = [
    path("activity/", views.AdminActivityView.as_view(), name="admin-activity"),
    path("health/", views.AdminHealthView.as_view(), name="admin-health"),
]

urlpatterns = [
    path("", root),
    path("admin/", admin.site.urls),
    path("api/auth/", include(auth_patterns)),
    path("api/admin/", include(admin_ops_patterns)),   # NEW (before the router include)
    path("api/", include(docs_patterns)),
    path("api/", include(router.urls)),
]
```

> Placement note: `path("api/admin/", include(admin_ops_patterns))` must come **before** `path("api/", include(router.urls))` so `/api/admin/activity/` and `/api/admin/health/` resolve to these views. (The Django `/admin/` site lives at the project root `path("admin/", ...)`, a different prefix, so there is no collision.) `SystemLog` is intentionally **not** given a router resource — there is no public CRUD on audit rows; they are created only by the in-app hook and read via the activity feed and Django `/admin/`.

### config/settings.py
Add an `"admin"` tag to `SPECTACULAR_SETTINGS["TAGS"]` so the new endpoints group correctly in `/api/docs/` and `/api/redoc/`. No `ENUM_NAME_OVERRIDES` change is needed (this feature adds no new TextChoices enum — `target_status` is a free `CharField`).

```python
    "TAGS": [
        {"name": "auth", "description": "Registration, login, JWT tokens"},
        {"name": "users", "description": "User accounts and self-profile"},
        {"name": "classes", "description": "Classes, enrollment, lesson plans, assignments"},
        {"name": "modules", "description": "Learning modules and their exercises"},
        {"name": "exercises", "description": "Exercises and their submissions"},
        {"name": "submissions", "description": "Student submissions and auto-grading"},
        {"name": "feedback", "description": "Reviewer feedback on submissions"},
        {"name": "progress", "description": "Per-student module progress"},
        {"name": "admin", "description": "Admin operations: audit log, activity feed, system health"},  # NEW
    ],
```

> If files 03/06 also add tags (`study-materials`, `ai-models`), keep all of them — the list is additive and order does not matter.

### core/admin.py
Register `SystemLog` in the Django admin (house rule: "NEW models MUST be registered here too"). Make it **read-mostly**: audit rows are written by the app, so disable add/change/delete in `/admin/` to keep the ledger append-only and tamper-resistant, while still letting an admin browse and search it.

```python
# (add SystemLog to the existing models import)
from .models import (
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    Question,
    QuestionOption,
    StudentModuleProgress,
    Submission,
    SystemLog,   # NEW
    User,
)
```

```python
# ---------- System Log (audit; read-only in admin) ----------
@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ("id", "admin", "action_description", "target_status", "timestamp")
    list_filter = ("target_status", "timestamp")
    search_fields = ("action_description", "admin__email")
    raw_id_fields = ("admin",)
    readonly_fields = ("admin", "action_description", "target_status", "timestamp")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # view-only (list/detail still render)

    def has_delete_permission(self, request, obj=None):
        return False
```

### core/management/commands/seed_demo.py
1. Add `SystemLog` to the `from core.models import (...)` block.
2. Add a wipe line in the children-first delete block. `SystemLog` is a leaf (its only FK is `admin` → `User`, `SET_NULL`), so delete it **before** `User.objects.all().delete()`.
3. Seed a few audit rows after Users exist (`admin` + the suspended student are available). The existing seed already suspends `students[11]` (Luna) at `seed_demo.py:94` — record a matching audit row so the demo activity feed and `target_status` filter have data. Add a count line to the summary.

Import:
```python
from core.models import (
    Assignment,
    Class,
    ClassStudent,
    DifficultyLevel,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    LessonPlan,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    SystemLog,   # NEW
    User,
    UserRole,
    UserStatus,
)
```

Wipe (add to the existing delete block in `handle()`, before `User.objects.all().delete()`):
```python
        self.stdout.write("Wiping existing data…")
        # Children first; cascades cover the rest, but be explicit.
        Feedback.objects.all().delete()
        Submission.objects.all().delete()
        Assignment.objects.all().delete()
        StudentModuleProgress.objects.all().delete()
        Exercise.objects.all().delete()
        LearningModule.objects.all().delete()
        LessonPlan.objects.all().delete()
        ClassStudent.objects.all().delete()
        Class.objects.all().delete()
        SystemLog.objects.all().delete()   # NEW (leaf; admin FK is SET_NULL)
        User.objects.all().delete()
```

Seed rows (insert after the `# ---------- Users ----------` block, where `admin` and `students` exist — `students[11]` is the seeded suspended student):
```python
        # ---------- System Logs (admin audit) ----------
        self.stdout.write("Seeding system logs…")
        suspended = students[11]  # seeded as UserStatus.SUSPENDED above
        SystemLog.objects.bulk_create([
            SystemLog(
                admin=admin,
                action_description=(
                    f"Set status of user {suspended.id} ({suspended.email}) "
                    f"to 'suspended'"),
                target_status=UserStatus.SUSPENDED),
            SystemLog(
                admin=admin,
                action_description="Reviewed user directory and permissions",
                target_status=None),
            SystemLog(
                admin=admin,
                action_description="Checked system health dashboard",
                target_status=None),
        ])
```

> `timestamp` is `auto_now_add`, so `bulk_create` stamps each row at seed time — fine for a demo feed. (To spread them across time for a more realistic feed, create them individually and `update()` `timestamp`; not required for the MVP.)

Summary line (add to the trailing `self.stdout.write` block):
```python
        self.stdout.write(f"  SystemLogs:   {SystemLog.objects.count():>4}")
```

## Migrations
TextChoices are not involved (`target_status` is a free `CharField`), so there is **no** `ALTER TYPE` dance — this is a plain additive table create. Run:

```bash
python manage.py makemigrations core   # generates 000N_systemlog.py
python manage.py migrate
```

- Existing migrations are `0001_initial` and `0002_submission_answers_submission_auto_score_and_more`. The new migration's number (`0003`…`0007`) depends on which other files in this set (03/06 add models; 01/02/04/05 may alter existing models) you apply first. Just run `makemigrations core` after this file's model edits land; Django picks the next number and the right `dependencies`.
- The user-directory filter, audit hook, activity view, and health view are **pure Python/view changes** — they need no migration. Only the `SystemLog` model create does.
- **No data migration / backfill** is required: `SystemLog` is a brand-new table; existing rows in other tables are untouched. The `?role=`/`?status=`/`?search=` filters operate on existing columns (`User.role`, `User.status`, `User.email`, `User.full_name`) that already exist since `0001_initial`.

To repopulate demo data (now including audit rows):
```bash
python manage.py seed_demo
```

## RBAC / permissions
| Action | Endpoint | Allowed roles | Permission classes |
|---|---|---|---|
| List/filter user directory | `GET /api/users/?role=&status=&search=` | Admin only | `_perms(IsAdmin)` (existing `get_permissions` already gates `list`) |
| Suspend/activate a user (audited) | `PUT/PATCH /api/users/{id}/` (`status`) | Admin only | `_perms(IsAdmin)` (existing) + audit side-effect |
| Read admin activity feed | `GET /api/admin/activity/` | Admin only | `_BASE + [IsAdmin]` |
| Read system health | `GET /api/admin/health/` | Admin only | `_BASE + [IsAdmin]` |
| Browse audit log in Django admin | `/admin/` → System logs | Django staff/superuser (admin) | Django admin auth; view-only (no add/change/delete) |

- **Reuse existing** permission classes only — `IsActiveUser` + `IsAdmin` from `core/permissions.py`, composed via `_perms(*extra)` (ViewSet actions) or as a plain class list `_BASE + [IsAdmin]` (the two `APIView`s). **No new permission class is needed.**
- Suspended/inactive users are already rejected by `IsActiveUser` (do not re-implement) — a suspended user cannot reach any admin endpoint.
- Non-admins setting `status` is prevented by file 02 (and the existing `data.pop("status")` in `me`); this file's audit hook therefore only ever logs admin-initiated changes.
- The audit write uses `request.user` as `admin`; since these write paths are admin-gated, the actor is always an admin (or becomes `null` only if that admin is later deleted, via `SET_NULL`).

## Acceptance criteria & smoke test
Checklist:
- [ ] `python manage.py makemigrations core` creates a `*_systemlog.py` migration; `migrate` applies cleanly.
- [ ] `python manage.py seed_demo` runs and prints a `SystemLogs:` count (3).
- [ ] `GET /api/users/?role=teacher` returns only teachers; `?status=suspended` returns the seeded suspended student; `?search=alice` matches by name/email. With **no** params the response is identical to today (contract preserved).
- [ ] `PATCH /api/users/{id}/` with `{"status":"suspended"}` as Admin → 200 **and** a new `SystemLog` row exists with `target_status="suspended"` and `admin_id` = admin's id.
- [ ] The same `PATCH` as a Teacher/Student → 403 (existing admin gate), and **no** `SystemLog` row is written.
- [ ] `GET /api/admin/activity/` as Admin → 200, a **bare JSON array** newest-first, each item `{type,id,summary,timestamp}`; as Teacher/Student → 403.
- [ ] `GET /api/admin/health/` as Admin → 200 with `status:"ok"`, `database:"ok"`, numeric `db_latency_ms`, and a `counts` object; as Teacher/Student → 403.
- [ ] `SystemLog` appears in `/admin/` and is view-only (no Add button; rows not editable/deletable).
- [ ] `/api/docs/` shows the activity/health endpoints under an "admin" tag and the user-list filter params.

Seeded users (password `password123` for all):

```bash
# 1) Tokens (login returns {access_token, refresh_token, token_type:"bearer"})
ADMIN=$(http --ignore-stdin POST :8000/api/auth/login \
  email=admin@english.app password=password123 | jq -r .access_token)
TEACHER=$(http --ignore-stdin POST :8000/api/auth/login \
  email=emma.teacher@english.app password=password123 | jq -r .access_token)
STUDENT=$(http --ignore-stdin POST :8000/api/auth/login \
  email=alice.student@english.app password=password123 | jq -r .access_token)

# 2) User directory filters (Admin) — UC-19
http --ignore-stdin GET ":8000/api/users/?role=teacher"        "Authorization:Bearer $ADMIN"
http --ignore-stdin GET ":8000/api/users/?status=suspended"    "Authorization:Bearer $ADMIN"
http --ignore-stdin GET ":8000/api/users/?search=alice"        "Authorization:Bearer $ADMIN"

# 3) Suspend a user as Admin → 200, AND it gets audited
#    (pick an active student id, e.g. Bob; find it from the directory above)
http --ignore-stdin PATCH :8000/api/users/<BOB_ID>/ "Authorization:Bearer $ADMIN" status=suspended
#    -> reactivate
http --ignore-stdin PATCH :8000/api/users/<BOB_ID>/ "Authorization:Bearer $ADMIN" status=active

# 4) Non-admin cannot change status → 403 (existing gate; no audit row)
http --ignore-stdin PATCH :8000/api/users/<BOB_ID>/ "Authorization:Bearer $TEACHER" status=suspended

# 5) Activity feed (Admin only) — expect 200 + bare array newest-first
http --ignore-stdin GET ":8000/api/admin/activity/?limit=20" "Authorization:Bearer $ADMIN"
http --ignore-stdin GET  :8000/api/admin/activity/           "Authorization:Bearer $STUDENT"   # expect 403

# 6) Health (Admin only)
http --ignore-stdin GET :8000/api/admin/health/ "Authorization:Bearer $ADMIN"
http --ignore-stdin GET :8000/api/admin/health/ "Authorization:Bearer $TEACHER"   # expect 403
```

curl equivalents (suspend + activity + health):
```bash
curl -s -X PATCH http://localhost:8000/api/users/3/ \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"status":"suspended"}'

curl -s "http://localhost:8000/api/admin/activity/?limit=20" \
  -H "Authorization: Bearer $ADMIN"

curl -s http://localhost:8000/api/admin/health/ \
  -H "Authorization: Bearer $ADMIN"
```

manage.py shell sanity check (proves the audit hook + health DB probe without HTTP):
```python
from core.models import SystemLog, User, UserStatus
from core.views import _log_status_change

admin = User.objects.get(email="admin@english.app")
bob = User.objects.get(email="bob.student@english.app")

before = SystemLog.objects.count()
# Simulate what UserViewSet.update does after an admin status change:
bob.status = UserStatus.SUSPENDED
bob.save(update_fields=["status"])
_log_status_change(admin, bob, bob.status)

assert SystemLog.objects.count() == before + 1
log = SystemLog.objects.order_by("-id").first()
assert log.admin_id == admin.id
assert log.target_status == UserStatus.SUSPENDED
print("audit OK:", log.action_description)

# Health DB probe:
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SELECT 1"); print("db ok:", cur.fetchone())
```

## Deferred / out of scope
- **Live CPU / memory metrics (psutil).** `psutil` is **not** in `requirements.txt`, so `GET /api/admin/health/` ships with only stdlib metrics (DB connectivity, latency timing, counts). To grow it later: add `psutil` to `requirements.txt` and extend `HealthSerializer` + `AdminHealthView` with `cpu_percent` / `memory_percent` (guard with a `try: import psutil` so the endpoint degrades gracefully if absent). This matches the repo's "mock the external bits, ship the interface" stance.
- **Real DB query-latency profiling.** The MVP times a single `SELECT 1` round-trip as a proxy for "database query latency" (docs.md UC-20). True per-endpoint query profiling (e.g. `django-silk`, APM) is out of scope.
- **Richer / persisted activity feed.** The feed is computed on-the-fly by merging the most-recent N rows from a few tables in Python. It is not paginated (consistent with the bare-array convention) and not backed by a denormalized event table. If volume grows, replace the in-view merge with a dedicated event table or a DB-level `UNION` query — additive, no contract change to `{type,id,summary,timestamp}`.
- **Broader audit coverage.** This file only audits **user status changes** (the docs.md §5 Table 12 focus). Logging other governance actions (AI-model config changes from file 06, class/material management) is a natural extension: call `SystemLog.objects.create(...)` from those code paths with an appropriate `action_description` and `target_status=None`. AI-model audit specifically is left to **06**.
- **JWT blacklist on logout (UC-03).** docs.md mentions a token-blacklist hook on logout; that is session/auth plumbing, not admin audit, and is out of scope here.
- **Owned by other files.** Field/enum edits to existing models (Exercise/Submission/Feedback, NOT-NULL tightening) belong to **01**; admin-only `status` write enforcement is **02**; the Submission `status` field this file references is **04**; the other brand-new standalone models are `StudyMaterial` → **03** and `AiModel` → **06**.

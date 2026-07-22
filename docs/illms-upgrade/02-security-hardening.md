# 02 — Security hardening: register role allowlist, feedback ownership, class teacher lock

**Status:** Proposed · **Depends on:** [01 — Model foundations](./01-model-foundations.md) (`Class.teacher_id` → NOT NULL interacts with the teacher-lock) · **Spec refs:** docs.md §2 RBAC rows 1 (Register Basic Account — Admin only), 10/11 (Grade / Review Graded Results), 14 (Manage User Directory & Permissions), 15 (Classroom Group Management); docs.md §3.1 UC-01 (Register: *Admin* selects role `Student` or `Teacher`); docs.md §6 safeguards 1 (Nullable Evaluator) & 3 (Stateless JWT Guarding); UI_FLOW_BRIEF.md:75 ("admin is provisioned, not self-signup") + J6 ("suspended → 403"); REFACTOR_PLAN.md §4 (Security fixes); DJANGO_MIGRATION.md "Carried-over security gaps".

**Estimated blast radius:** `core/serializers.py` (RegisterSerializer), `core/views.py` (RegisterView, SubmissionViewSet.feedback, FeedbackViewSet, ClassViewSet.update), `core/permissions.py` (one new helper predicate). No model changes, no migration owned by this file. No new endpoints, no new tags.

---

## Goal

Close the three carried-over security gaps that the FastAPI→Django port faithfully reproduced (documented verbatim in `DJANGO_MIGRATION.md` "Carried-over security gaps" and `REFACTOR_PLAN.md` §4):

1. **Role self-escalation** — registration accepts any `role`, including `admin`. Restrict self-signup to `{student, teacher}` so admins can only be provisioned (seed / superuser), per docs.md UC-01 and UI_FLOW_BRIEF.md:75.
2. **Feedback leak** — `GET /api/submissions/{id}/feedback/` has no ownership check; any active user reads any feedback by guessing IDs. Restrict reads to submission owner / reviewer / class teacher / admin (RBAC rows 10–11).
3. **Class teacher reassignment** — a teacher can move a class to another teacher via `PATCH /api/classes/{id}/`. Lock `teacher_id` so only admin may set/change it (RBAC rows 14–15), mirroring the existing `status` lock in `UserViewSet.me`.

This file is **view / serializer / permission edits only** — additive and contract-preserving. The login response shape (`{access_token, refresh_token, token_type:"bearer"}`), all field names, all `*_id` wire aliases, and bare-array list responses are untouched.

---

## Why (gap)

| Gap | What the code does today | What the spec requires |
|---|---|---|
| Role escalation | `core/serializers.py` → `RegisterSerializer.Meta.fields` includes `"role"` with **no field- or object-level validation** of its value; it is a plain `ModelSerializer` field over `User.role` (a `CharField(choices=UserRole.choices)`, so `admin` is an accepted choice). `RegisterView.post` (`core/views.py:65-71`) validates only email-uniqueness, then calls `ser.save()` — nothing rejects `role=="admin"`. A `POST /api/auth/register {"role":"admin", …}` mints an admin. | docs.md §2 RBAC row 1: only Administrator may "Register Basic Account". docs.md §3.1 **UC-01**: "Admin enters email … and selects role (`Student` or `Teacher`)." UI_FLOW_BRIEF.md:75: "role selector (student/teacher; **admin is provisioned, not self-signup**)." |
| Feedback leak | `SubmissionViewSet.feedback` (`core/views.py:423-427`) does `Feedback.objects.filter(submission=sub)` with **no owner/role check** — `get_permissions` returns `_perms()` (just `IsAuthenticated, IsActiveUser`). Any active user can read any submission's feedback by ID. `FeedbackViewSet` (`core/views.py:432`) is `CreateModelMixin` only (no list/retrieve today), but shares the same unguarded `queryset = Feedback.objects.all()`. | docs.md §2 RBAC rows 10–11: grading/remarks are Teacher+Admin; "Review Graded Results" is the **student's own**. REFACTOR_PLAN.md §4: "allow submission owner, the reviewer, the class teacher, or admin only." docs.md §6 safeguard 1 (Nullable Evaluator): when reading grading state, **inspect `is_ai_generated`**; never assume `evaluator_id` (the repo's `reviewer_id`) is non-null. |
| Class teacher reassignment | `ClassViewSet.update` (`core/views.py:193-203`) checks the caller owns the class (`obj.teacher_id != user.id → 403`) but then passes **all** request data — including `teacher_id` — to `ClassSerializer`, where `teacher_id` is a writable `PrimaryKeyRelatedField(source="teacher", required=False)` (`core/serializers.py:73-76`). A teacher can `PATCH {"teacher_id": <other>}` and hand the class away. | docs.md §2 RBAC rows 14 (Manage User Directory & Permissions) + 15 (Classroom Group Management): reassigning ownership is an admin governance action. REFACTOR_PLAN.md §3.2: "`update_class`: strip `teacher_id` from teacher-writable fields (mirror the `status` pattern)." |

**Already handled — do NOT re-implement:** suspended-user `403` enforcement exists in `core/permissions.py` → `IsActiveUser.has_permission` (`return user.status == UserStatus.ACTIVE`), wired globally via `config/settings.py` `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` and per-view via `_BASE = [IsAuthenticated, IsActiveUser]` (`core/views.py:47`). This satisfies UI_FLOW_BRIEF.md J6 ("suspended → 403"). REFACTOR_PLAN.md §4's third bullet ("Suspended not enforced") describes the **old FastAPI** state and is stale for this Django repo — verify and reference, do not add a duplicate check.

**docs.md §6 safeguard 3 (Stateless JWT Guarding):** the repo already complies — auth is `rest_framework_simplejwt.authentication.JWTAuthentication` (`config/settings.py` `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`), no server-side session auth, and `request.user.role` is read from the JWT-resolved user on every request. The role allowlist below is enforced at the serializer boundary, not via any new session state, so it preserves this directive.

---

## Changes (file by file)

### core/serializers.py

Restrict `RegisterSerializer.role` to the self-signup allowlist `{student, teacher}`. Two complementary guards (belt + suspenders): (a) narrow the field's `choices` so the OpenAPI schema and DRF's built-in `ChoiceField` validation reject `admin`/`inactive` inputs at the field level; (b) an explicit `validate_role` for an unambiguous error message and so the rule survives if the field declaration is later refactored. Keep `role` **required** (the frontend register form always sends it — UI_FLOW_BRIEF.md:75), matching today's behavior where it is a required model field.

```python
# core/serializers.py  (UserRole is already imported at the top of the module)

# Roles a user may self-assign at registration. Admin is provisioned only
# (seed / createsuperuser), never self-signup — docs.md UC-01, UI_FLOW_BRIEF:75.
SELF_SIGNUP_ROLES = (UserRole.STUDENT, UserRole.TEACHER)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6, max_length=128)
    # Narrow the choices so `admin` is rejected at field validation AND the
    # OpenAPI schema advertises only the two allowed values.
    role = serializers.ChoiceField(
        choices=[(r.value, r.label) for r in SELF_SIGNUP_ROLES]
    )

    class Meta:
        model = User
        fields = ["id", "email", "password", "full_name", "avatar_url", "role"]
        read_only_fields = ["id"]

    def validate_role(self, value):
        # Defense in depth: explicit, friendly 400 even if `choices` is widened.
        if value not in SELF_SIGNUP_ROLES:
            raise serializers.ValidationError(
                "Self-registration is limited to 'student' or 'teacher'. "
                "Admin accounts are provisioned by an administrator."
            )
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)
```

> Contract note: `POST /api/auth/register` with `role` in `{student, teacher}` is unchanged. `role:"admin"` now returns **400** instead of **201** — this is the intended fix and the only behavioral change. `UserSerializer` (the response) and the field set are untouched, so the 201 success payload is byte-identical to today's.

### core/views.py — `RegisterView`

No structural change required: `RegisterView.post` (`core/views.py:62-71`) already calls `ser.is_valid(raise_exception=True)`, so the serializer-level guard above is enforced automatically and returns a 400 with the field error. **Do not** add role logic in the view — keep the single source of truth in the serializer. (Listed here only to make explicit that the view is intentionally left as-is.)

### core/permissions.py — new feedback ownership predicate

The feedback ownership rule is not a clean "role" check (it depends on the *object*: owner OR reviewer OR that class's teacher OR admin), so it does not fit the existing `_RolePermission` family. Implement it as a small helper used by the view rather than a global permission class — this keeps it colocated with the queryset logic and avoids `has_object_permission` plumbing for an `@action`. Add a reusable predicate next to the existing classes:

```python
# core/permissions.py  (UserRole already imported)

def can_view_submission_feedback(user, submission) -> bool:
    """True if `user` may read feedback on `submission`.

    Allowed: the submission's student (owner), the teacher who owns the
    submission's class (via assignment.klass.teacher), a teacher who reviewed
    it, or any admin. Mirrors REFACTOR_PLAN §4 and docs.md RBAC rows 10-11.

    Per docs.md §6 safeguard 1 (Nullable Evaluator), do NOT assume a reviewer
    exists: feedback can be AI-generated (reviewer_id / docs.md `evaluator_id`
    null). Ownership here is decided by the submission + class, independent of
    whether a human reviewer is set.
    """
    if user.role == UserRole.ADMIN:
        return True
    if submission.student_id == user.id:
        return True
    if user.role == UserRole.TEACHER:
        # Class teacher (assignment may be null for ad-hoc practice subs).
        assignment = submission.assignment
        if assignment and assignment.klass_id and assignment.klass.teacher_id == user.id:
            return True
        # Teacher who authored a feedback row on this submission (reviewer).
        if submission.feedback.filter(reviewer_id=user.id).exists():
            return True
    return False
```

> `submission.feedback` is the existing reverse accessor (`Feedback.submission` → `related_name="feedback"`, `core/models.py:339`). `submission.assignment.klass.teacher_id` follows real FKs (`Submission.assignment` → `Assignment.klass` → `Class.teacher`). Both `assignment` and `klass` are nullable (`Submission.assignment` and `Assignment.klass` are `SET_NULL`), so the `if assignment and assignment.klass_id` guard is required.

### core/views.py — `SubmissionViewSet.feedback`

Enforce the guard on the GET feedback action. The action stays a bare-array GET; only an authorization gate is added before returning.

```python
# core/views.py — extend the existing permissions import
from .permissions import (
    IsActiveUser, IsAdmin, IsStudent, IsTeacherOrAdmin,
    can_view_submission_feedback,            # NEW
)

# ... inside SubmissionViewSet, replace the body of `feedback`:
    @extend_schema(
        summary="List feedback on a submission (owner / reviewer / class teacher / admin)",
        responses={
            200: s.FeedbackSerializer(many=True),
            403: OpenApiResponse(description="Not permitted to view this feedback"),
        },
    )
    @action(detail=True, methods=["get"], url_path="feedback")
    def feedback(self, request, pk=None):
        sub = self.get_object()
        if not can_view_submission_feedback(request.user, sub):
            raise PermissionDenied("Not permitted to view this feedback")
        qs = Feedback.objects.filter(submission=sub).order_by("id")
        return Response(s.FeedbackSerializer(qs, many=True).data)
```

> `PermissionDenied` is already imported (`core/views.py:21`); `OpenApiResponse` is already imported (`core/views.py:12`). The success path (200 bare array of `FeedbackSerializer`) is unchanged for authorized callers — no frontend contract break. Unauthorized callers who previously got 200 now get 403, which is the fix.

### core/views.py — `FeedbackViewSet`

`FeedbackViewSet` is currently `CreateModelMixin` only (`core/views.py:432`) — it exposes `POST /api/feedback/` guarded by `_perms(IsTeacherOrAdmin)`, with no list/retrieve, so there is **no read leak through this viewset today**. To prevent a future leak if a `Retrieve`/`List` mixin is ever added, defensively scope its queryset to what the caller may see, and keep the create permission as-is. This is additive (create behavior identical) and contract-preserving.

```python
# core/views.py — top-level import (NEW; not currently present)
from django.db.models import Q

# core/views.py — FeedbackViewSet
@extend_schema(tags=["feedback"])
class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = s.FeedbackSerializer

    def get_queryset(self):
        # Base queryset, scoped so any future list/retrieve action cannot leak
        # other users' feedback. Create (the only current action) ignores this.
        qs = Feedback.objects.all().order_by("id")
        user = self.request.user
        if user.role == UserRole.ADMIN:
            return qs
        if user.role == UserRole.TEACHER:
            return qs.filter(
                Q(reviewer=user) | Q(submission__assignment__klass__teacher=user)
            ).distinct()
        # Student: only feedback on their own submissions.
        return qs.filter(submission__student=user)

    def get_permissions(self):
        return _perms(IsTeacherOrAdmin)

    def perform_create(self, serializer):
        serializer.save(reviewer=self.request.user)
```

> Replacing the class attribute `queryset = …` with `get_queryset()` is safe: DRF prefers `get_queryset()`, and `basename="feedback"` is already set in `config/urls.py`, so dropping the `queryset` attribute does not break URL reversing. Leave `perform_create` exactly as-is (`reviewer=self.request.user`) — this file does NOT own `Feedback.is_ai_generated` semantics (that is file 06).

### core/views.py — `ClassViewSet.update`

Lock `teacher_id`: non-admin callers may not set or change it. Mirror the exact pattern used in `UserViewSet.me` for `status` (`core/views.py:137-138` — pop the protected key from validated data for non-admins). Validate after `is_valid`, then strip before `save`.

```python
# core/views.py — ClassViewSet.update (replace existing method body)
    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if user.role == UserRole.TEACHER and obj.teacher_id != user.id:
            raise PermissionDenied("Not your class")
        ser = self.get_serializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        # Only admin may set/change the class teacher (mirrors the `status`
        # lock in UserViewSet.me). For everyone else, drop teacher_id so the
        # existing owner is preserved. Spec: docs.md RBAC rows 14-15.
        if user.role != UserRole.ADMIN:
            ser.validated_data.pop("teacher", None)   # serializer source is `teacher`
        ser.save()
        return Response(ser.data)
```

> Note the key is `"teacher"`, not `"teacher_id"`: `ClassSerializer.teacher_id` uses `source="teacher"` (`core/serializers.py:73`), so after validation the value lands in `validated_data["teacher"]`. `partial_update` already delegates to `update` (`core/views.py:205-207`), so PATCH is covered. `ClassViewSet.create` is out of scope here — it is already `_perms(IsTeacherOrAdmin)` and `perform_create` forces `teacher=self.request.user` for teachers (`core/views.py:187-191`); tightening *creation* to admin-only is REFACTOR_PLAN §3.2 territory and is **deferred** (see below) to keep this change non-breaking for the current teacher-creates-own-class flow.

### config/settings.py · config/urls.py · core/admin.py · core/management/commands/seed_demo.py

**No changes.**
- `settings.py`: no new tag (reuses `auth`, `submissions`, `feedback`, `classes`); no permission-class change (the global `IsActiveUser` already does the suspended check).
- `urls.py`: no new routes; `feedback`/`submissions`/`classes` already registered.
- `admin.py`: no new model.
- `seed_demo.py`: the existing seed already provisions the admin via `User.objects.create_user(role=UserRole.ADMIN, is_staff=True, is_superuser=True)` (`seed_demo.py:59-63`) — i.e. admins are *provisioned*, never self-signup, which is exactly the invariant this file enforces at the API boundary. The suspended student (`students[11]` = `luna.student@english.app`, `seed_demo.py:94`) already exercises the `IsActiveUser` path. No seed edit needed to prove any of the three fixes.

---

## Migrations

**None for this file.** All three fixes are serializer/view/permission logic over existing columns — no schema change, so `makemigrations` produces nothing here. (TextChoices are plain `varchar`; even a future enum change would be a model edit + `makemigrations core` with **no** native-PG `ALTER TYPE` dance.)

The only schema interaction is the **dependency** on file 01: once `Class.teacher_id` becomes `NOT NULL` (owned by 01), the `pop("teacher", None)` strip in `ClassViewSet.update` must never null the column. It cannot — `pop` removes the key so the existing value is preserved on save; a PATCH without `teacher_id` likewise leaves it untouched. Apply 01's migration first:

```bash
python manage.py makemigrations core   # from file 01 (Class.teacher_id NOT NULL, etc.)
python manage.py migrate
# this file (02): no migration of its own
```

No data backfill required.

---

## RBAC / permissions

| Action | Student | Teacher | Admin | Mechanism |
|---|:---:|:---:|:---:|---|
| `POST /api/auth/register` role=student/teacher | ✅ (self) | ✅ (self) | ✅ (self) | `RegisterSerializer` choices + `validate_role` |
| `POST /api/auth/register` role=admin | ❌ 400 | ❌ 400 | ❌ 400 | same — provisioned via seed/superuser only |
| `GET /api/submissions/{id}/feedback/` — own submission | ✅ | ✅ | ✅ | `can_view_submission_feedback` (owner) |
| `GET /api/submissions/{id}/feedback/` — class you teach | n/a | ✅ | ✅ | `can_view_submission_feedback` (class teacher / reviewer) |
| `GET /api/submissions/{id}/feedback/` — unrelated submission | ❌ 403 | ❌ 403 | ✅ | `can_view_submission_feedback` |
| `PATCH /api/classes/{id}/ {teacher_id}` | ❌ (already 403 via `IsTeacherOrAdmin`) | ❌ silently dropped, owner preserved | ✅ reassign | `ClassViewSet.update` strip + `IsTeacherOrAdmin` |
| Any endpoint while `status=suspended` | ❌ 403 | ❌ 403 | ❌ 403 | **existing** `IsActiveUser` (unchanged) |

**Reused:** `IsAuthenticated`, `IsActiveUser`, `IsTeacherOrAdmin`, the `_BASE`/`_perms()` stack. **New:** one module-level predicate `can_view_submission_feedback(user, submission)` in `core/permissions.py` (a function, not a `BasePermission` class — used directly inside the `@action`). No change to the role-permission class hierarchy.

---

## Acceptance criteria & smoke test

Seeded users (password `password123`): `admin@english.app`, `emma.teacher@english.app`, `david.teacher@english.app`, `sophie.teacher@english.app`, `alice.student@english.app` … `luna.student@english.app` (`luna` is the suspended one — `seed_demo.py:94`).

Checklist:
- [ ] `register` with `role:"admin"` → **400**, body names `role`.
- [ ] `register` with `role:"student"` and `role:"teacher"` → **201**, unchanged payload (id, email, full_name, avatar_url, role, status, timestamps).
- [ ] A student reading **their own** submission's feedback → **200** bare array.
- [ ] A student reading **another student's** submission feedback → **403**.
- [ ] The class teacher (emma) reading a submission in a class she teaches → **200**; a teacher (david) reading a submission in a class he does **not** teach → **403**.
- [ ] admin reading any submission's feedback → **200**.
- [ ] A teacher `PATCH /api/classes/{own}/ {"teacher_id": <other>}` → **200** but `teacher_id` unchanged; admin doing the same → **200** with `teacher_id` changed.
- [ ] A suspended user (luna) hitting any guarded endpoint → **403** (regression check on existing `IsActiveUser`; no new code).

Smoke (httpie; bare login response is `{access_token,…}`):

```bash
# 0) tokens
ADMIN=$(http POST :8000/api/auth/login email=admin@english.app  password=password123 | jq -r .access_token)
EMMA=$( http POST :8000/api/auth/login email=emma.teacher@english.app password=password123 | jq -r .access_token)
DAVID=$(http POST :8000/api/auth/login email=david.teacher@english.app password=password123 | jq -r .access_token)
ALICE=$(http POST :8000/api/auth/login email=alice.student@english.app password=password123 | jq -r .access_token)
BOB=$(  http POST :8000/api/auth/login email=bob.student@english.app   password=password123 | jq -r .access_token)
LUNA=$( http POST :8000/api/auth/login email=luna.student@english.app  password=password123 | jq -r .access_token)

# 1) role escalation blocked  -> expect 400
http POST :8000/api/auth/register email=hacker@x.io password=password123 \
     full_name="H A X" role=admin
# self-signup allowed          -> expect 201
http POST :8000/api/auth/register email=newteacher@x.io password=password123 \
     full_name="New Teacher" role=teacher

# 2) feedback ownership. submissions[0] -> students[0]=alice, exercise[0] in
#    classes[0] taught by emma (per seed_demo ordering).
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $ALICE"   # 200 (owner)
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $BOB"     # 403 (other student)
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $EMMA"    # 200 (class teacher)
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $DAVID"   # 403 (other teacher)
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $ADMIN"   # 200 (admin)
http GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $LUNA"    # 403 (suspended, existing IsActiveUser)

# 3) teacher cannot reassign class. classes[0] (id=1) is emma's.
#    Pick a teacher id that is NOT emma, e.g. david's id (<david_id>).
http PATCH :8000/api/classes/1/ "Authorization:Bearer $EMMA"  teacher_id:=<david_id>  # 200, teacher_id unchanged
http GET   :8000/api/classes/1/ "Authorization:Bearer $EMMA"                           # teacher_id still emma
http PATCH :8000/api/classes/1/ "Authorization:Bearer $ADMIN" teacher_id:=<david_id>  # 200, teacher_id == david
```

manage.py shell unit-level proof of the feedback predicate (no HTTP):

```bash
python manage.py shell <<'PY'
from core.models import User, Submission
from core.permissions import can_view_submission_feedback
sub = Submission.objects.filter(feedback__isnull=False).first()
owner = sub.student
other = User.objects.exclude(id=owner.id).filter(role="student").first()
admin = User.objects.filter(role="admin").first()
print("owner        ->", can_view_submission_feedback(owner, sub))  # True
print("other student->", can_view_submission_feedback(other, sub))  # False
print("admin        ->", can_view_submission_feedback(admin, sub))  # True
PY
```

---

## Deferred / out of scope

- **Admin-only class *creation*** (REFACTOR_PLAN §3.2 "drop teacher" from `create_class`, plus validating `teacher_id` targets a `role==teacher` user). Tightening creation would break the current teacher-creates-own-class flow the frontend uses; this file only locks *reassignment* on update. Grow later by switching `ClassViewSet.get_permissions` for `create` to `_perms(IsAdmin)` and adding teacher-role validation in `perform_create`.
- **`Feedback.is_ai_generated` semantics & nullable-reviewer attribution** (docs.md §6 safeguard 1) — the *field* is added in file 01, the *logic* (AI vs human attribution, auto-grading feedback rows) is owned by files 04/06. `can_view_submission_feedback` here is already written to not assume a reviewer exists, so it is forward-compatible.
- **JWT blacklist / logout (UC-03)** — docs.md §3.1 UC-03 mentions a "backend token blacklist hook." Stateless JWT (docs.md §6 safeguard 3) means no session table; a refresh-token blacklist is a separate feature, not part of these three gaps.
- **Object-level `has_object_permission` for `SubmissionViewSet`** — feedback is currently the only read-leak surface, guarded inline. If more detail actions are added later, promote `can_view_submission_feedback` into a `BasePermission.has_object_permission` so it applies uniformly.
- **Rate limiting / brute-force protection on `register`/`login`** — orthogonal hardening, not a carried-over gap.

# 03 — Study materials library — admin-managed documents, read by all

**Status:** Proposed · **Depends on:** 01 (migration mechanics, conventions) · **Spec refs:** docs.md §2 RBAC rows 6 ("Manage Official Study Materials Library") & 7 ("Read & Save Study Documents") ; docs.md §4 DBML `Table study_materials` ; docs.md §5 "Table 2: study_materials" ; UC docs §2 (Student Learning & Practice Workflows — reading official documents)
**Estimated blast radius:** `core/models.py`, `core/serializers.py`, `core/views.py`, `config/urls.py`, `config/settings.py`, `core/admin.py`, `core/management/commands/seed_demo.py`, plus one new migration (`core/migrations/0003_studymaterial.py`).

## Goal
Add an official study-materials library: a NEW `StudyMaterial` model that Admins fully manage (create/update/delete) and that every authenticated, active user (Admin, Teacher, Student) can list and read. This implements docs.md's `study_materials` table and satisfies RBAC row 6 (Admin-only write) and row 7 (read by all roles). The library is exposed as a new `/api/study-materials/` resource and registered in the Django admin so Admins can manage documents both via the API and via `/admin/` (the point of the migration).

## Why (gap)
Today the repo has **no** model, endpoint, admin registration, serializer, or seed data for study materials. Grep confirms `study_materials` / `StudyMaterial` appear nowhere under `core/`. The closest existing concept is `LearningModule` + `Exercise` (interactive coursework graded via `Submission`/`Feedback`), which is a different thing: docs.md's `study_materials` (§5 Table 2) is a flat catalogue of **static reference documents** ("official study documents managed by Admins") with just `id, title, file_url, uploaded_by, created_at` — no questions, no grading, no progress. REFACTOR_PLAN.md sketched this as a "Document" model that was never built.

Concretely, the gap vs the current code:

| docs.md requirement | Current code | This file adds |
|---|---|---|
| `study_materials` table (§4 DBML, §5 Table 2) | (nothing) | NEW `StudyMaterial` model, `db_table = "study_materials"` |
| RBAC row 6 — Admin manages the library | `IsAdmin` exists in `core/permissions.py` but no view uses it for materials | `StudyMaterialViewSet` gates write actions with `_perms(IsAdmin)` |
| RBAC row 7 — all roles read & save documents | (nothing) | `list`/`retrieve` open to the `_BASE` stack `[IsAuthenticated, IsActiveUser]` |
| `uploaded_by` FK to users (§5 Table 2 rationale) | (nothing) | `uploaded_by = FK(User, SET_NULL, related_name="study_materials")`, surfaced read-only as `uploaded_by_id` |

The wire idioms to match are already established in the repo: FKs surfaced as `*_id` via `PrimaryKeyRelatedField(source=...)` (`core/serializers.py` `ClassSerializer.teacher_id`, `ProgressSerializer.student_id`), the shared permission helper `_perms(*extra)` in `core/views.py`, explicit `db_table` on every model, and mock string URLs for files (e.g. `audio_prompt_url="https://mock.cdn/audio/..."` in `seed_demo.py`).

### docs.md `study_materials` → Django field mapping
Map each docs.md DBML column onto a concrete Django field so the table is matched exactly (one additive field — `description` — and one optional scope FK — `klass` — are extensions, marked below):

| docs.md column (§4 DBML / §5 Table 2) | Django field on `StudyMaterial` | Notes |
|---|---|---|
| `id` (PK) | implicit `BigAutoField` `id` | `DEFAULT_AUTO_FIELD = BigAutoField` (project default) |
| `title varchar(200)` | `title = CharField(max_length=200)` | exact length match |
| `file_url varchar(255)` | `file_url = CharField(max_length=255)` | mock string URL (no upload backend) |
| `uploaded_by` (FK users, nullable) | `uploaded_by = FK(User, SET_NULL, null=True, related_name="study_materials")` | surfaced read-only as `uploaded_by_id` |
| `created_at timestamp` | `created_at = DateTimeField(auto_now_add=True)` | server-set |
| *(none — additive)* | `description = TextField(null=True, blank=True)` | additive, safe; not in docs.md |
| *(none — optional scope)* | `klass = FK(Class, CASCADE, null=True, related_name="study_materials")` | optional class scope, surfaced as `class_id`; null = global doc |

## Changes (file by file)

### core/models.py
Add a new `StudyMaterial` model after `Feedback` (the last model). `file_url` is a plain `CharField` holding a **mock string URL** (per the repo's "mock file URLs as strings" rule — no real upload backend). `description` is a nullable `TextField` (extends docs.md's minimal field set; safe, additive). `uploaded_by` uses `SET_NULL` so deleting an admin does not destroy the catalogue, with `related_name="study_materials"` exactly as the SCOPE requires.

The SCOPE asks us to **decide** on an optional class scope: **include it** as a nullable `klass` FK (REFACTOR_PLAN.md's "Document" proposed scoping a doc to a class). It is nullable so the default is a global/official library document (`class_id == null`); a non-null `class_id` means the doc is scoped to one class. Follow the house rule: the model field is `klass` (`class` is a keyword), surfaced on the wire as `class_id`. This is additive and optional — global docs simply leave it null.

```python
# ---------- Study Material ----------
class StudyMaterial(models.Model):
    """Official reference document in the study-materials library.

    A flat catalogue of static documents (docs.md `study_materials`, §5 Table 2):
    Admins manage them, all roles read them. ``file_url`` is a mock string URL
    (no real upload backend in this MVP — same convention as
    ``Exercise.audio_prompt_url``). An optional ``klass`` scopes a document to a
    single class; left null it is a global/official library document.
    """

    title = models.CharField(max_length=200)
    file_url = models.CharField(max_length=255)  # mock string URL (no upload backend)
    description = models.TextField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="study_materials",
    )
    # Optional class scope: null = global/official document, set = class-scoped.
    klass = models.ForeignKey(
        Class, null=True, blank=True, on_delete=models.CASCADE,
        related_name="study_materials",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "study_materials"
        ordering = ["-created_at", "id"]

    def __str__(self):
        return self.title
```

Notes:
- `max_length=200` for `title` and `max_length=255` for `file_url` match the docs.md DBML exactly (`varchar(200)` / `varchar(255)`).
- `Class` is already defined above `Feedback` in this file, so the `klass` FK reference resolves without a string reference. (If you prefer, `"Class"` as a string also works.)
- `ordering = ["-created_at", "id"]` gives newest-first listing; list endpoints still return a bare array (pagination is disabled globally), so this only affects order, not shape.

### core/serializers.py
Add `StudyMaterial` to the `from .models import (...)` block (`Class` is already imported), then add `StudyMaterialSerializer`. `uploaded_by_id` is **read-only** (the view sets it to the requesting admin, mirroring how `FeedbackSerializer.reviewer_id` and `ProgressSerializer.student_id` are read-only and server-assigned). `file_url` is a plain string field — document the mock-upload note in the docstring. `class_id` is writable+optional so an admin can scope a doc to a class (matching `ExerciseSerializer.module_id` / `AssignmentSerializer.exercise_id` style).

```python
# (add StudyMaterial to the existing models import; Class is already imported)
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
    StudyMaterial,   # NEW
    Submission,
    User,
    UserRole,
)
```

```python
# ---------- Study Material ----------
class StudyMaterialSerializer(serializers.ModelSerializer):
    """Official study document. ``file_url`` is a plain mock string URL — there
    is no real upload backend in this MVP, so clients send/receive a URL string
    (same convention as ``Exercise.audio_prompt_url``). ``uploaded_by_id`` is
    set server-side to the requesting admin and is read-only."""

    uploaded_by_id = serializers.PrimaryKeyRelatedField(
        source="uploaded_by", read_only=True
    )
    class_id = serializers.PrimaryKeyRelatedField(
        source="klass", queryset=Class.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = StudyMaterial
        fields = [
            "id", "title", "file_url", "description",
            "uploaded_by_id", "class_id", "created_at",
        ]
        read_only_fields = ["id", "uploaded_by_id", "created_at"]
```

### core/views.py
Add `StudyMaterial` to the `from .models import (...)` block, then add a `StudyMaterialViewSet`. It is a full `ModelViewSet`: `list`/`retrieve` are open to the base stack (RBAC row 7 — read by all active users); `create`/`update`/`partial_update`/`destroy` require `IsAdmin` (RBAC row 6). `perform_create` stamps `uploaded_by` with the requesting admin, exactly like `FeedbackViewSet.perform_create` stamps `reviewer` and `ModuleViewSet.perform_create` stamps `created_by`.

```python
# (add StudyMaterial to the existing models import)
from .models import (
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    StudentModuleProgress,
    StudyMaterial,   # NEW
    Submission,
    SubmissionType,
    User,
    UserRole,
)
```

```python
# ============================================================ Study Materials
@extend_schema(tags=["study-materials"])
class StudyMaterialViewSet(viewsets.ModelViewSet):
    """Official study-documents library.

    Read (list/retrieve) is open to every authenticated, active user (RBAC
    row 7 — "Read & Save Study Documents"). Write (create/update/destroy) is
    Admin-only (RBAC row 6 — "Manage Official Study Materials Library")."""

    queryset = StudyMaterial.objects.all().order_by("-created_at", "id")
    serializer_class = s.StudyMaterialSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return _perms(IsAdmin)
        return _perms()

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)
```

No custom `update`/`retrieve` overrides are needed: there is no per-object ownership rule (any admin may edit any document; any active user may read any document), unlike `ClassViewSet`/`ModuleViewSet` which enforce teacher ownership. `IsAdmin` on the write actions is sufficient.

### config/urls.py
Register the new viewset on the existing `DefaultRouter` (bare-array lists are preserved because pagination is globally off). Path is `/api/study-materials/`.

```python
router.register("users", views.UserViewSet, basename="user")
router.register("classes", views.ClassViewSet, basename="class")
router.register("modules", views.ModuleViewSet, basename="module")
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("submissions", views.SubmissionViewSet, basename="submission")
router.register("feedback", views.FeedbackViewSet, basename="feedback")
router.register("progress", views.ProgressViewSet, basename="progress")
router.register("study-materials", views.StudyMaterialViewSet, basename="study-material")  # NEW
```

### config/settings.py
Add a `"study-materials"` tag to `SPECTACULAR_SETTINGS["TAGS"]` so the new endpoints group correctly in `/api/docs/` and `/api/redoc/`. No `ENUM_NAME_OVERRIDES` change is needed (this feature adds no new enum).

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
        {"name": "study-materials", "description": "Official study documents: Admin-managed, read by all"},  # NEW
    ],
```

### core/admin.py
Add `StudyMaterial` to the `from .models import (...)` block, then register a `StudyMaterialAdmin`. This is required by the house rule "NEW models MUST be registered here too" (Django admin is the point of the migration).

```python
# (add StudyMaterial to the existing models import)
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
    StudyMaterial,   # NEW
    Submission,
    User,
)
```

```python
# ---------- Study Materials ----------
@admin.register(StudyMaterial)
class StudyMaterialAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "uploaded_by", "klass", "created_at")
    list_filter = ("created_at",)
    search_fields = ("title", "description", "file_url")
    raw_id_fields = ("uploaded_by", "klass")
```

### core/management/commands/seed_demo.py
1. Add `StudyMaterial` to the `from core.models import (...)` block.
2. Add a wipe line in the children-first delete block. `StudyMaterial` is a leaf (nothing FKs *to* it), so deleting it first is always safe — it must come **before** `User` and `Class` (the tables it points at) so those later `.delete()` calls don't trip the `uploaded_by` / `klass` references.
3. Seed a few **admin-uploaded** materials after Users/Classes exist (so `uploaded_by=admin` and an optional `klass=` resolve). Use mock string URLs like the existing `audio_prompt_url` seeds. Add a count line to the summary.

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
    StudyMaterial,   # NEW
    Submission,
    SubmissionType,
    User,
    UserRole,
    UserStatus,
)
```

Wipe (add to the existing delete block in `handle()`):
```python
        self.stdout.write("Wiping existing data…")
        # Children first; cascades cover the rest, but be explicit.
        Feedback.objects.all().delete()
        Submission.objects.all().delete()
        Assignment.objects.all().delete()
        StudentModuleProgress.objects.all().delete()
        StudyMaterial.objects.all().delete()   # NEW (leaf; SET_NULL/CASCADE FKs)
        Exercise.objects.all().delete()
        LearningModule.objects.all().delete()
        LessonPlan.objects.all().delete()
        ClassStudent.objects.all().delete()
        Class.objects.all().delete()
        User.objects.all().delete()
```

Seed rows (insert after the `# ---------- Classes ----------` block so `admin` and `classes` exist; a couple are global, one is class-scoped to show off the optional `klass`):
```python
        # ---------- Study Materials ----------
        self.stdout.write("Seeding study materials…")
        StudyMaterial.objects.bulk_create([
            StudyMaterial(
                title="English Grammar Handbook (PDF)",
                file_url="https://mock.cdn/docs/grammar-handbook.pdf",
                description="Comprehensive reference: tenses, articles, prepositions.",
                uploaded_by=admin),
            StudyMaterial(
                title="IELTS Writing Band Descriptors",
                file_url="https://mock.cdn/docs/ielts-writing-descriptors.pdf",
                description="Official band 5–9 criteria for Writing Task 1 & 2.",
                uploaded_by=admin),
            StudyMaterial(
                title="Common Phrasal Verbs Cheat Sheet",
                file_url="https://mock.cdn/docs/phrasal-verbs.pdf",
                description="200 high-frequency phrasal verbs with examples.",
                uploaded_by=admin),
            StudyMaterial(
                title="Pronunciation Guide — IPA Vowel Chart",
                file_url="https://mock.cdn/docs/ipa-vowel-chart.pdf",
                description="Interactive IPA chart with audio links (mock).",
                uploaded_by=admin),
            # Class-scoped example (optional klass set): tied to "English 101 — Beginners".
            StudyMaterial(
                title="English 101 — Course Syllabus",
                file_url="https://mock.cdn/docs/english-101-syllabus.pdf",
                description="Week-by-week plan and grading policy for the beginners class.",
                uploaded_by=admin, klass=classes[0]),
        ])
```

Summary line (add to the trailing `self.stdout.write` block, matching the existing `:>4` numeric width used by every other count line):
```python
        self.stdout.write(f"  StudyMaterials:{StudyMaterial.objects.count():>4}")
```

## Migrations
TextChoices are not involved here, so there is **no** `ALTER TYPE` dance — this is a plain additive table create. Run:

```bash
python manage.py makemigrations core   # generates 0003_studymaterial.py (depends on 0002)
python manage.py migrate
```

No data migration / backfill is required: `StudyMaterial` is a brand-new table with no existing rows. `uploaded_by` and `klass` are both nullable, so even a manually inserted row needs no special handling. To repopulate demo data:

```bash
python manage.py seed_demo   # wipes + reseeds, now including study materials
```

## RBAC / permissions
| Action | Endpoint | Allowed roles | Permission classes |
|---|---|---|---|
| List documents | `GET /api/study-materials/` | Admin, Teacher, Student (all active) | `_perms()` → `[IsAuthenticated, IsActiveUser]` |
| Read one document | `GET /api/study-materials/{id}/` | Admin, Teacher, Student (all active) | `_perms()` |
| Create document | `POST /api/study-materials/` | Admin only | `_perms(IsAdmin)` |
| Update document | `PUT/PATCH /api/study-materials/{id}/` | Admin only | `_perms(IsAdmin)` |
| Delete document | `DELETE /api/study-materials/{id}/` | Admin only | `_perms(IsAdmin)` |

- **Reuse existing** permission classes only — `IsActiveUser` and `IsAdmin` from `core/permissions.py`, composed via the existing `_perms(*extra)` helper in `core/views.py`. **No new permission class is needed.**
- Suspended/inactive users are already rejected by `IsActiveUser` (do not re-implement) — a suspended student gets 403 even on the open `list`/`retrieve` actions.
- Anonymous requests get 401 from `IsAuthenticated` (the global default), consistent with every other endpoint.

## Acceptance criteria & smoke test
Checklist:
- [ ] `python manage.py makemigrations core` creates `0003_studymaterial.py`; `migrate` applies cleanly.
- [ ] `python manage.py seed_demo` runs and prints a `StudyMaterials:` count (5).
- [ ] `GET /api/study-materials/` returns a **bare JSON array** (not paginated) for an Admin, a Teacher, and a Student.
- [ ] `POST /api/study-materials/` succeeds (201) as Admin and is forbidden (403) as Teacher and as Student.
- [ ] Created document has `uploaded_by_id` equal to the admin's id (server-stamped, not client-supplied).
- [ ] `StudyMaterial` appears in `/admin/` and is editable there.
- [ ] `/api/docs/` shows the endpoints under a "study-materials" tag.

Seeded users (password `password123` for all):

```bash
# 1) Get tokens (login returns {access_token, refresh_token, token_type:"bearer"})
ADMIN=$(http --ignore-stdin POST :8000/api/auth/login \
  email=admin@english.app password=password123 | jq -r .access_token)
TEACHER=$(http --ignore-stdin POST :8000/api/auth/login \
  email=emma.teacher@english.app password=password123 | jq -r .access_token)
STUDENT=$(http --ignore-stdin POST :8000/api/auth/login \
  email=alice.student@english.app password=password123 | jq -r .access_token)

# 2) Read works for ALL roles (RBAC row 7) — expect 200 + a bare array
http --ignore-stdin GET :8000/api/study-materials/ "Authorization:Bearer $STUDENT"
http --ignore-stdin GET :8000/api/study-materials/ "Authorization:Bearer $TEACHER"
http --ignore-stdin GET :8000/api/study-materials/ "Authorization:Bearer $ADMIN"

# 3) Admin can create (RBAC row 6) — expect 201, uploaded_by_id == admin id
http --ignore-stdin POST :8000/api/study-materials/ "Authorization:Bearer $ADMIN" \
  title="Linking Words List" \
  file_url="https://mock.cdn/docs/linking-words.pdf" \
  description="Transitions for academic essays."

# 4) Teacher / Student CANNOT create — expect 403
http --ignore-stdin POST :8000/api/study-materials/ "Authorization:Bearer $TEACHER" \
  title="Should fail" file_url="https://mock.cdn/docs/x.pdf"
http --ignore-stdin POST :8000/api/study-materials/ "Authorization:Bearer $STUDENT" \
  title="Should fail" file_url="https://mock.cdn/docs/x.pdf"

# 5) Admin can update & delete — expect 200 then 204
http --ignore-stdin PATCH :8000/api/study-materials/1/ "Authorization:Bearer $ADMIN" \
  description="Updated description."
http --ignore-stdin DELETE :8000/api/study-materials/1/ "Authorization:Bearer $ADMIN"
```

curl equivalents (read + admin create):
```bash
curl -s http://localhost:8000/api/study-materials/ \
  -H "Authorization: Bearer $STUDENT"

curl -s -X POST http://localhost:8000/api/study-materials/ \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"title":"Linking Words List","file_url":"https://mock.cdn/docs/linking-words.pdf"}'
```

manage.py shell sanity check:
```python
from core.models import StudyMaterial, User
admin = User.objects.get(email="admin@english.app")
m = StudyMaterial.objects.create(
    title="Shell test", file_url="https://mock.cdn/docs/shell.pdf", uploaded_by=admin
)
assert m.uploaded_by_id == admin.id
assert StudyMaterial.objects.filter(klass__isnull=True).exists()  # global docs exist
```

## Deferred / out of scope
- **Real file upload.** `file_url` is a mock string only — there is no `FileField`, storage backend, presigned-URL flow, or MIME validation. To make it real later, add a `FileField`/`upload_to` (or an S3/presigned-URL endpoint) while keeping `file_url` as the serialized output so the wire contract is unchanged.
- **"Save" as a per-user bookmark (RBAC row 7, the "Save" half).** For this MVP, "Save Study Documents" is satisfied by **read access** — a user can open/download any document; there is no per-user saved/favourites state. To grow it, add a `SavedMaterial` link table (`user` FK + `study_material` FK, `unique_together`, `db_table="saved_materials_lnk"`) plus a `POST/DELETE /api/study-materials/{id}/save/` detail action and a `GET /api/study-materials/saved/` list — additive, no change to the contract defined here.
- **Versioning / categories / tags.** docs.md's `study_materials` is a flat catalogue; no `category`, `tag`, or `version` fields. Add later as additive columns if the spec grows.
- **Owned by other files.** Field/enum edits to existing models (Exercise `content_text`/`created_by`, Submission `status`, Feedback `is_ai_generated`, NOT-NULL tightening) belong to **01**; the other two brand-new standalone models (`AiModel` → 06, `SystemLog` → 07) are out of scope here.

# 01 — Model foundations: field & enum deltas, migrations, seed conventions

**Status:** Proposed · **Depends on:** none (foundation) · **Spec refs:** docs.md §4 (DBML: `exercises`, `submissions`, `evaluations`, `classes`, `assignments`), §5 (Data Dictionary tables 3, 5, 7, 8, 10), §6 directives 1 & 2; RBAC matrix rows 8 (Create & Assign Homework), 10 (Grade Submissions), 17/18 (AI Feedback context); UC-06/07, UC-08, UC-13, UC-14/15, UC-17/18.
**Estimated blast radius:** `core/models.py`, `core/serializers.py`, `core/admin.py`, `core/management/commands/seed_demo.py`, one new migration `core/migrations/0003_*.py`. (NOT touched here: `core/views.py`, `core/permissions.py`, `config/urls.py`, `config/settings.py` — those belong to feature files 02/04/06.)

---

## Goal
Align the **existing** domain models with the docs.md §4 DBML / §5 data dictionary by adding the small set of fields and one enum the spec requires but the current schema lacks: a reading-passage body on `Exercise`, explicit `Exercise` authorship, a grading-lifecycle `status` on `Submission`, and an `is_ai_generated` flag on `Feedback` (= docs.md `evaluations`). It also tightens two FKs that docs.md marks `not null` (`classes.teacher_id`, `assignments.exercise_id`/`class_id`). This file is the **single source of truth for model shape**; files 02–07 build on these fields without redefining them. The changes satisfy the data-structure half of UC-06/07 (reading passages), UC-08/UC-14 (submission lifecycle), and UC-17/18 (AI-vs-human grading attribution); the *behavior* that uses them lives in files 04 and 06.

## Why (gap)

| docs.md requirement | Current code (real symbols) | Gap |
|---|---|---|
| `exercises.content_text text [note: 'For reading passages']` (§4, §5 Table 5); §6 directive 2 ("If Reading… require `content_text`") | `core/models.py` `Exercise` has only `prompt_text` (TextField) + `audio_prompt_url`. The READING docstring even says "stimulus = prompt_text (passage)" — so passage and instructions are conflated into one field. | No dedicated passage field. A reading exercise cannot carry both a long passage *and* separate instructions. |
| `exercises.created_by integer [ref: > users.id]` (§4, §5 Table 5) | `Exercise` has **no** `created_by`. Ownership is only derivable as `exercise.module.created_by_id` — see `ModuleViewSet.exercises()` in `core/views.py` (`module.created_by_id != user.id`). | Module-less exercises (`Exercise.module` is `null=True`) have **no** owner at all. File 04's teacher-grading-inbox needs a direct owner; today it cannot scope module-less exercises to a teacher. |
| `submissions.status submission_status [default: 'Pending']`, `Enum submission_status { Pending, Graded, AI_Graded }` (§4) | `Submission` has `auto_score` but no lifecycle field; `SubmissionType` enum exists but there is no `SubmissionStatus`. | No way to express "awaiting grading / graded by teacher / graded by AI". Teacher inbox (file 04) and AI grading (file 06) have no status to filter or transition. |
| `evaluations.is_ai_generated boolean [default: false]` (§4, §5 Table 10); §6 directive 1 ("Always inspect `is_ai_generated`… fallback to system AI attribution") | docs.md `evaluations` == existing `Feedback` (`core/models.py`). `Feedback` has `reviewer` (null=True, SET_NULL) but **no** `is_ai_generated`. | Cannot distinguish AI feedback from teacher feedback. `reviewer_id IS NULL` alone is ambiguous (could be a deleted teacher). docs.md §6 directive 1 explicitly forbids inferring AI-attribution from a null evaluator. |
| `classes.teacher_id integer [not null]` (§4, §5 Table 3) | `Class.teacher = ForeignKey(User, null=True, blank=True, on_delete=SET_NULL)` | Currently nullable. Spec says NOT NULL. |
| `assignments.exercise_id [not null]`, `assignments.class_id [not null]` (§4, §5 Table 7) | `Assignment.klass` and `Assignment.exercise` are both `null=True, blank=True, on_delete=SET_NULL`. | Currently nullable. Spec says both NOT NULL. |

Everything else in the §4 DBML is already satisfied (often more richly) by the current schema — see the full mapping table below.

---

## docs.md DBML → current Django mapping (authoritative)

Legend: **MATCH** = same shape; **RENAME-AVOIDED** = docs name mapped onto an existing name to preserve the wire/table contract; **RICHER-THAN-SPEC** = current code already exceeds the spec; **DELTA(here)** = changed by *this* file; **NEW(file)** = a brand-new model owned by another upgrade file.

| docs.md table.field | Current model.field (`db_table`) | Status |
|---|---|---|
| `users` (whole table) | `User` (`users`) | MATCH |
| `users.password_hash` | `User.password` (Django `AbstractBaseUser`) | RENAME-AVOIDED |
| `users.profile_picture` | `User.avatar_url` | RENAME-AVOIDED |
| `users.is_active` | `User.status` (`UserStatus` enum) + `User.is_active` (auth plumbing) | RICHER-THAN-SPEC (3-state active/suspended/inactive vs bool; enforced by `IsActiveUser`) |
| `Enum user_role {Admin,Teacher,Student}` | `UserRole` TextChoices | MATCH |
| `Enum exercise_type {Reading,Listening,Writing,Speaking}` | `ExerciseType` TextChoices (also adds `QUIZ`) | RICHER-THAN-SPEC |
| `Enum submission_status {Pending,Graded,AI_Graded}` | **NEW `SubmissionStatus` TextChoices** | DELTA(here) |
| `classes` | `Class` (`classes`) | MATCH |
| `classes.teacher_id [not null]` | `Class.teacher` (FK→User) | DELTA(here): make NOT NULL |
| `class_students_lnk` | `ClassStudent` (`class_students_lnk`); `class_id` ← `klass` | MATCH (RENAME-AVOIDED on `klass`→`class_id` wire) |
| `study_materials` | — | NEW(file 03) `StudyMaterial` |
| `exercises` | `Exercise` (`exercises`) | MATCH + DELTA(here) |
| `exercises.instructions [not null]` | `Exercise.prompt_text` (TextField, NOT NULL) | RENAME-AVOIDED (decision below: `prompt_text` *is* `instructions`) |
| `exercises.content_text [note: reading passages]` | **NEW `Exercise.content_text`** | DELTA(here) |
| `exercises.media_url` | `Exercise.audio_prompt_url` | RENAME-AVOIDED |
| `exercises.created_by` | **NEW `Exercise.created_by`** | DELTA(here) |
| `exercise_questions` | `Question` (`questions`) + `QuestionOption` (`question_options`) | RICHER-THAN-SPEC (options + multi-answer key vs a single `correct_answer` text) |
| `exercise_questions.correct_answer` | `QuestionOption.is_correct` set per option | RICHER-THAN-SPEC |
| `assignments` | `Assignment` (`assignments`) | MATCH + DELTA(here) |
| `assignments.exercise_id [not null]`, `assignments.class_id [not null]` | `Assignment.exercise`, `Assignment.klass` | DELTA(here): make both NOT NULL |
| `submissions` | `Submission` (`submissions`) | MATCH + DELTA(here) |
| `submissions.submission_text` | `Submission.writing_text` | RENAME-AVOIDED |
| `submissions.audio_recording_url` | `Submission.audio_recording_url` | MATCH |
| `submissions.status` | **NEW `Submission.status`** | DELTA(here) |
| `submission_answers` | `Submission.answers` (JSONField) + `Submission.grade()` auto-scoring | RICHER-THAN-SPEC (JSON map + computed `auto_score` vs per-row table) |
| `submission_answers.is_correct` | computed inside `Submission.grade()` against `QuestionOption.is_correct` | RICHER-THAN-SPEC |
| `evaluations` | `Feedback` (`feedback`) | RENAME-AVOIDED + DELTA(here) |
| `evaluations.evaluator_id [null if AI]` | `Feedback.reviewer` (null=True, SET_NULL) | MATCH |
| `evaluations.remarks` | `Feedback.comments` | RENAME-AVOIDED |
| `evaluations.is_ai_generated` | **NEW `Feedback.is_ai_generated`** | DELTA(here) |
| `ai_models` | — | NEW(file 06) `AiModel` |
| `system_logs` | — | NEW(file 07) `SystemLog` |

### Decisions recorded here (referenced by other files)

1. **`prompt_text` IS `instructions`.** Do **not** add a separate `instructions` column. `Exercise.prompt_text` is already `not null` and already carries the task instructions in every seeded row (e.g. "Record yourself ordering a coffee…"). docs.md `exercises.instructions [not null]` maps cleanly onto it. Adding `content_text` for the *passage* removes the only reason a second field would be needed. This keeps the wire contract (`prompt_text`) intact for the Next.js client.
2. **`content_text` is the reading passage; `audio_prompt_url` is `media_url`.** Per §6 directive 2: Reading/Writing read `content_text`/`prompt_text`; Listening/Speaking read `audio_prompt_url` (= `media_url`). `content_text` is **nullable** — only receptive Reading exercises populate it.
3. **`created_by` is `null=True, on_delete=SET_NULL`** (not NOT NULL — docs.md leaves it `ref` without `not null`). This matches the existing `LearningModule.created_by` idiom exactly and survives user deletion. The view layer (file 04) sets it on create.
4. **`assignments` FKs → NOT NULL is safe.** docs.md self-practice (UC-12 "Autonomous AI Skill Practice") is modelled on the **Submission** side: `Submission.assignment` stays `null=True` (the seed creates assignment-less submissions, e.g. `assignment=None, exercise=exercises[3]`). An `Assignment` row, by definition, always targets a class + exercise, so tightening `Assignment.klass`/`Assignment.exercise` to NOT NULL loses no capability. Justified: do it.

---

## Changes (file by file)

### core/models.py

**(a) New enum `SubmissionStatus`** — add alongside the other TextChoices enums (after `SubmissionType`, before the `User` section):

```python
class SubmissionStatus(models.TextChoices):
    # docs.md Enum submission_status. Lifecycle of grading, not the skill type.
    PENDING = "pending", "Pending"        # submitted, not yet graded
    GRADED = "graded", "Graded"           # a human teacher left Feedback
    AI_GRADED = "ai_graded", "AI Graded"  # an AI evaluator left Feedback
    # NOTE: transition LOGIC lives in file 04 (teacher grade) / file 06 (AI grade).
```

**(b) `Exercise`: add `content_text` + `created_by`.** The block becomes:

```python
class Exercise(models.Model):
    module = models.ForeignKey(
        LearningModule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="exercises",
    )
    title = models.CharField(max_length=255)
    exercise_type = models.CharField(max_length=20, choices=ExerciseType.choices)
    # docs.md exercises.instructions — kept as prompt_text (wire contract).
    prompt_text = models.TextField()
    # docs.md exercises.content_text — reading passage body (receptive Reading).
    content_text = models.TextField(null=True, blank=True)
    # docs.md exercises.media_url — listening/speaking audio target.
    audio_prompt_url = models.CharField(max_length=500, null=True, blank=True)
    # docs.md exercises.created_by — direct authorship (module may be null).
    # Set on create in the view layer (file 04). Mirrors LearningModule.created_by.
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="exercises_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exercises"

    def __str__(self):
        return self.title
```

**(c) `Submission`: add `status`.** Add the field (default `PENDING`) right after `auto_score`:

```python
    auto_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    # docs.md submissions.status — grading lifecycle. Transitions: file 04 / 06.
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
```

**(d) `Feedback`: add `is_ai_generated`.** Add after `comments`:

```python
    comments = models.TextField(null=True, blank=True)
    # docs.md evaluations.is_ai_generated. Per §6 directive 1, AI attribution is
    # read from THIS flag, never inferred from reviewer_id being null. Semantics: 04/06.
    is_ai_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

**(e) `Class.teacher` → NOT NULL.** Drop `null=True, blank=True`. Because the FK can no longer be `SET_NULL` on a NOT NULL column, change `on_delete` to `PROTECT` (do not allow deleting a teacher who still owns classes — reassign first):

```python
class Class(models.Model):
    class_name = models.CharField(max_length=100)
    # docs.md classes.teacher_id [not null]. PROTECT: a teacher with classes
    # cannot be deleted until their classes are reassigned (see file 02, which
    # also locks teacher_id against post-creation reassignment).
    teacher = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="classes_taught",
    )
    academic_year = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "classes"
        verbose_name_plural = "classes"

    def __str__(self):
        return self.class_name
```

> **Keep `Meta` intact.** The shown block restores the existing `db_table = "classes"` + `verbose_name_plural = "classes"` (`core/models.py:115-120`) — only the `teacher` field line changes (drop `null=True, blank=True`, switch `SET_NULL`→`PROTECT`). Do **not** drop the `Meta`/`__str__` when editing.

> Cross-ref: **file 02** locks `teacher_id` against reassignment after creation (it stays immutable post-create); this file only makes it required + non-orphaning at the DB level.

> **Serializer contract caveat (handoff to file 02).** Today `ClassSerializer.teacher_id` is `PrimaryKeyRelatedField(source="teacher", required=False, allow_null=True)` (`core/serializers.py:73-76`) and `ClassViewSet.perform_create` forces `teacher=self.request.user` for teachers but leaves it caller-supplied for admins (`core/views.py:187-191`). After this NOT-NULL change, an **admin** POST/PUT that omits `teacher_id` will pass serializer validation (because it is still `required=False, allow_null=True`) and then fail at the DB with `IntegrityError: null value in column "teacher_id"`. This file deliberately does **not** touch `ClassSerializer` (serializer/view hardening is file 02's scope); file 02 must drop `allow_null=True` (and make `teacher_id` required for the admin path) when it locks teacher reassignment. Until then, the safe usage is: teachers always get themselves as teacher (already enforced), and admins must send `teacher_id`.

**(f) `Assignment.klass` and `Assignment.exercise` → NOT NULL.** Same reasoning — NOT NULL means `SET_NULL` is invalid; use `CASCADE` (deleting a class or exercise removes its assignments, consistent with `Submission.exercise` already using CASCADE):

```python
class Assignment(models.Model):
    # docs.md assignments.class_id / exercise_id [not null]. Self-practice is
    # modelled on Submission.assignment (nullable), NOT on Assignment, so these
    # can safely be required. CASCADE mirrors Submission.exercise.
    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="assignments",
    )
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="assignments",
    )
    assigned_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assignments_made",
    )
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

> `assigned_by` stays nullable/`SET_NULL` — docs.md marks it `not null`, but the existing `ClassViewSet.assignments()` already always sets `assigned_by=request.user` on create, and keeping it `SET_NULL` avoids orphaning assignments when a teacher account is removed. This is a deliberate RICHER-THAN-SPEC retention, not a gap.

### core/serializers.py

**(a) `ExerciseSerializer`: surface `content_text` (read/write) and `created_by` (read-only).** `created_by` mirrors the existing `LearningModuleSerializer.created_by` pattern (`PrimaryKeyRelatedField(read_only=True)`):

```python
class ExerciseSerializer(serializers.ModelSerializer):
    module_id = serializers.PrimaryKeyRelatedField(
        source="module", queryset=LearningModule.objects.all(),
        required=False, allow_null=True,
    )
    # Authorship — set by the view on create (file 04), read-only on the wire.
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    # Present only on receptive exercises (reading/listening/quiz).
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Exercise
        fields = [
            "id", "module_id", "title", "exercise_type",
            "prompt_text", "content_text", "audio_prompt_url",
            "created_by", "questions", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]
```

> Contract note: this is **additive** — `content_text` and `created_by` are new keys; no existing key renamed or removed. Existing clients that POST without `content_text` still work (nullable, not required).

**(b) `SubmissionSerializer`: surface `status` read-only.** Add to `fields` and `read_only_fields`:

```python
    class Meta:
        model = Submission
        fields = [
            "id", "exercise_id", "assignment_id", "submission_type",
            "writing_text", "audio_recording_url",
            "answers", "auto_score", "status", "student_id", "submitted_at",
        ]
        read_only_fields = ["id", "student_id", "auto_score", "status", "submitted_at"]
```

> `status` is read-only on the wire — students never set it; it is mutated server-side by grading actions (file 04 / 06).

**(c) `FeedbackSerializer`: surface `is_ai_generated` read-only.** Reviewers don't self-declare AI; the server sets it (teacher path → `False`, AI path → `True`, file 06):

```python
    class Meta:
        model = Feedback
        fields = [
            "id", "submission_id", "reviewer_id",
            "score", "comments", "is_ai_generated", "created_at",
        ]
        read_only_fields = ["id", "reviewer_id", "is_ai_generated", "created_at"]
```

> No new import needed: `Exercise`, `Submission`, `Feedback`, `LearningModule` are already imported. `SubmissionStatus` does **not** need importing into serializers (the field is read-only and rendered as its stored varchar).

### core/admin.py

Register the new fields so the /admin/ console (the whole point of the migration) shows them. Edit the three existing `ModelAdmin` classes:

```python
@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "exercise_type", "module", "created_by", "created_at")
    list_filter = ("exercise_type",)
    search_fields = ("title", "prompt_text", "content_text")
    raw_id_fields = ("module", "created_by")
    inlines = [QuestionInline]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "exercise", "submission_type", "status", "auto_score", "submitted_at")
    list_filter = ("submission_type", "status")
    raw_id_fields = ("assignment", "exercise", "student")
    inlines = [FeedbackInline]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "reviewer", "score", "is_ai_generated", "created_at")
    list_filter = ("is_ai_generated",)
    raw_id_fields = ("submission", "reviewer")
```

> The `ClassAdmin` (`raw_id_fields = ("teacher",)`) and `AssignmentAdmin` need no edit for the NOT-NULL change — Django renders required raw-id FKs the same way; the form just won't accept blank.

### core/management/commands/seed_demo.py

The seed already (a) sets a teacher on every `Class.objects.create(...)`, (b) sets `klass=` and `exercise=` on every `Assignment.objects.create(...)`, and (c) creates `assignment=None` submissions only. So the **NOT-NULL tightenings need no seed change** — the existing data is already conformant. Add the following:

**(a) Import `SubmissionStatus`** (extend the existing `from core.models import (...)` tuple):

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
    SubmissionStatus,
    SubmissionType,
    User,
    UserRole,
    UserStatus,
)
```

**(b) Seed `Exercise.created_by` + a couple of reading passages.** In the exercises block, set `created_by` to the same teacher who owns the parent module (`modules[i].created_by`), and add one or two READING exercises that exercise `content_text`. The module owners are: `modules[0]`=`teachers[1]`, `[1]`=`teachers[0]`, `[2]`=`teachers[0]`, `[3]`=`teachers[1]`, `[4]`=`teachers[2]`. Append these to the `exercises = [...]` list (and add `created_by=` to the 15 pre-existing rows):

```python
            # NEW: a reading-comprehension exercise that uses content_text (passage)
            # + prompt_text (instructions). created_by = module owner.
            Exercise.objects.create(module=modules[1], title="Reading: A Day at the Market",
                exercise_type=ExerciseType.READING,
                prompt_text="Read the passage, then answer the comprehension questions.",
                content_text=(
                    "Every Saturday, Mai visits the local market near her home. She buys "
                    "fresh vegetables, ripe mangoes, and a loaf of warm bread. The vendors "
                    "know her by name and often save the best fruit for her. By nine o'clock "
                    "the market is crowded, so Mai always arrives early to avoid the rush."
                ),
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[4], title="Reading: The Future of Work",
                exercise_type=ExerciseType.READING,
                prompt_text="Read the passage and answer the questions that follow.",
                content_text=(
                    "Remote work, once a rare privilege, has become a permanent fixture for "
                    "millions. Proponents cite flexibility and the elimination of commutes, "
                    "while critics warn of blurred boundaries between home and office. As "
                    "companies experiment with hybrid models, the definition of a 'workplace' "
                    "continues to evolve."
                ),
                created_by=teachers[2]),
```

For the pre-existing 15 exercises, add `created_by=teachers[<module owner index>]` to each `Exercise.objects.create(...)` (`modules[0]`→`teachers[1]`, `modules[1]`→`teachers[0]`, `modules[2]`→`teachers[0]`, `modules[3]`→`teachers[1]`, `modules[4]`→`teachers[2]`). If you prefer one line, after building the list you may instead backfill: `for ex in exercises: ex.created_by = ex.module.created_by; ex.save(update_fields=["created_by"])` — but inline `created_by=` is clearer and matches the file's style.

> If you add the two READING exercises, they get list indices 15 and 16; the existing feedback/submission indexing (`submissions[0]`, `assignments[2]`, etc.) is unchanged because they are appended at the **end** of `exercises` and not referenced by any later block. Optionally add `Question`/`QuestionOption` rows to make them auto-gradeable — that is **deferred** (questions live with the comprehension feature, file 04/03 scope), so leaving them question-less (un-auto-scorable) is fine for the seed.

**(c) `Submission.status` and `Feedback.is_ai_generated` — leave at model defaults, with one illustrative override.** New submissions default to `PENDING` and new feedback to `is_ai_generated=False` automatically, so no change is strictly required. To make the demo show the lifecycle, set `status=SubmissionStatus.GRADED` on the submissions that receive seeded `Feedback`. The cleanest spot is right after the `Feedback.objects.bulk_create([...])` block — flip the graded submissions in bulk:

```python
        # NEW: reflect grading lifecycle — submissions that got teacher Feedback
        # are GRADED (human, not AI). is_ai_generated stays False (default) for
        # all seeded feedback; the AI path is exercised by file 06's demo, not here.
        graded_idx = [0, 1, 2, 4, 5, 9, 10, 13, 14, 17, 18, 19, 21, 24]
        for i in graded_idx:
            submissions[i].status = SubmissionStatus.GRADED
            submissions[i].save(update_fields=["status"])
```

> `graded_idx` matches the submission indices referenced in the existing `Feedback.objects.bulk_create([...])` block exactly — keep them in sync if you edit feedback. All other submissions remain `PENDING`, which is exactly what file 04's teacher inbox will surface as "to grade".

---

## Migrations

```bash
# from repo root, with the project venv active
python manage.py makemigrations core   # generates 0003_*.py
python manage.py migrate
```

**Why no `ALTER TYPE` dance:** every enum in this repo is a Django `TextChoices` stored as a plain `varchar(20)` (see `Exercise.exercise_type`, `Submission.submission_type` in `0001`/`0002`). `SubmissionStatus` is identical — adding it produces a `migrations.AddField` of a `CharField(choices=..., default="pending")`, **not** a native PostgreSQL `CREATE TYPE` / `ALTER TYPE`. There is no enum type to alter; choices live in Python and are enforced at the application/validation layer. (REFACTOR_PLAN.md's native-enum migration procedure describes the OLD SQLAlchemy app and does **not** apply here.)

**What `0003` will contain (expected operations):**
- `AddField Exercise.content_text` (TextField null) — additive, no data migration.
- `AddField Exercise.created_by` (FK SET_NULL null) — additive, no data migration.
- `AddField Submission.status` (CharField, default `"pending"`) — Django backfills every existing row to `pending`; no manual step.
- `AddField Feedback.is_ai_generated` (BooleanField default False) — backfills to `False`.
- `AlterField Class.teacher` (now NOT NULL, `on_delete=PROTECT`).
- `AlterField Assignment.klass` and `Assignment.exercise` (now NOT NULL, `on_delete=CASCADE`).

**Data-migration / backfill notes for the NOT-NULL alters (non-demo DBs):**
- `Class.teacher_id`: the seed always sets a teacher, so a freshly-seeded DB migrates cleanly. For a **pre-existing** database that may hold classes with `teacher_id IS NULL`, run a backfill **before** `migrate`, e.g. in `manage.py shell`:
  ```python
  from core.models import Class, User, UserRole
  fallback = User.objects.filter(role=UserRole.TEACHER).order_by("id").first()
  Class.objects.filter(teacher__isnull=True).update(teacher=fallback)  # then migrate
  ```
  If you skip this on a dirty DB, `migrate` raises `IntegrityError: null value in column "teacher_id"`.
- `Assignment.klass_id` / `exercise_id`: same shape. The seed never creates null-FK assignments, so demo DBs are safe. For a dirty DB, either backfill or delete orphan assignments first:
  ```python
  from core.models import Assignment
  Assignment.objects.filter(klass__isnull=True).delete()
  Assignment.objects.filter(exercise__isnull=True).delete()  # then migrate
  ```
- After any schema change on a demo box, the canonical reset is just: `python manage.py migrate && python manage.py seed_demo` (the seed wipes children-first and reinserts conformant rows).

---

## RBAC / permissions
This file changes **only model shape, serializers, admin, and seed** — it does not add or alter any endpoint or permission class. No change to `core/permissions.py`, no change to `config/settings.py` `DEFAULT_PERMISSION_CLASSES`. The existing stack still applies wherever these models are exposed:
- `IsActiveUser` (suspended/inactive gate) — **already exists**, unchanged; do not re-implement.
- The new fields' *write paths* are governed by the views that own them in later files: `Exercise.created_by` is set in `ModuleViewSet.exercises()` / the exercise-create path (file 04) under `IsTeacherOrAdmin`; `Submission.status` and `Feedback.is_ai_generated` are mutated by grading actions under `IsTeacherOrAdmin` (file 04, human) and the admin-gated AI path (file 06). On the wire here, `created_by`, `status`, and `is_ai_generated` are all **read-only**, so no role can set them via these serializers directly.
- The `Class.teacher_id` NOT-NULL + `PROTECT` change reinforces RBAC matrix row 15 (Classroom Group Management, Admin/Teacher) by guaranteeing every class has an owning teacher for ownership checks (`ClassViewSet.update()` already does `obj.teacher_id != user.id`).

---

## Acceptance criteria & smoke test

Checklist:
- [ ] `python manage.py makemigrations core` creates exactly one new migration (`0003_*`) with the six operations listed above; `python manage.py migrate` applies clean.
- [ ] `python manage.py seed_demo` runs without error and reports the same counts (plus 2 extra exercises if the reading rows were added → Exercises: 17).
- [ ] `Exercise` rows have non-null `created_by`; the two READING rows have non-null `content_text`.
- [ ] All seeded submissions are `pending` except the 14 with feedback, which are `graded`.
- [ ] All seeded feedback has `is_ai_generated = False`.
- [ ] `GET /api/exercises/{id}/` returns `content_text` and `created_by` keys; `GET /api/submissions/me/` returns a `status` key; `GET /api/submissions/{id}/feedback/` returns `is_ai_generated`.
- [ ] No existing wire key was renamed/removed (login still returns `{access_token, refresh_token, token_type:"bearer"}`; FK keys still `*_id`; `class_id` still maps to `klass`).

Shell verification (after migrate + seed):

```bash
python manage.py shell -c "
from core.models import Exercise, Submission, Feedback, SubmissionStatus
print('exercises w/ created_by:', Exercise.objects.exclude(created_by=None).count(), '/', Exercise.objects.count())
print('exercises w/ content_text:', Exercise.objects.exclude(content_text=None).exclude(content_text='').count())
print('submissions graded:', Submission.objects.filter(status=SubmissionStatus.GRADED).count())
print('submissions pending:', Submission.objects.filter(status=SubmissionStatus.PENDING).count())
print('feedback ai:', Feedback.objects.filter(is_ai_generated=True).count(), 'human:', Feedback.objects.filter(is_ai_generated=False).count())
"
```

API smoke (httpie; password `password123`). Log in as a teacher to read an exercise with the new fields, and as a student to see `status`:

```bash
# teacher token
TOKEN=$(http --ignore-stdin POST :8000/api/auth/login email=emma.teacher@english.app password=password123 | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# an exercise now carries content_text + created_by
http --ignore-stdin GET :8000/api/exercises/16/ "Authorization:Bearer $TOKEN"
#   -> JSON includes "content_text": "...", "created_by": <teacher id>, "prompt_text": "..."

# student sees submission.status
STOKEN=$(http --ignore-stdin POST :8000/api/auth/login email=alice.student@english.app password=password123 | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
http --ignore-stdin GET :8000/api/submissions/me/ "Authorization:Bearer $STOKEN"
#   -> each item includes "status": "graded" | "pending"

# feedback on a submission shows is_ai_generated
http --ignore-stdin GET :8000/api/submissions/1/feedback/ "Authorization:Bearer $TOKEN"
#   -> [{ ..., "is_ai_generated": false }]
```

curl equivalent for the exercise read:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/exercises/16/ | python -m json.tool
```

(Adjust the exercise id `16` to whichever READING row exists; `GET /api/modules/{id}/exercises/` lists them.)

---

## Deferred / out of scope
- **The three brand-new standalone models** — `StudyMaterial` (file 03), `AiModel` (file 06), `SystemLog` (file 07). This file does not define them; the mapping table only points to their owning files.
- **Status transition logic** — *when* a `Submission` moves Pending→Graded / Pending→AI_Graded, and *who* may trigger it, lives in file 04 (teacher grading) and file 06 (AI grading). Here `status` is just a field with a default; the seed sets a sane illustrative value but adds no transition code.
- **`is_ai_generated` semantics & the AI grading path** — file 04 (human feedback sets it `False`) and file 06 (AI evaluator sets it `True`, possibly with `reviewer=None`). §6 directive 1 attribution logic is enforced there.
- **`content_text` validation per `exercise_type`** (§6 directive 2: "If Reading… require `content_text`") — model field is nullable here; conditional validation belongs in the exercise-create serializer/view (file 04). This file only adds the column.
- **Questions/options for the seeded reading exercises** — left question-less (not auto-scorable) to avoid overreaching into the comprehension feature's seed scope; add `Question`/`QuestionOption` rows when file 04/03 builds out reading comprehension.
- **`exercises.instructions` as a distinct column** — explicitly rejected (decision 1): `prompt_text` serves it; revisit only if a future requirement needs passage and instructions *and* a third free-text field simultaneously.

# Model Refactor Plan — align to Student / Teacher / Admin use-case spec

**Scope:** SQLAlchemy models in [app/models.py](app/models.py), plus the schema / router / seed
changes required to make the model changes actually work. Demo MVP rules from
[todo.md](todo.md) hold: Auth + basic CRUD only, mock file URLs as strings, no AI grading.

## Decisions locked

| Fork | Decision |
|---|---|
| Reading/Listening modeling | **Free-text (MVP)** — enum values only, reuse `Submission` text/audio columns. No `Question`/`Option`/`Answer` tables. |
| Teacher class creation | **Admin-only** — new spec wins over the old brief; teacher manages, does not create. |
| Admin "Monitor all app activity" | **Aggregate read-only endpoints** over existing `created_at`/`submitted_at`/`joined_at`. No `ActivityLog` table. |
| Security gaps | **Included** in this refactor. |

---

## 1. Model changes ([app/models.py](app/models.py))

### 1.1 Enums — add reading + listening to BOTH (the actual blocker)

`ExerciseType` alone is useless: `Submission.submission_type` is `nullable=False` and
`SubmissionType` only has `{writing, speaking}`, so a reading/listening submission has no
valid type to persist.

```python
class ExerciseType(str, enum.Enum):
    writing = "writing"
    speaking = "speaking"
    quiz = "quiz"
    reading = "reading"      # NEW
    listening = "listening"  # NEW


class SubmissionType(str, enum.Enum):
    writing = "writing"
    speaking = "speaking"
    reading = "reading"      # NEW
    listening = "listening"  # NEW
```

### 1.2 Exercise — add a reading passage; reuse existing audio column for listening

`prompt_text` stays `NOT NULL` (holds the question/instructions) → no schema/router ripple.
Reading passage goes in a new nullable column; listening audio reuses `audio_prompt_url`.

```python
class Exercise(Base):
    ...
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)        # unchanged
    passage_text: Mapped[str | None] = mapped_column(Text, nullable=True) # NEW — reading
    audio_prompt_url: Mapped[str | None] = ...                            # unchanged — listening
```

Student answers for reading/listening land in the existing `Submission.writing_text`
(free text / JSON string). No `Submission` structural change.

### 1.3 New model — Document (Teacher "Manage Documents")

Minimal per mock-URL rule. No type enum / visibility / DB-stored content for MVP.

```python
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)   # mock string URL
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    uploader: Mapped["User"] = relationship(back_populates="documents")
    class_: Mapped["Class | None"] = relationship(back_populates="documents")
```

Add reverse relationships:
- `User.documents: Mapped[list["Document"]] = relationship(back_populates="uploader")`
- `Class.documents: Mapped[list["Document"]] = relationship(back_populates="class_")`

### 1.4 Class — teacher_id becomes required (admin-only creation)

Admin must assign a teacher at creation, else classes orphan and the teacher-scoped
`list_classes` filter silently drops them.

```python
class Class(Base):
    ...
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)  # was nullable
    teacher: Mapped["User"] = relationship(back_populates="classes_taught", foreign_keys=[teacher_id])
```

### Model deltas at a glance
- `ExerciseType` += `reading`, `listening`
- `SubmissionType` += `reading`, `listening`
- `Exercise` += `passage_text`
- **+ `Document`** model
- `Class.teacher_id` nullable → NOT NULL
- ~~`Question` / `QuestionOption` / `StudentAnswer`~~ — deferred
- ~~`ActivityLog`~~ — deferred

---

## 2. Migration reality — native PG enums + no Alembic

The enums are native Postgres types (`SAEnum(..., name="exercise_type")`). On an already-seeded
DB, `Base.metadata.create_all()` ([main.py:11](app/main.py#L11)) will **NOT** add the new enum
values — inserting `type=reading` throws `invalid input value for enum`. There is no Alembic.

**Apply procedure for this refactor (demo):** drop everything (tables **and** enum types), recreate, reseed.

Change the wipe in [app/seed.py](app/seed.py) from `TRUNCATE` to a full drop so the enum types
are recreated with the new values:

```python
def seed():
    Base.metadata.drop_all(bind=engine)    # drops tables AND the SAEnum types
    Base.metadata.create_all(bind=engine)  # recreates with reading/listening
    db = SessionLocal()
    ...
```

(If you keep `TRUNCATE`: the enum type survives truncate, so you'd additionally need a one-time
`ALTER TYPE exercise_type ADD VALUE 'reading'; ... 'listening';` and the same for
`submission_type`. The drop_all path is simpler for a throwaway demo.)

Also add `"documents"` to `TABLES_IN_FK_ORDER` (children-first), before `classes`/`users`:
```python
TABLES_IN_FK_ORDER = [
    "feedback", "submissions", "assignments", "student_module_progress",
    "exercises", "learning_modules", "lesson_plans",
    "documents",            # NEW — depends on users + classes
    "class_students_lnk", "classes", "users",
]
```

---

## 3. Schema + router changes (models are inert without these)

### 3.1 Exercise schema ([app/schemas.py](app/schemas.py))
- `ExerciseBase` += `passage_text: str | None = None`.
- `ExerciseOut` += `passage_text`.

### 3.2 Teacher cannot create classes ([app/routers/classes_router.py](app/routers/classes_router.py))
- `create_class`: `require_roles(UserRole.admin)` (drop teacher).
- **Delete the dead teacher auto-assign branch** ([classes_router.py:22-24](app/routers/classes_router.py#L22-L24)).
- Validate `payload.teacher_id` is present AND targets a `role == teacher` user; else 400.
- `update_class`: strip `teacher_id` from teacher-writable fields (mirror the `status` pattern in
  [users_router.py:25-26](app/routers/users_router.py#L25-L26)) — else the create restriction is bypassable.

### 3.3 Documents — new router + schemas
- `DocumentCreate {title, file_url, description?, class_id?}`, `DocumentOut`.
- `documents_router.py`: teacher/admin create; owner+admin update/delete; list scoped (own +
  class-shared); register in [app/main.py](app/main.py).

### 3.4 Teacher "View Student Submissions" — real inbox
Current [list_exercise_submissions](app/routers/modules_router.py#L174) is per-exercise only.
Add `GET /modules/submissions/inbox` for teacher: submissions across the teacher's owned
exercises, with a `graded`/`ungraded` flag derived from `Feedback` existence.
⚠️ Include self-practice submissions (`assignment_id IS NULL`) — joining only through
`Assignment.class_id` silently drops them.

### 3.5 Dashboards — aggregate read-only endpoints (no model)
- Student `GET /dashboard`: due assignments, in-progress modules, recent feedback.
- Teacher `GET /dashboard`: class count, enrolled-student counts, ungraded-submission count.
- Admin `GET /dashboard` + `GET /admin/activity`: user counts by role, totals, recent rows
  across users/submissions/feedback ordered by their existing timestamp columns.

---

## 4. Security fixes (folded in)
- **Feedback leak** — [list_feedback](app/routers/modules_router.py#L207-L213) has no auth check;
  any student reads any feedback by guessing submission IDs. Guard: allow submission owner,
  the reviewer, the class teacher, or admin only.
- **Role escalation** — [register](app/routers/auth_router.py#L14-L29) accepts arbitrary `role`.
  Restrict to `{student, teacher}`; admin is provisioned, not self-signup
  ([UI_FLOW_BRIEF.md:77](UI_FLOW_BRIEF.md)).
- **Suspended not enforced** — `UserStatus.suspended` is never checked; brief's J6
  ("suspended → 403") is fiction. Reject suspended users in `get_current_user` ([app/auth.py](app/auth.py)).

---

## 5. Seed updates ([app/seed.py](app/seed.py))
- Switch wipe to `drop_all` + `create_all` (§2).
- Add `"documents"` to `TABLES_IN_FK_ORDER` (§2).
- Classes created with explicit `teacher_id` (already the case — admin "owns" creation now).
- Add a few `reading` + `listening` exercises (`passage_text` / `audio_prompt_url`) and matching
  submissions (`submission_type=reading|listening`, answers in `writing_text`).
- Add a handful of `Document` rows owned by teachers, some `class_id`-scoped.

---

## 6. Ordered task checklist
1. [models.py] enums (×2), `Exercise.passage_text`, `Document` + reverse rels, `Class.teacher_id` NOT NULL.
2. [schemas.py] `passage_text` on Exercise schemas; `DocumentCreate`/`DocumentOut`.
3. [classes_router.py] admin-only create + teacher_id validation; lock `teacher_id` in update.
4. [documents_router.py] new; register in [main.py].
5. [modules_router.py] teacher submissions inbox (graded/ungraded, incl. self-practice).
6. dashboard + admin activity aggregate endpoints.
7. [auth.py / auth_router.py] suspended enforcement, register role restriction, feedback guard.
8. [seed.py] drop_all+create_all, TABLES_IN_FK_ORDER, reading/listening + document rows.
9. Reset DB + `python -m app.seed`; smoke-test new flows via `/docs`.

## 7. Deferred (how to grow later)
- **Structured comprehension Q&A** — add `Question` / `QuestionOption` / `StudentAnswer`,
  an `answers[]` field on `SubmissionCreate`, and `is_correct` auto-scoring in the submit
  endpoint. Resolves the brief's open question (free-text vs multiple-choice).
- **ActivityLog** — promote the aggregate endpoints to a real audit table + write hooks if
  monitoring needs history/diffing beyond "recent rows".

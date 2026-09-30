# Database Design

Companion notes for `docs/database-design.puml` (5 diagrams: 1 overview + 4 detail).
Source of truth: `core/models.py`.

| | |
|---|---|
| Engine | PostgreSQL (`DATABASE_URL`, `config/settings.py`) |
| ORM / migrations | Django ORM, 13 migrations in `core/migrations/` |
| Primary keys | `bigint` auto-increment (`BigAutoField`) on every table |
| App tables | 32, in 7 domains |
| Framework tables | `users_groups`, `users_user_permissions`, `auth_group`, `auth_permission`, `django_content_type`, `django_session`, `django_admin_log`, `django_migrations` |
| Enum columns | `varchar` + Django `TextChoices` — checked in the app layer, not Postgres `ENUM` types |

---

## Domains

### 1. Identity & Admin — 3 tables

| Table | Purpose |
|---|---|
| `users` | One table for all roles (`student` / `teacher` / `admin`). Email is the login. `status` drives app logic; `is_active` gates Django auth. |
| `ai_models` | Registry of AI engine targets. The newest row with `is_active = true` is the live one, so swapping engines is a row flip, not a deploy. `strictness` tunes grading severity. |
| `system_logs` | Append-only audit trail of admin actions (suspend/activate a user, etc.). |

### 2. Classroom — 6 tables

| Table | Purpose |
|---|---|
| `classes` | A class taught by one teacher. |
| `class_students_lnk` | Enrolment (many-to-many users ↔ classes). `UNIQUE (klass_id, student_id)`. |
| `lesson_plans` | A class's plan: objectives + date range. |
| `assignments` | An exercise handed to a class, with an optional due date. |
| `study_materials` | Reference documents. `klass_id` null = global library, set = one class only. |
| `meetings` | 1:1 video room metadata. Media is peer-to-peer WebRTC; only `scheduled_at` / `started_at` / `ended_at` are stored. |

### 3. Content — 5 tables

| Table | Purpose |
|---|---|
| `learning_modules` | A course unit, tagged by difficulty, band, topic. |
| `student_module_progress` | Completion % per student per module. `UNIQUE (student_id, module_id)`. |
| `exercises` | The unit of work: reading, listening, writing, speaking or quiz. Holds the stimulus (passage, audio URL, prompt). Can pin a rubric. |
| `questions` | Questions on a receptive exercise. `max_words` enforces IELTS "no more than N words". |
| `question_options` | Options per question. `is_correct` **is** the answer key. |

### 4. Submissions & Feedback — 4 tables

| Table | Purpose |
|---|---|
| `submissions` | A student's answer to one exercise. Written text, audio URL, or `answers` JSON — whichever the exercise type needs. |
| `feedback` | One review pass: a teacher's or the AI's. `reviewer_id` null + `is_ai_generated` = AI. `score` is always 0–100. |
| `ai_insights` | Stored AI coaching (mistake explanations, feedback reviews) as schema-shaped JSON. Regenerating inserts a new row; the API reads the newest. |
| `writing_annotations` | Inline teacher notes anchored to a character range of an essay. `CHECK (end_offset > start_offset)`. |

### 5. Rubrics — 4 tables

| Table | Purpose |
|---|---|
| `rubric_templates` | A scoring matrix, e.g. "IELTS Writing Task 2": its scale, step, and rule for combining criterion scores. Retired with `is_active = false`, never deleted. |
| `rubric_criteria` | Rows of the matrix (Task Response, Coherence, …). `code` is a stable key for analytics. |
| `rubric_band_descriptors` | Cells of the matrix: the official wording for a criterion at a band. |
| `criterion_scores` | One score per criterion, hung off a `feedback` row. |

### 6. Mock Tests — 8 tables

Split into an **authoring** side (what a test *is*) and a **sitting** side (a student taking it).

| Table | Side | Purpose |
|---|---|---|
| `test_formats` | authoring | IELTS Academic, TOEIC… plus how the overall score is combined (`band_average` / `scaled_sum` / `mean_percent`). |
| `score_conversion_tables` | authoring | Raw → band mapping per format per skill, as sparse JSON (`{"39": "9.0", "37": "8.5"}`). |
| `mock_test_templates` | authoring | A specific test paper in a format. |
| `test_sections` | authoring | Listening / Reading / Writing / Speaking blocks, with duration and `item_flow` (`free` or `sequential`). |
| `test_section_exercises_lnk` | authoring | Which exercises make up a section, their order, `weight`, and speaking timings. Reuses ordinary `exercises`. |
| `test_attempts` | sitting | One student sitting one template. `mode` (`exam` / `practice`) fixed at creation. |
| `section_attempts` | sitting | Per-section state: the server clock (`expires_at`), the autosave draft, raw/converted scores, and the AI-grading job status. |
| `section_submissions_lnk` | sitting | Links each section to the ordinary `submissions` it produced. One-to-one on `submission_id`. |

### 7. Pronunciation — 2 tables

| Table | Purpose |
|---|---|
| `pronunciation_drills` | A target word/sentence/minimal pair with an IPA hint. Deliberately **not** an `exercises` row — drills are unlimited low-stakes practice, not gradeable coursework. |
| `pronunciation_attempts` | One recording + the engine's scores (accuracy, fluency, completeness, prosody) and a per-word/per-phoneme breakdown. |

---

## Design decisions

### One `submissions` table for every kind of work
A submission can come from three places, and the table does not change for any
of them:

- **Class assignment** — `assignment_id` set.
- **Self-practice** — `assignment_id` null.
- **Mock test** — linked through `section_submissions_lnk`.

Mock tests were added later as a *link table* beside `submissions` rather than
new columns on it. So the teacher inbox, the grading screen, rubrics, AI
feedback and annotations all work on mock-test answers with no changes.

### Delete behaviour is chosen per relationship
Three `on_delete` policies, used deliberately:

| Policy | Used for | Why |
|---|---|---|
| `CASCADE` | Owned children — questions of an exercise, sections of an attempt, feedback of a submission | They mean nothing without the parent. |
| `PROTECT` | `classes.teacher_id`, `test_attempts.template_id`, `section_attempts.section_id`, `test_section_exercises_lnk.exercise_id`, `criterion_scores.criterion_id`, `mock_test_templates.format_id` | Deleting the parent would destroy real results. A test someone has sat is frozen — duplicate it to revise. A rubric in use is retired, not deleted. |
| `SET NULL` | Every `created_by` / `uploaded_by` / `updated_by` / `reviewer_id` / `admin_id` | Provenance. Removing a staff account must not delete the content they wrote or the audit trail they left. |

### JSONB only where the data is a document
JSON columns are used where the data is read and written as one whole piece,
never queried field by field:

- `section_attempts.draft_answers` — the autosave envelope. One `UPDATE` per
  save instead of one row per answer, which keeps autosave cheap.
- `submissions.answers` — `{question_id: {option_ids, text}}`.
- `score_conversion_tables.mapping` — sparse lookup, nearest-lower key wins.
- `ai_insights.payload`, `pronunciation_attempts.word_results`,
  `engine_metadata` — engine output kept exactly as the engine shaped it.

Everything that is filtered, joined or constrained stays a real column.

### New test formats and rubrics are data, not code
Adding a new exam type means inserting a `test_formats` row and its
`score_conversion_tables` rows — no migration, no code change. Rubrics work the
same way: the combining rule (`aggregation`) is a column, because the IELTS
rounding convention is not officially published and may need to change.

### Invariants the database enforces itself

| Constraint | Table | Guarantees |
|---|---|---|
| Partial unique `(exercise_type) WHERE is_default_for_type AND is_active` | `rubric_templates` | At most one active default rubric per exercise type. |
| `CHECK (end_offset > start_offset)` | `writing_annotations` | No empty or reversed annotation ranges. |
| `UNIQUE (attempt_id, section_id)` | `section_attempts` | One state row per section per sitting. |
| `UNIQUE (template_id, order)` | `test_sections` | Section order is unambiguous. |
| `UNIQUE submission_id` | `section_submissions_lnk` | A submission belongs to at most one section. |
| `UNIQUE (feedback_id, criterion_id)` | `criterion_scores` | One score per criterion per review. |
| `UNIQUE (format_id, skill)` | `score_conversion_tables` | One conversion table per skill per format. |

### Indexes for "newest first" reads
Beyond the automatic foreign-key indexes, two composite indexes match the
hottest queries exactly:

- `ai_insights (submission_id, kind, created_at DESC)` — "latest explanation for
  this submission".
- `pronunciation_attempts (student_id, drill_id, created_at DESC)` — "my history
  on this drill".

### Status columns as a job queue
`section_attempts.ai_status` + `ai_started_at` work as the AI-grading queue,
with no broker. Workers claim rows with `SELECT … FOR UPDATE SKIP LOCKED`, so two
workers never grade the same section. A row stuck in `running` past a timeout can
be claimed again. `test_attempts` is row-locked the same way when a section
starts, so a double-click cannot start two clocks.

### Immutable data makes stable anchors
`submissions.writing_text` cannot be edited after submit, so an annotation's
`[start_offset, end_offset)` stays correct forever. `quoted_text` stores the
selected text as well, as an integrity check and a display fallback.

### Scores on one scale, converted at the edges
`feedback.score` is always 0–100 across the platform. Rubrics score on their own
native scale (e.g. bands 0–9) and are normalised into that 0–100 value. Mock
tests convert back out to the exam's own scale (band or scaled score) through
`score_conversion_tables`. Comparisons and dashboards only ever deal with one
scale.

### Nullable scores allow create-then-fill
Every score column on `pronunciation_attempts` is nullable. The row is saved
before the engine runs, so if the engine fails the recording is kept and can be
re-scored later. The same shape lets scoring move to an async worker later
without a schema change.

# Research Docs — Index (API)

Four feature research docs, each paired with a frontend companion in
`english-learning-web/docs/research/`. This index summarizes each doc and
consolidates the cross-cutting infrastructure decisions all four share.
Cross-doc references use the short names "doc 01" … "doc 04".

---

## Doc index

### [01 — Mock Tests (IELTS / TOEIC, extensible)](01-mock-tests.md)

A timed, multi-section mock-test mode built as a thin composition layer over
the existing Exercise/Question/Submission machinery (build-over-adopt decision
vs QTI/TAO/Learnosity). A data-driven `TestFormat` registry plus versioned
score-conversion tables make new exam formats a data change, not a code fork.
Adds an attempt engine (`TestAttempt`/`SectionAttempt`) with a strict section
state machine, server-authoritative timing (`expires_at` + `server_time`,
409 `section_expired`), single-row autosave drafts, and answer-blind runner
serializers (no `is_correct`, empty `[[]]` fill-blank markers). Receptive
sections auto-grade via `Submission.grade()`; Writing/Speaking flow through the
existing `Feedback` pipeline (human + AI) and map to bands via conversion
tables. Frontend companion: `01-mock-tests-frontend.md` (catalog, full-screen
runner, report).

### [02 — Scoring Matrix (Rubrics) for Speaking & Writing Review](02-scoring-rubrics.md)

Replaces one-number grading of productive skills with criteria×bands rubric
matrices stored as data (`RubricTemplate` → `RubricCriterion` →
`RubricBandDescriptor`), seeded from public IELTS/TOEIC/CEFR descriptors.
Per-criterion scores (`CriterionScore`) attach to the existing `Feedback` row;
a per-template aggregation rule (IELTS mean-down-half as data, not code)
computes the native overall and normalizes it into `Feedback.score` 0–100, so
every existing consumer (inbox, dashboard, CSV) keeps working untouched.
Custom Django models, standards-informed (Canvas/Moodle/1EdTech shape).
Phase 3 extends the AI backends' return contract with the same
`criterion_scores` structure for human-vs-AI comparability. Frontend
companion: `02-scoring-rubrics-frontend.md` (clickable matrix, student
breakdown).

### [03 — Inline Annotations for Teacher Review of Student Writing](03-writing-annotations.md)

Span-anchored, categorized inline notes on `Submission.writing_text`
(`WritingAnnotation`: start/end code-point offsets + quoted-text integrity
check), leaning on the fact that `writing_text` is immutable after submit.
Custom implementation using W3C Web Annotation selector vocabulary
(`TextPositionSelector`/`TextQuoteSelector`) after rejecting dead or
overweight annotation libraries. Same read audience as feedback
(`can_view_submission_feedback`); annotations never touch `Submission.status`
or `Feedback`. Coexists with doc 02 on the review screen as the complementary
*inline* pass (annotations = where; rubric matrix = how much per skill) —
disjoint tables, shippable in either order. Frontend companion:
`03-writing-annotations-frontend.md` (selection capture, segment-split
overlap renderer, synced sidebar).

### [04 — Pronunciation Practice](04-pronunciation-practice.md)

A self-serve record → score → retry drill loop (words, sentences, minimal
pairs) with phoneme-granular feedback. Engine choice: Azure AI Speech
Pronunciation Assessment (accuracy/fluency/completeness/prosody + IPA phoneme
scores + miscue detection, ~$0.003 per 10 s attempt), behind a Mock/Real
backend split mirroring `core/ai/backends.py`; SpeechAce is the documented
fallback. New `PronunciationDrill`/`PronunciationAttempt` models deliberately
bypass `Submission`/`Feedback` (unlimited low-stakes practice, no inbox
noise). **This doc owns the platform's real-file-storage decision** (first
`FileField`, `MEDIA_ROOT`, ffmpeg transcode — Phase 0). Frontend companion:
`04-pronunciation-practice-frontend.md` (`useRecorder()` hook, level meter,
word/phoneme feedback UI).

---

## Shared Infrastructure Decisions

These are the one-per-platform choices the four docs now agree on. When a doc
appears to differ, the doc named here is authoritative.

### 1. File & audio storage — one story (owner: doc 04 §3.3)

- **Django `FileField` + `MEDIA_ROOT` on local disk for MVP → `django-storages`
  + S3 later** by flipping `STORAGES["default"]` — no model changes on the
  flip. Direct-to-S3 presigned uploads and DB blobs are rejected.
- Media serving: authenticated file view in dev/MVP; S3 + presigned
  (signed, expiring) GET URLs in the S3 phase — which is also what doc 01's
  play-once listening integrity ultimately wants.
- Existing mock-URL string fields (`StudyMaterial.file_url`,
  `Exercise.audio_prompt_url`, `Submission.audio_recording_url`) stay as-is
  until migrated in a later pass. Consequences per feature:
  - doc 01: real Listening audio and Speaking uploads are gated on this
    (risk 1); mock-test speaking recordings travel as data-URL strings at
    submit until then.
  - doc 02: real speaking playback on the review screen is gated on this
    (risk 4); matrix grading works regardless.
  - doc 04: introduces the infrastructure (Phase 0) and the first real
    `FileField` (`PronunciationAttempt.audio_file`).

### 2. Audio recording + transcoding pipeline (owner: doc 04 §3.3, pipeline A)

- Browser records with **native `MediaRecorder`** (webm/opus on
  Chrome/Firefox, mp4/aac on Safari); no client-side re-encoding, no recorder
  library.
- The raw Blob is uploaded (multipart `FormData` for pronunciation; data-URL
  string for mock-test speaking until storage migrates).
- **Server-side ffmpeg** (`core/audio.py: transcode_to_wav16k`, subprocess,
  no pydub) is the single normalization point to WAV PCM 16 kHz mono, plus
  upload validation (≤ 5 MB / ≤ 30 s via ffprobe). ffmpeg becomes a documented
  deploy dependency (DEPLOYMENT.md + image).
- Frontend counterpart: one shared `useRecorder()` hook
  (`lib/use-recorder.ts`, spec in doc 04 FE §4.3) consumed by pronunciation's
  `RecorderPanel` and doc 01's `SpeakingTaskPane`; the legacy base64 recorder
  in the exercises page is superseded, migrated later.

### 3. Sync-now / Celery-later boundary

No Celery, no queues today — all four docs hold the same line, with the async
boundaries pre-drawn so going async is a deployment change, not a redesign:

- **Grading (docs 01, 02):** `core/ai/service.py: evaluate_submission()` runs
  synchronously and is the documented future enqueue boundary. Doc 01 keeps AI
  auto-grade of mock W/S sections optional until that boundary is implemented;
  doc 02's rubric persistence changes nothing there.
- **Pronunciation (doc 04):** `core/ai/pronunciation.py: assess_attempt()` is
  the future Celery task body; the view is its only caller. Nullable score
  fields already permit a create-then-fill async path.
- **Housekeeping (doc 01):** `expire_section_attempts` is a management command
  on cron now, Celery beat later.
- Trigger to revisit: p95 latency (> ~4 s for pronunciation) or Azure/LLM
  throttling — not speculative scale.

### 4. AI backend registry pattern (reuse, not reinvention)

- One admin-managed **`AiModel` registry** (`AiModel.active()`, `strictness`,
  `endpoint_url`) serves both engine families; engine swaps stay an admin
  action.
- Settings-driven backend switches: existing `AI_BACKEND` (grading) and new
  sibling `PRONUNCIATION_BACKEND` (doc 04), each with the same Mock/Real
  split, deterministic offline mocks, and loud-failure Real stubs.
- Division of labor (not a conflict): **Whisper transcription + LLM rubric**
  = the *graded* speaking path (`transcribe_and_score_speaking`, docs 01–02);
  **Azure Pronunciation Assessment** = phoneme-level *measurement* (doc 04).
  Plain STT is structurally wrong for the latter; the LLM path is right for
  the former.
- Doc 02 Phase 3 extends the grading backends' return contract with
  `criterion_scores`; doc 01 consumes only the normalized `Feedback.score`,
  so the two changes are orthogonal.
- Flagged jointly: once two *real* engines are live, `AiModel` is double-duty
  — add a `purpose` field then (doc 04 risk table).

### 5. Rubrics + annotations on the review screen (docs 02 ↔ 03)

- Disjoint tables (`rubric_*`/`criterion_scores` vs `writing_annotations`);
  both are additive around `Feedback`; both keep the existing
  `Submission.status` mutators (`FeedbackViewSet.perform_create` and the AI
  path `evaluate_submission`) untouched. The features ship in either order.
- One review screen, two passes: annotations = *where* (inline), matrix =
  *how much per skill* (overall). Frontend layout coordination is tracked
  symmetrically (doc 02 FE risk 5 / doc 03 FE risk 8): left column text +
  inline annotations, right column annotation sidebar above rubric matrix +
  comments — resolved by whichever merges second.
- Shared permission story: `can_view_submission_feedback` is the single read
  audience for all review artifacts.

### 6. Suggested build order

1. **03 — Writing annotations.** Smallest scope, zero infrastructure
   dependencies, immediately valuable. Establishes the frontend's zod
   runtime-validation pattern and adds `@floating-ui/react` +
   `@hookform/resolvers` (which docs 02/04 may then reuse).
2. **02 — Scoring rubrics.** Same review screen as 03 (resolves the layout
   pass); unblocks *meaningful* Writing/Speaking band conversion for mock
   tests (doc 01 maps `Feedback.score` → band regardless, but rubric-graded
   scores make those bands defensible). AI parity (Phase 3) can trail.
3. **04 — Pronunciation practice.** Its Phase 0 (media plumbing:
   `MEDIA_ROOT`, ffmpeg, `core/audio.py`) is the platform storage story that
   unblocks real audio everywhere, and its `useRecorder()` hook is consumed by
   doc 01's Speaking pane. Phase 0 is standalone — it can be pulled forward at
   any time if mock tests need real audio sooner.
4. **01 — Mock tests.** Largest feature; consumes 02 (rubric-graded W/S →
   band conversion), 04 (storage + shared recorder) for full
   Listening/Speaking realism. Its receptive-only slice (backend Phases 1–3 +
   frontend F1–F3, reading sections) has no audio dependency and can proceed
   in parallel with 2–3 whenever capacity exists.

Dependency summary: 03 unblocks nothing but proves the review-screen pattern;
02 unblocks 01's productive scoring quality; 04 Phase 0 unblocks 01's audio
realism and all future media features; 01 depends (softly) on all three.

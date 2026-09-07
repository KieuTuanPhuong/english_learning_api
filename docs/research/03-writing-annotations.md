# Feature 3 — Inline Annotations for Teacher Review of Student Writing

Research and design document. Companion frontend doc:
`english-learning-web/docs/research/03-writing-annotations-frontend.md`.

Sibling features: `01-mock-tests.md`, `02-scoring-rubrics.md`,
`04-pronunciation-practice.md`. Inline annotations and rubrics (doc 02) both
extend the submission-review flow around `Feedback`; §6 covers how they
coexist without touching each other's tables.

---

## 1. Overview & Goals

Teachers currently grade writing submissions with a single holistic `Feedback`
row (score + one comments blob, `core/models.py: Feedback`). For writing
pedagogy that is coarse: a student who wrote 300 words gets "watch your verb
tenses and paragraph structure" with no pointer to *where* the problems are.

This feature adds **inline annotations**: a teacher selects a span of the
student's `Submission.writing_text`, attaches a categorized note (grammar,
vocabulary, spelling, coherence, task response, praise, other) and an optional
suggested correction. The student sees the same text with color-coded
highlights and a synced sidebar of notes. Annotations are an *inline pass* that
complements — not replaces — the existing *overall pass* (`Feedback`).

**Users**
- **Teacher** (and admin): create/edit/delete annotations on writing
  submissions in classes they teach; then post overall `Feedback` as today.
- **Student**: read-only view of annotations on their own submissions;
  optionally acknowledge each one ("got it") so teachers see uptake.

**Success criteria**
- A teacher can annotate any span of a writing submission in ≤3 interactions
  (select → categorize → save).
- Highlights render correctly for overlapping annotations and survive reload
  byte-for-byte (offsets never drift, because the text is immutable).
- Student view reuses the exact same renderer; no divergence between what the
  teacher marked and what the student sees.
- No schema or behavior change to `Feedback`, `Submission.status`, or the
  grading lifecycle.

---

## 2. Requirements Breakdown

### Functional
- **FR1** Teacher/admin can create an annotation on a writing submission:
  `(start_offset, end_offset)` range into `writing_text`, category, comment,
  optional `suggested_correction`.
- **FR2** Annotations list is readable by: submission owner (student), the
  class teacher, a teacher who reviewed the submission, admins — the same
  audience as feedback (`core/permissions.py: can_view_submission_feedback`).
- **FR3** Author (or admin) can edit comment/category/correction and delete an
  annotation. Offsets are editable only by re-selecting (client sends new
  offsets + quote; server re-validates).
- **FR4** Overlapping ranges are allowed and must render deterministically.
- **FR5** Annotations never mutate `Submission.status`; only `Feedback`
  creation does (`core/views.py: FeedbackViewSet.perform_create` and the AI
  path `core/ai/service.py: evaluate_submission`).
- **FR6** (Phase 2) Student can acknowledge an annotation; author sees the flag.
- **FR7** Only `submission_type == "writing"` submissions with non-empty
  `writing_text` are annotatable (speaking/receptive types are rejected).

### Non-functional
- **NFR1 Anchor integrity.** `writing_text` is written once at submit and never
  updated (no API path mutates it — `SubmissionSerializer` is create-only and
  `SubmissionViewSet` has no update action). Offsets must therefore be exact
  forever; the server must verify the quoted text matches the offsets at write
  time and refuse drifted anchors.
- **NFR2 Interop vocabulary.** Data model should map cleanly onto the W3C Web
  Annotation model (`TextPositionSelector` + `TextQuoteSelector`) so a future
  export or third-party viewer is a serializer, not a migration.
- **NFR3** List endpoint returns all annotations for a submission in one call
  (typical essay: < 50 annotations; no pagination needed).
- **NFR4** Offset unit must be specified precisely (JS UTF-16 vs Python code
  points — see §7 Risks) and enforced by the server-side quote check.
- **NFR5** No new external services, queues, or paid dependencies.

---

## 3. Tools & Technology Choice

The backend needs no new tooling — this is a plain Django model + DRF viewset.
The real choice is the **frontend annotation layer**. Landscape as of August
2026 (verified via web search; sources inline):

| Candidate | What it is | Pros | Cons | Cost |
|---|---|---|---|---|
| **Custom React renderer + selection capture** (recommended) | ~300 LOC: segment-splitting renderer, `window.getSelection()` → offsets, popover editor | Zero deps; exact fit to our offset model; trivially React 19 / Next 16 App Router safe; full control of category colors, sidebar sync, student read view | We own the offset math and overlap algorithm (both small, testable pure functions) | Free |
| **`@recogito/text-annotator-js` + `@recogito/react-text-annotator`** | Actively maintained successor to RecogitoJS; W3C-aligned model with `TextQuoteSelector`/`TextPositionSelector`; React wrapper on v3.4.x with recent publishes — https://github.com/recogito/text-annotator-js, https://www.npmjs.com/package/@recogito/react-text-annotator | Maintained; W3C selectors out of the box; handles selection + rendering | Imperative annotator instance wrapped for React — heavier than our need; brings its own popup/styling system we'd fight to match the Tailwind `components/ui` kit; anchors against live DOM rather than our canonical immutable string; young API surface | Free (BSD-3) |
| **RecogitoJS / Annotorious classic** | Older Recogito annotation libraries — https://github.com/recogito/recogito-js | — | `recogito-js` is **deprecated and archived (Sep 30 2025, read-only)**; Annotorious (https://www.npmjs.com/package/@recogito/annotorious) is image-centric — wrong medium | Free |
| **`react-text-annotate` / `react-text-annotate-blend`** | Small React span-annotation components; the blend variant adds overlap color-blending — https://www.npmjs.com/package/react-text-annotate, https://github.com/smhaley/react-text-annotate-blend | Tiny API; token/span model close to ours; blend variant demonstrates the overlap-rendering idea | `react-text-annotate` last published ~6 years ago (v0.3.0), effectively unmaintained; the blend fork is a small project with no verified React 19 support; no sidebar/popover — we'd still build most of the UI | Free |
| **TipTap / ProseMirror + comments** | Rich-text editor framework with a commenting feature | Battle-tested marks/decorations engine | Requires replacing our plain `whitespace-pre-wrap` render of `writing_text` with a full editor document; comments live in TipTap's **paid Cloud platform** (plans from ~$49/mo; Team $149/mo — https://eddyter.com/blogs/tiptap-pricing-explained-2026); editor document positions ≠ our string offsets; huge dependency for a read-mostly display | Core MIT free; comments paid |
| **Hypothesis client / Apache Annotator** | W3C Web Annotation ecosystem: Hypothesis sidebar client, `apache/incubator-annotator` selector libs | Reference implementations of W3C anchoring (quote → position fallback) | Hypothesis client is a full sidebar app for annotating arbitrary third-party pages — wrong integration shape; Apache Annotator never left incubation and retirement has been on the IPMC's agenda, activity is minimal — https://incubator.apache.org/projects/annotator.html, https://github.com/apache/incubator-annotator | Free |

### Recommendation: custom implementation, W3C vocabulary

Build the annotation layer ourselves, and borrow the **W3C Web Annotation data
model's selector vocabulary** (https://www.w3.org/TR/annotation-model/) where
it maps cleanly: our `start_offset`/`end_offset` *are* a
`TextPositionSelector`, and `quoted_text` *is* the `exact` of a
`TextQuoteSelector`, used here as an integrity check.

Why custom wins:

1. **The hard problem the libraries solve doesn't exist for us.** Anchoring
   libraries (Hypothesis, Apache Annotator, text-annotator-js) spend most of
   their complexity *re-attaching* annotations to text that may have changed
   or whose DOM differs between visits. Our target is a single immutable
   database string rendered by our own component — offsets into `writing_text`
   are exact forever. What remains (selection → offsets, segment-split
   rendering, a popover form) is a few small pure functions plus UI we would
   have to build anyway to match the existing design system.
2. **Maintenance risk elsewhere is real.** Two of the ready-made options are
   dead (recogito-js archived 2025; react-text-annotate stale since ~2019),
   and TipTap's comments are a paid cloud product dragging in an editor we
   don't need.
3. **Stack fit.** The web app is React 19.2 / Next 16 App Router / TypeScript
   strict / Tailwind 4. A custom renderer is guaranteed compatible and typed
   end-to-end against the OpenAPI-generated types (`lib/types/api.d.ts`),
   exactly like the existing quiz components.
4. **W3C vocabulary keeps the door open.** Because the stored fields are
   isomorphic to `TextPositionSelector` + `TextQuoteSelector`, exporting
   standards-compliant JSON later is a read-only serializer, not a migration.

---

## 4. Design (Backend / API)

### 4.1 Model

Add to `core/models.py` after `Feedback`, matching the file's conventions
(`TextChoices`, explicit `db_table`, nullable `SET_NULL` author FKs):

```python
class AnnotationCategory(models.TextChoices):
    """Pedagogical taxonomy for inline writing annotations."""
    GRAMMAR = "grammar", "Grammar"
    VOCABULARY = "vocabulary", "Vocabulary"
    SPELLING = "spelling", "Spelling"
    COHERENCE = "coherence", "Coherence"
    TASK_RESPONSE = "task_response", "Task response"
    PRAISE = "praise", "Praise"
    OTHER = "other", "Other"


class WritingAnnotation(models.Model):
    """An inline note anchored to a character range of a writing submission.

    Anchoring: ``writing_text`` is immutable after submit (no update path in
    the API), so ``[start_offset, end_offset)`` — Unicode code points into
    that string — is a permanently exact anchor (W3C TextPositionSelector).
    ``quoted_text`` duplicates the sliced text (W3C TextQuoteSelector.exact)
    as a server-enforced integrity check and a display fallback.
    """

    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="annotations"
    )
    author = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="annotations_authored",
    )
    start_offset = models.PositiveIntegerField()
    end_offset = models.PositiveIntegerField()  # exclusive
    quoted_text = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=AnnotationCategory.choices,
        default=AnnotationCategory.OTHER,
    )
    comment = models.TextField()
    suggested_correction = models.TextField(null=True, blank=True)
    # Phase 2: student uptake signal ("got it"). Never blocks anything.
    is_acknowledged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "writing_annotations"
        ordering = ["start_offset", "end_offset", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_offset__gt=models.F("start_offset")),
                name="writing_annotation_nonempty_range",
            ),
        ]

    def __str__(self):
        return f"Annotation {self.pk} on submission {self.submission_id}"
```

Notes:
- **Offset unit is Unicode code points** (Python string indexing), end
  exclusive, `0 <= start < end <= len(writing_text)`. The frontend converts
  from JS UTF-16 indices (see frontend doc "Offset math" and §7 Risks).
- The DB `CheckConstraint` guards non-empty ranges; the upper bound and quote
  equality need the submission's text, so they live in the serializer.
- No FK to `Feedback`: annotations and feedback are independent passes over
  the same submission (a teacher may annotate without scoring, or score
  without annotating). The review screen fetches both per submission.

### 4.2 Endpoints

Follows the established Feedback split exactly — nested **read** under
`SubmissionViewSet`, top-level viewset for **writes** (cf. the `lib/api.ts`
comment: "Grading: POST /api/feedback/ with submission_id (nested path is
405)"):

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/submissions/{id}/annotations/` | owner student / class teacher / reviewer / admin | List annotations on a submission, ordered by `start_offset` |
| POST | `/api/annotations/` | teacher of the submission's class, admin | Create (body carries `submission_id`) |
| PATCH | `/api/annotations/{id}/` | author, admin | Edit category/comment/correction/offsets |
| DELETE | `/api/annotations/{id}/` | author, admin | Remove |
| POST | `/api/annotations/{id}/acknowledge/` | submission's student | Phase 2 — set `is_acknowledged=True` |

Registration: `router.register("annotations", views.WritingAnnotationViewSet,
basename="annotation")` in `config/urls.py`. The list is a new
`@action(detail=True, methods=["get"], url_path="annotations")` on
`SubmissionViewSet`, guarded by the existing `can_view_submission_feedback`
(the annotation audience is identical to the feedback audience by design).

### 4.3 Serializer

`core/serializers.py`, mirroring `FeedbackSerializer`'s `submission_id`
pattern:

```python
class WritingAnnotationSerializer(serializers.ModelSerializer):
    submission_id = serializers.PrimaryKeyRelatedField(
        source="submission", queryset=Submission.objects.all()
    )
    author_id = serializers.PrimaryKeyRelatedField(source="author", read_only=True)

    class Meta:
        model = WritingAnnotation
        fields = [
            "id", "submission_id", "author_id",
            "start_offset", "end_offset", "quoted_text",
            "category", "comment", "suggested_correction",
            "is_acknowledged", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "author_id", "is_acknowledged",
                            "created_at", "updated_at"]

    def validate(self, attrs):
        submission = attrs.get("submission") or self.instance.submission
        start = attrs.get("start_offset", getattr(self.instance, "start_offset", None))
        end = attrs.get("end_offset", getattr(self.instance, "end_offset", None))

        if submission.submission_type != SubmissionType.WRITING or not submission.writing_text:
            raise serializers.ValidationError(
                "Annotations are only supported on writing submissions."
            )
        text = submission.writing_text
        if not (0 <= start < end <= len(text)):
            raise serializers.ValidationError("Offsets out of range for writing_text.")

        # W3C TextQuoteSelector integrity check: the client's quote must equal
        # the server-side slice, else the client's offset mapping is buggy
        # (e.g. UTF-16 vs code points) and we refuse to store a drifted anchor.
        expected = text[start:end]
        quoted = attrs.get("quoted_text")
        if quoted is not None and quoted != expected:
            raise serializers.ValidationError(
                {"quoted_text": "Does not match writing_text[start:end] — offset mismatch."}
            )
        attrs["quoted_text"] = expected  # server value is canonical
        return attrs
```

### 4.4 ViewSet & permissions

```python
class WritingAnnotationViewSet(
    mixins.CreateModelMixin, mixins.UpdateModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    serializer_class = s.WritingAnnotationSerializer

    def get_queryset(self):
        # Same leak-proof scoping idea as FeedbackViewSet.get_queryset.
        qs = WritingAnnotation.objects.all().order_by("id")
        user = self.request.user
        if user.role == UserRole.ADMIN:
            return qs
        if user.role == UserRole.TEACHER:
            return qs.filter(
                Q(author=user) | Q(submission__assignment__klass__teacher=user)
            ).distinct()
        return qs.filter(submission__student=user)  # students: acknowledge only

    def get_permissions(self):
        if self.action == "acknowledge":
            return _perms(IsStudent)
        return _perms(IsTeacherOrAdmin)

    def perform_create(self, serializer):
        submission = serializer.validated_data["submission"]
        if not _teacher_may_annotate(self.request.user, submission):
            raise PermissionDenied("Not your class's submission")
        serializer.save(author=self.request.user)
```

- `_teacher_may_annotate(user, submission)`: admin → yes; teacher → yes if
  `submission.assignment.klass.teacher_id == user.id`, or (for self-practice
  submissions where `assignment` is null) if the teacher owns the exercise
  (`exercise.created_by_id == user.id`, with the `exercise.module.created_by`
  fallback — the same ownership rule the submissions inbox uses,
  `SubmissionViewSet.inbox`). Lives beside
  `can_view_submission_feedback` in `core/permissions.py`.
- Edit/delete: object-level check `obj.author_id == user.id or admin`
  (in `perform_update` / `perform_destroy`).
- `acknowledge`: `@action(detail=True, methods=["post"])`, permitted only when
  `obj.submission.student_id == request.user.id`.

### 4.5 Overlap handling (contract)

The API deliberately **allows overlapping ranges** and stores them as-is;
resolving overlaps is purely a rendering concern. Canonical algorithm (shared
with the frontend, detailed in the frontend doc): collect all `start_offset` /
`end_offset` values as boundaries, sort and dedupe them, split `writing_text`
into segments between consecutive boundaries, and tag each segment with the
set of annotations covering it. Every segment renders exactly once with
stacked styles. The backend only guarantees the invariant that makes this
trivial: valid, quote-verified, immutable offsets.

### 4.6 AI-backend integration (future, not MVP)

`core/ai/backends.py: BaseAIBackend.grade_writing()` returns
`{"score", "comments"}`. A natural extension adds an optional
`"annotations": [{start_offset, end_offset, category, comment,
suggested_correction}]` key: `MockBackend` can emit deterministic ranges
(e.g. flag sentences lacking its rubric markers), and `RealBackend`'s LLM
prompt can request character-anchored issues, validated server-side by the
same serializer — the quote check catches LLM off-by-one errors (reject rows
that fail, keep the rest). Such rows would be created with `author=None` plus
a new `is_ai_generated` flag mirroring `Feedback.is_ai_generated`. Deferred
because it multiplies prompt/validation complexity and the teacher-authored
flow must prove the UX first.

---

## 5. Implementation Guidelines

**Phase 1 — MVP (teacher annotates, student reads)**
1. `core/models.py`: add `AnnotationCategory` + `WritingAnnotation` (§4.1);
   `python manage.py makemigrations core && python manage.py migrate`.
2. `core/permissions.py`: add `_teacher_may_annotate` beside
   `can_view_submission_feedback`.
3. `core/serializers.py`: add `WritingAnnotationSerializer` (§4.3) after
   `FeedbackSerializer`.
4. `core/views.py`: add `WritingAnnotationViewSet`; add the `annotations`
   list `@action` to `SubmissionViewSet` next to the existing `feedback`
   action; `@extend_schema` summaries on every action so the drf-spectacular
   schema (`/api/schema/`) stays self-documenting.
5. `config/urls.py`: `router.register("annotations", ...)`.
6. `core/tests.py`: offset validation (out of range, empty range, quote
   mismatch, non-writing submission), RBAC matrix (student cannot create,
   foreign teacher cannot create/read, owner student can read), and a guard
   test asserting no API path updates `writing_text`.
7. `core/admin.py`: register `WritingAnnotation` (list_display: submission,
   author, category, offsets) for support/debugging.
8. Frontend Phase 1 (see frontend doc): regenerate types (`yarn gen:api`),
   teacher annotate flow + student read view.

**Phase 2 — Enhancements**
9. `acknowledge` action + surfacing `is_acknowledged` in the teacher sidebar.
10. Real-time nudge: reuse `_broadcast_class` (`core/views.py`) to emit an
    `annotation_posted` event alongside the existing `feedback_posted`
    Channels broadcast so an open student view refetches.
11. Optional AI annotation pass (§4.6) behind the existing
    `settings.AI_BACKEND` switch.

**Explicit non-goals**: annotating speaking/receptive submissions; threaded
replies; annotating exercise prompts or study materials; rich-text comments.

---

## 6. Integration with Existing Code

**Reused**
- `Submission.writing_text` as the single canonical, immutable anchor target —
  the whole anchoring strategy leans on the existing fact that
  `SubmissionViewSet` is create-only (`mixins.CreateModelMixin` +
  `GenericViewSet`, no update mixin), so `writing_text` cannot change.
- `Feedback` stays the overall-score mechanism, and `Submission.status` keeps
  its existing mutators (`FeedbackViewSet.perform_create` and the AI path
  `core/ai/service.py: evaluate_submission`) — annotations add none. The
  review screen simply shows both passes.
- `can_view_submission_feedback` (`core/permissions.py`) defines the read
  audience for annotations verbatim — one permission story for "review
  artifacts on a submission".
- `IsTeacherOrAdmin` / `IsStudent` permission classes and the `_perms` helper;
  the `submission_id = PrimaryKeyRelatedField(source=...)` serializer
  convention; the nested-read / top-level-write endpoint split from Feedback;
  `db_table` + `TextChoices` model conventions.
- drf-spectacular → regenerated `lib/types/api.d.ts` keeps the frontend typed
  with zero hand-written DTOs.
- Channels `_broadcast_class` for the Phase 2 live event.

**Coexistence with doc 02 (scoring rubrics)**
- Disjoint tables (`writing_annotations` vs `rubric_*`/`criterion_scores`);
  both features are additive around `Feedback` and both leave the existing
  `Submission.status` mutators alone, so they can ship in either order. On the
  review screen they are complementary passes: inline annotations explain
  *where*, the rubric matrix explains *how much per skill* (right-column
  layout coordination is tracked in both frontend docs — doc 02 FE risk 5,
  doc 03 FE risk 8).

**Deliberately NOT reused**
- `Feedback.comments` as a container for inline notes (e.g. JSON embedded in
  the text blob): annotations need per-row CRUD, authorship, and validation —
  first-class rows, not a serialized payload inside another model's field.
- Quiz components (`components/quiz/*`): they render question/option
  structures; nothing overlaps with span rendering. The submission detail
  *page* is reused (see frontend doc), the quiz components are not.
- `Question`/`QuestionOption`: receptive-only machinery, irrelevant here.
- The AI layer in MVP: annotation authoring is human-first; the AI hook is
  designed (§4.6) but intentionally left unwired.

---

## 7. Risks & Open Questions

| # | Risk / question | Mitigation |
|---|---|---|
| 1 | **UTF-16 vs code-point offsets.** JS `string.length` / DOM offsets count UTF-16 code units; Python indexes code points. One emoji in an essay shifts every later offset by one unit. | Canonical unit = **code points**, converted at the frontend boundary (helper specified in the frontend doc). The serializer's mandatory `quoted_text == text[start:end]` check turns any residual mismatch into a 400 at write time instead of silent anchor drift. |
| 2 | **Immutability assumption breaks later** (e.g. a future "students may revise" feature edits `writing_text`). | Annotations must then be versioned per revision. Guard now: model docstring + a test asserting no API path updates `writing_text`. If revisions arrive, snapshot text per revision (`Submission.revision`) rather than re-anchoring fuzzily; `quoted_text` enables Hypothesis-style re-anchoring as a last resort. |
| 3 | **Whitespace fidelity.** The renderer must preserve the exact character stream; any normalization (e.g. CRLF→LF) between save and render breaks offsets. | Store and render the identical string; never normalize post-submit. The frontend renders from the same API field it computed offsets against (`whitespace-pre-wrap`, already used on the submission page). |
| 4 | Deep overlap stacks (3+ annotations on one segment) become visually unreadable. | Rendering concern only (data model unaffected): cap visual stacking at two category colors + a "+N" affordance; the sidebar carries the full list. |
| 5 | Should students see annotations before the teacher posts overall `Feedback` (mid-review)? | Open question. MVP: yes, visible immediately (simple; matches feedback visibility). If staged release is wanted later, add `is_published` defaulting true — an additive migration. |
| 6 | Concurrent annotators (two teachers / an admin) on one submission. | Low stakes — rows are independent; last-write-wins per row. TanStack Query refetch keeps lists fresh; no locking. |
| 7 | Category taxonomy churn (institutions want custom rubric labels). | `TextChoices` is fine for MVP; if configurability is demanded, promote to an admin-managed table à la `AiModel` — an additive migration. |

---

## 8. Frontend summary

The web implementation — selection capture (`window.getSelection()` + `Range`
→ code-point offsets via text-node walking), the segment-splitting overlap
renderer, the category color system, popover editor, synced sidebar, student
read view, TanStack Query wiring, zod schemas, and phased steps — is specified
in **`english-learning-web/docs/research/03-writing-annotations-frontend.md`**.
It consumes exactly the API contract in §4 (via regenerated
`lib/types/api.d.ts`) and touches `lib/api.ts`, `lib/query-keys.ts`,
`lib/hooks.ts`, `lib/types/index.ts`, new `lib/annotations.ts` and
`components/annotations/*` files, and
`app/(app)/submissions/[id]/page.tsx` only.

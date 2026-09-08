# Feature 4 — Pronunciation Practice (Backend & Full-Stack Research)

> Companion frontend doc: `english-learning-web/docs/research/04-pronunciation-practice-frontend.md`
> Status: research / design — no code written yet.

---

## 1. Overview & Goals

Pronunciation practice gives **students** a self-serve drill loop for spoken English at three
granularities: single **words** ("thorough"), full **sentences** ("She sells sea shells…"), and
**minimal pairs** ("ship / sheep" — contrasting one phoneme). The student sees the target text
and an IPA hint, records themselves, and gets back machine scores within a few seconds:

- an **overall pronunciation score** (0–100),
- per-dimension scores (**accuracy, fluency, completeness, prosody**),
- **word-level and phoneme-level** results rendered as color-coded feedback ("you said /s/
  where /ʃ/ was expected in *ship*"),
- an attempt history so they can see improvement across retries.

**Teachers/admins** author drills (optionally attached to a `LearningModule`) and can later view
aggregate struggle data (post-MVP). This is a *practice loop*, not graded coursework — attempts
are unlimited, low-stakes, and never enter the teacher grading inbox.

**Success criteria**

1. A student can complete record → score → retry in under ~10 seconds per cycle.
2. Feedback is phoneme-granular, not just a single number (this is the entire pedagogical point).
3. Dev/test environments run fully offline with a deterministic mock engine — same stance as
   `core/ai/backends.py` `MockBackend`.
4. No AI/service credentials ever reach the browser.
5. The feature works on Chrome, Firefox, Safari (desktop + mobile) despite their different
   audio recording codecs.

---

## 2. Requirements Breakdown

### Functional

| # | Requirement |
|---|---|
| F1 | Teacher/admin CRUD for drills: `target_text`, IPA/phoneme hint, type (word / sentence / minimal_pair), difficulty, optional module link. Minimal-pair drills carry a contrast text. |
| F2 | Student browses drills filtered by type, difficulty, and module. |
| F3 | Student records audio in-browser and submits it; server stores the file, runs pronunciation assessment, returns scores + word/phoneme breakdown in one response. |
| F4 | Student can replay their own recording and see attempt history per drill (scores over time). |
| F5 | Assessment engine is swappable (mock ↔ real) via settings + the existing `AiModel` admin registry, mirroring `AI_BACKEND` in `config/settings.py`. |
| F6 | Miscue detection for sentence drills: omitted / inserted / mispronounced words flagged per word. |

### Non-functional

| # | Requirement |
|---|---|
| N1 | Latency: end-to-end score response ≤ ~4 s p95 (upload + transcode + engine call). Synchronous is acceptable at MVP scale (matches the existing synchronous AI-eval stance in `core/ai/service.py`). |
| N2 | Security: engine API keys server-side only; students can only read their own attempts and audio. |
| N3 | Cost: per-attempt engine cost must be sub-cent; drills are short (≤ 15 s audio, enforce ≤ 30 s / ≤ 5 MB upload). |
| N4 | Determinism in tests: mock backend produces stable scores from stable inputs (no network). |
| N5 | Storage: real files (this feature ends the "mock URL string" era) — local disk MVP, S3-compatible later without model changes. |
| N6 | Audio normalization: whatever the browser sends (webm/opus, mp4/aac, wav) is converted server-side to the engine's required format (WAV PCM 16 kHz mono for Azure). |

---

## 3. Tools & Technology Choice

### 3.1 Core decision — pronunciation-assessment engine

Plain STT is structurally the wrong tool here, which rules out the "just use Whisper" shortcut:
Whisper (and Speechmatics, Deepgram, etc.) are trained to be **robust to accents** — they
normalize away exactly the errors we need to measure, and they output orthographic words, not
phoneme-level quality scores. A learner saying /ʃip/ for *sheep* gets transcribed as "sheep"
and looks perfect. Pronunciation assessment needs **forced alignment against a reference text
plus Goodness-of-Pronunciation (GOP) scoring per phoneme**, which is a different model class.
(This rejection is scoped to pronunciation *measurement*: Whisper transcription + LLM rubric
scoring remains the right architecture for the *graded* speaking path in docs 01–02
(`transcribe_and_score_speaking`), where content and language — not phoneme quality — are
assessed.)

Candidates (verified via web search, Aug 2026):

| Engine | Granularity | Score dimensions | Languages | Pricing | Notes |
|---|---|---|---|---|---|
| **Azure AI Speech — Pronunciation Assessment** | phoneme (IPA or SAPI), syllable, word, full text | accuracy, fluency, completeness, **prosody**; miscue detection (omission/insertion/mispronunciation) | broad locale coverage (en/es/fr/de/zh…); prosody en-US | Billed at the same rate as standard speech-to-text (≈ $1 / audio-hour pay-as-you-go; free tier 5 audio-hours/month) → a 10 s attempt ≈ $0.003 | Mature SDK + REST; scripted (reference text) and unscripted modes. https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-pronunciation-assessment · https://azure.microsoft.com/en-us/pricing/details/speech/ |
| **SpeechAce API** | phoneme + syllable + word + sentence | pronunciation, fluency; IELTS/PTE-style estimates on higher plans | en-US/UK, fr, es | Basic **$40/mo** incl. 5,000 × 15 s requests, then $0.008 per 15 s | Purpose-built for language learning; 30 s max audio/request. https://www.speechace.com/api-plans/ · https://api-docs.speechace.com/ |
| **SpeechSuper** | phoneme, syllable, word, sentence | pronunciation, fluency, mispronunciation detection | 8 languages incl. zh | Scripted: word $0.0028, sentence $0.0042, paragraph $0.0056 per request; unscripted $0.021–$0.0245 | Competitive pricing; smaller Western-market footprint, thinner docs than Azure. https://www.speechsuper.com/pricing.html |
| **ELSA API** | phoneme-level feedback, word stress, intonation | pronunciation, fluency, intonation | English focus | **Sales-contact pricing**, no public self-serve tier | Excellent consumer pedigree; opaque pricing and procurement friction for a small platform. https://elsaspeak.com/en/elsa-api/ |
| **Speechmatics** | word-level ASR confidence only | none (it is an STT product) | 50+ | usage-based STT pricing | **Not a pronunciation scorer** — no GOP/phoneme assessment endpoint; rejected for the same reason as Whisper. https://www.speechmatics.com/ |
| **Self-hosted: wav2vec2 forced alignment + GOP (torchaudio/Kaldi recipes)** | phoneme (whatever the lexicon supports) | accuracy only; fluency/prosody are DIY research | any, with per-language lexicon work | GPU hosting $100s/mo + engineering time | Full control, no per-call cost — but this is an ML-ops product in itself (model serving, G2P lexicon, calibration against human raters). Wildly disproportionate for this codebase: no Celery, no GPU infra, one Django app. |

**Recommendation: Azure AI Speech Pronunciation Assessment.**

Justification:

- Only candidate with **all four score dimensions** (accuracy/fluency/completeness/prosody)
  *plus* phoneme-level IPA output *plus* miscue detection in one call — F3 and F6 in a single
  API request.
- **Cheapest managed option at our traffic shape**: pure pay-as-you-go with a free tier and no
  monthly platform fee (SpeechAce's $40/mo floor buys nothing until volume exists; ELSA cannot
  even be priced without a sales cycle). ~10 s attempts cost ~$0.003 each.
- Server-side Python SDK (`azure-cognitiveservices-speech`) fits the "assessment happens on the
  server, on the uploaded file" design (§3.3) — no keys in the browser.
- Slots into the existing `AiModel` registry unchanged: `endpoint_url` holds the Azure
  region/endpoint, `strictness` maps onto score post-processing, and `AiModel.active()` keeps
  engine swaps an admin action, exactly as `core/ai/service.py:get_backend()` works today.

SpeechAce is the documented fallback (the most language-learning-specific alternative) — the
backend abstraction in §4.4 makes swapping a one-class change.

### 3.2 Browser SDK streaming vs server-side assessment

Azure also ships a **browser JS SDK** that streams the mic to Azure and scores near-real-time.
Rejected for MVP:

- Requires minting short-lived Azure tokens for every student (token endpoint, expiry handling)
  — a key-exposure surface that violates the spirit of N2.
- We must store the audio anyway (F4 replay/history), so the upload happens regardless.
- It bypasses our backend, so mock-mode dev/test parity (N4) dies.

Server-side assessment of the uploaded file keeps one code path for mock and real.

### 3.3 File storage & audio pipeline (the deferred upload decision)

This feature forces real files. Today every audio/file field in `core/models.py` is a mock
string (`StudyMaterial.file_url`, `Exercise.audio_prompt_url`, `Submission.audio_recording_url`)
and `config/settings.py` defines no `MEDIA_ROOT`.

**Storage: Django `FileField` + `MEDIA_ROOT` on local disk for MVP → `django-storages` + S3
later.** Standard Django layering means model code never changes when we flip
`STORAGES["default"]` to `storages.backends.s3.S3Storage`. Alternatives rejected: direct-to-S3
presigned uploads (needless AWS coupling before a bucket even exists) and audio blobs in
Postgres (bloats backups, no range/streaming support).

**Recording format — two viable pipelines:**

| Pipeline | How | Pros | Cons |
|---|---|---|---|
| **A. Native `MediaRecorder` → server transcode (recommended)** | Browser records webm/opus (Chrome/Firefox) or mp4/aac (Safari); server runs `ffmpeg -i in -ar 16000 -ac 1 -sample_fmt s16 out.wav` | Zero frontend deps; small uploads (opus ≈ 20 KB per 10 s); one normalization point absorbs *all* browser codec differences | Server needs the `ffmpeg` binary (Docker apt-install; routine ops) |
| B. In-browser WAV via `extendable-media-recorder` + `extendable-media-recorder-wav-encoder` | JS re-encodes PCM to WAV client-side | Engine-ready-ish output, no ffmpeg | ~10× larger uploads (16-bit 48 kHz WAV ≈ 1 MB per 10 s); still cannot guarantee 16 kHz mono — `MediaRecorder`/`getUserMedia` cannot set the capture sample rate, so a server resample step survives anyway; adds a frontend dependency. https://github.com/chrisguttandin/extendable-media-recorder · https://media-codings.com/articles/recording-cross-browser-compatible-media |

Pipeline A wins: since the server must validate and resample regardless (N6), doing *all*
conversion server-side with ffmpeg is simpler and cheaper on bandwidth. Call `ffmpeg` via
`subprocess` directly rather than through `pydub` — pydub wraps the same binary, adding a
dependency without removing the ops requirement.

### 3.4 Sync vs async

Azure scripted assessment on ≤ 15 s audio returns in ~1–3 s. MVP runs it **synchronously inside
the POST**, mirroring the documented stance in `core/ai/service.py` ("runs SYNCHRONOUSLY today
but is the natural enqueue boundary"). The function `assess_attempt(attempt)` in the new
`core/ai/pronunciation.py` is the future Celery task body; the view is its only caller. If p95
latency or Azure throttling bites, add Celery + a `processing` attempt state and frontend
polling — no model change needed (score fields are already nullable).

---

## 4. Design

### 4.1 Models (`core/models.py`)

Follows existing conventions: explicit `db_table`, `TextChoices`, nullable FK + `SET_NULL` for
authorship, `CASCADE` for ownership. Reuses the existing `DifficultyLevel` choices.

```python
class DrillType(models.TextChoices):
    WORD = "word", "Word"
    SENTENCE = "sentence", "Sentence"
    MINIMAL_PAIR = "minimal_pair", "Minimal Pair"


class PronunciationDrill(models.Model):
    """A repeatable pronunciation target. Deliberately NOT an Exercise row:
    drills are an unlimited low-stakes practice loop, not gradeable coursework
    (see §6 'deliberately not reused')."""

    target_text = models.CharField(max_length=255)
    # Minimal-pair contrast ("ship" vs "sheep"); null for word/sentence drills.
    contrast_text = models.CharField(max_length=255, null=True, blank=True)
    # IPA hint shown to the student, e.g. "/ˈθʌr.oʊ/".
    phoneme_hint = models.CharField(max_length=255, null=True, blank=True)
    drill_type = models.CharField(max_length=20, choices=DrillType.choices)
    difficulty_level = models.CharField(
        max_length=20, choices=DifficultyLevel.choices, null=True, blank=True
    )
    module = models.ForeignKey(
        LearningModule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="pronunciation_drills",
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="pronunciation_drills_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pronunciation_drills"
        ordering = ["difficulty_level", "id"]


class PronunciationAttempt(models.Model):
    """One student recording of one drill, plus the engine's verdict.
    Score fields nullable so a future async path can create-then-fill."""

    drill = models.ForeignKey(
        PronunciationDrill, on_delete=models.CASCADE, related_name="attempts"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="pronunciation_attempts"
    )
    # FIRST REAL FILE FIELD IN THE PROJECT — requires MEDIA_ROOT (see §5 phase 0).
    audio_file = models.FileField(upload_to="pronunciation/%Y/%m/")
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    accuracy_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fluency_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    completeness_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    prosody_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Normalized per-word list; each word nests phonemes. Shape in §4.3.
    word_results = models.JSONField(null=True, blank=True)
    # Engine bookkeeping: which backend ("mock"/"azure"), result id, latency…
    engine = models.CharField(max_length=50, blank=True, default="")
    engine_metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pronunciation_attempts"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["student", "drill", "-created_at"])]
```

### 4.2 Endpoints

All in the root URLconf (`config/urls.py` — the project has no per-app `core/urls.py`; new
viewsets/paths join the existing `DefaultRouter` there), drf-spectacular-annotated so
`yarn gen:api` picks them up. Permission classes are the existing ones in `core/permissions.py`
(`IsActiveUser`, `IsTeacherOrAdmin`, `IsStudent`).

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/api/pronunciation/drills/` | any authenticated | Filters: `?drill_type=&difficulty=&module_id=` |
| POST | `/api/pronunciation/drills/` | teacher, admin | `created_by` set server-side |
| GET | `/api/pronunciation/drills/{id}/` | any authenticated | |
| PATCH / DELETE | `/api/pronunciation/drills/{id}/` | creator-teacher, admin | |
| POST | `/api/pronunciation/drills/{id}/attempts/` | student | **multipart/form-data**, field `audio`; validate size/type, transcode, assess, return the full scored attempt (201) |
| GET | `/api/pronunciation/drills/{id}/attempts/` | student (own rows only) | attempt history for the retry loop |
| GET | `/api/pronunciation/attempts/me/` | student | cross-drill history, `?drill_id=` filter |

Audio playback: the attempt serializer exposes `audio_url` from `attempt.audio_file.url`. MVP
serves media via a small authenticated file view (dev: `django.views.static.serve`); the S3
phase swaps in storage URLs. Students can only fetch their own attempts (queryset scoping, the
same ownership style as `can_view_submission_feedback` in `core/permissions.py`).

### 4.3 Serializers & the result JSON contract

`PronunciationDrillSerializer` — plain ModelSerializer; `module` in/out as `module_id` (matches
the `klass` → `class_id` renaming convention noted at the top of `core/models.py`).

`PronunciationAttemptSerializer` — read-mostly; the create path takes only the uploaded file
(drill from URL, student from auth). `word_results` normalized shape (engine-agnostic — both
mock and Azure map into it; the frontend zod-validates it; all example values synthetic):

```json
[
  {
    "word": "sheep",
    "accuracy": 61.0,
    "error_type": "Mispronunciation",
    "phonemes": [
      {"phoneme": "ʃ", "accuracy": 22.0},
      {"phoneme": "iː", "accuracy": 88.0},
      {"phoneme": "p", "accuracy": 95.0}
    ]
  }
]
```

`error_type` ∈ `None | Omission | Insertion | Mispronunciation` (Azure's miscue vocabulary,
which the mock also emits).

### 4.4 AI-backend integration (`core/ai/pronunciation.py`)

Mirrors `core/ai/backends.py` + `service.py` structurally — same STUB-vs-REAL banner, same
loud-failure stance, same `AiModel.active()` binding:

```python
class BasePronunciationBackend:
    def __init__(self, ai_model=None):
        self.ai_model = ai_model  # active AiModel row or None

    def assess(self, *, audio_path: str, reference_text: str) -> dict:
        """Returns {"overall": Decimal, "accuracy": Decimal, "fluency": Decimal,
        "completeness": Decimal, "prosody": Decimal | None,
        "words": [...§4.3 shape...], "metadata": {...}}"""
        raise NotImplementedError


class MockPronunciationBackend(BasePronunciationBackend):
    """DETERMINISTIC, no network. Scores derive from
    sha256(reference_text + audio byte-size), so tests/seed are stable; emits a
    plausible word/phoneme breakdown by splitting reference_text and assigning
    seeded per-phoneme accuracies. Applies the AiModel strictness factor the
    same way backends.py `_STRICTNESS_FACTOR` does."""


class AzurePronunciationBackend(BasePronunciationBackend):
    """REAL. azure-cognitiveservices-speech: SpeechConfig(key=env AZURE_SPEECH_KEY,
    region=env AZURE_SPEECH_REGION or ai_model.endpoint_url) +
    PronunciationAssessmentConfig(reference_text=..., granularity=Phoneme,
    phoneme_alphabet="IPA", enable_miscue=True) with prosody enabled.
    Raises ImproperlyConfigured until env keys exist — same loud-failure stance
    as RealBackend's NotImplementedError guards."""


def get_pronunciation_backend():
    name = getattr(settings, "PRONUNCIATION_BACKEND", "mock")  # env-driven like AI_BACKEND
    ai_model = AiModel.active()
    if name == "azure":
        return AzurePronunciationBackend(ai_model)
    return MockPronunciationBackend(ai_model)


def assess_attempt(attempt) -> None:
    """Transcode → assess → persist scores onto the attempt row. The single
    trigger point and the future Celery task body (same contract idea as
    service.evaluate_submission)."""
```

Strictness note: `AiModel.strictness` shifts the *display thresholds* (what counts as
red/yellow/green per phoneme) rather than rescaling Azure's calibrated scores; the serializer
passes `strictness` through so the frontend colors consistently.

### 4.5 Frontend summary

Full detail in `english-learning-web/docs/research/04-pronunciation-practice-frontend.md`.
Headlines: new `app/(app)/pronunciation/` hub + `pronunciation/[drillId]` player routes; a
`useRecorder()` hook over native `MediaRecorder` with a permission-state machine; upload as
`FormData` via a small `apiFetch` fix (`buildRequest` in `lib/api.ts` must not force
`Content-Type: application/json` onto FormData bodies); color-coded word/phoneme feedback
components; canvas level-meter instead of wavesurfer.js at MVP; new TanStack Query keys in
`lib/query-keys.ts`; zod schemas guarding the `word_results` JSON.

---

## 5. Implementation Guidelines

**Phase 0 — media plumbing (prerequisite, ~half day)**
1. `config/settings.py`: add `MEDIA_ROOT = BASE_DIR / "media"`, `MEDIA_URL = "/media/"`; dev
   static-serve block in `config/urls.py`.
2. Add `ffmpeg` to `docs/DEPLOYMENT.md` and the deployment image.
3. New `core/audio.py`: `transcode_to_wav16k(src_path) -> Path` (ffmpeg subprocess, timeout)
   and `validate_upload(file)` (≤ 5 MB, content-type sniff, ≤ 30 s duration via ffprobe).

**Phase 1 — MVP (models + mock engine + API)**
4. `core/models.py`: add `DrillType`, `PronunciationDrill`, `PronunciationAttempt`; migration.
5. `core/ai/pronunciation.py`: base + `MockPronunciationBackend` + `get_pronunciation_backend()`
   + `assess_attempt()`. Read `PRONUNCIATION_BACKEND` from env in `config/settings.py` beside
   `AI_BACKEND` (currently line 179).
6. `core/serializers.py`: drill + attempt serializers (attempt exposes `audio_url`,
   `word_results`, all score fields).
7. `core/views.py` + `config/urls.py` (router/path registration): endpoints per §4.2; multipart parser on attempt-create; student
   queryset scoping; DRF throttle on attempt-create (e.g. 30/hour/student).
8. Tests: drill CRUD permissions; attempt upload happy path with a small fixture webm; mock
   determinism (same file + text ⇒ same scores); student isolation on attempt reads.
9. Regenerate web types (`yarn gen:api`); `@extend_schema` the multipart endpoint so generated
   types are usable.

**Phase 2 — real engine**
10. `AzurePronunciationBackend` (`azure-cognitiveservices-speech` in `requirements.txt`); map
    Azure's `NBest[0].PronunciationAssessment` + `Words[].Phonemes[]` into §4.3's shape.
11. Seed an `AiModel` row (`model_name="azure-pronunciation"`, `endpoint_url=<region>`); admins
    swap engines exactly as with grading models.
12. Measure latency under load; if p95 > 4 s, introduce Celery here (task wraps
    `assess_attempt`).

**Phase 3 — enhancements**
13. `django-storages[s3]`: flip `STORAGES["default"]`; migrate existing files.
14. Teacher analytics: per-class phoneme-error aggregates read from `word_results`.
15. Optional: feed drill activity into `StudentModuleProgress` completion.

---

## 6. Integration with Existing Code

**Reused**
- `DifficultyLevel` choices and the `LearningModule` FK — drills sit beside `Exercise` rows in
  a module.
- `AiModel` registry + `AiModel.active()` — engine selection/strictness stays an admin concern;
  no new registry model.
- The `core/ai/` Mock/Real split, loud-failure stubs, and "service function = future task body"
  pattern from `backends.py` / `service.py` — copied structurally, not imported (grading and
  pronunciation return different shapes).
- `core/permissions.py` role classes; the serializer `*_id` renaming convention; explicit
  `db_table` naming.
- Frontend: `lib/api.ts` client, `lib/query-keys.ts` factory, `components/ui.tsx` primitives
  (see frontend doc).

**Deliberately NOT reused**
- **`Submission` / `Feedback`.** A drill attempt is not a submission: no `Assignment` linkage,
  no grading lifecycle (`pending/graded/ai_graded`), no teacher inbox, unlimited retries, and a
  structured-JSON payload rather than `writing_text`/`answers`. Cramming it into `Submission`
  would nullable-pollute that table, leak practice noise into `/api/submissions/inbox/`, and
  force `Feedback.comments` (free text) to smuggle structured phoneme data. Separate tables
  keep both lifecycles honest. A *graded* speaking task remains the existing `speaking`
  `Exercise` path — unchanged.
- **`Exercise` / `Question`.** Drills need none of the stimulus/question machinery, and
  `ExerciseType`'s semantics (productive skills "graded subjectively via Feedback") do not fit
  a machine-scored loop.
- **Quiz components** (`components/quiz/*`) — nothing question-shaped to render here.
- **The mock-URL-string convention** (`Submission.audio_recording_url` et al.) — intentionally
  broken with a real `FileField`. Existing mock-URL fields stay as-is and can migrate to real
  storage in a later feature.

---

## 7. Risks & Open Questions

| Risk / question | Notes / mitigation |
|---|---|
| Azure prosody score is en-US-only today | Keep `prosody_score` nullable; frontend renders prosody as optional. |
| Synchronous engine call occupies a worker for 1–3 s | Acceptable at MVP scale; Celery boundary pre-drawn at `assess_attempt`. Watch worker counts under daphne (the ASGI server in `requirements.txt` — there is no gunicorn). |
| ffmpeg becomes a hard deploy dependency | Small and well-trodden; document in DEPLOYMENT.md; fail loudly at boot if `PRONUNCIATION_BACKEND=azure` and ffmpeg is missing. |
| Mock ↔ Azure realism gap (mock cannot predict real phoneme errors) | Mock only needs *shape* fidelity for UI/tests; contract tests pin the normalized JSON shape, not the values. |
| Authenticated media serving from local disk is ad-hoc | Fine for MVP; S3 + presigned GET solves it properly in phase 3. Also decide audio retention (delete after N days?) — open. |
| Minimal pairs: Azure scores against `target_text` only | The contrast word is pedagogy/UI. If we later want "which word did you actually say", that needs unscripted mode or a second scripted pass — open question. |
| Azure SDK wants WAV/PCM; compressed input needs GStreamer | Avoided entirely by our own ffmpeg transcode — never feed compressed audio to the SDK. |
| Attempt spam / cost abuse | DRF throttle on the attempt endpoint (30/hour/student) + the 5 MB / 30 s upload caps. |
| `AiModel` registry now double-duty (grading + pronunciation) | MVP: a single active row is fine. If both real engines go live simultaneously, add a `purpose` field to `AiModel` — flagged now to avoid surprise. |

**Sources**
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-pronunciation-assessment
- https://azure.microsoft.com/en-us/pricing/details/speech/
- https://www.speechace.com/api-plans/ · https://api-docs.speechace.com/
- https://www.speechsuper.com/pricing.html
- https://elsaspeak.com/en/elsa-api/
- https://github.com/chrisguttandin/extendable-media-recorder
- https://media-codings.com/articles/recording-cross-browser-compatible-media

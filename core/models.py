"""Domain models, ported from the FastAPI/SQLAlchemy app.

Note on naming: a foreign key to ``Class`` is named ``klass`` on the model
(``class`` is a Python keyword). Serializers expose it to the API as
``class_id`` to keep the wire format unchanged.
"""

import json
import re
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone


# ---------- Enums ----------
class UserRole(models.TextChoices):
    STUDENT = "student", "Student"
    TEACHER = "teacher", "Teacher"
    ADMIN = "admin", "Admin"


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    INACTIVE = "inactive", "Inactive"


class DifficultyLevel(models.TextChoices):
    BEGINNER = "beginner", "Beginner"
    INTERMEDIATE = "intermediate", "Intermediate"
    ADVANCED = "advanced", "Advanced"


class BandLevel(models.TextChoices):
    """IELTS-style target band range for a module/exercise."""

    BAND_4_5 = "band_4_5", "Band 4–5"
    BAND_5_6 = "band_5_6", "Band 5–6"
    BAND_6_7 = "band_6_7", "Band 6–7"
    BAND_7_8 = "band_7_8", "Band 7–8"
    BAND_8_9 = "band_8_9", "Band 8–9"


class Topic(models.TextChoices):
    """Thematic topic for a module/exercise (catalog filtering)."""

    LIFE = "life", "Life"
    SPORTS = "sports", "Sports"
    EDUCATION = "education", "Education"
    WORK = "work", "Work"
    TRAVEL = "travel", "Travel"
    ENVIRONMENT = "environment", "Environment"
    TECHNOLOGY = "technology", "Technology"
    HEALTH = "health", "Health"
    CULTURE = "culture", "Culture"


class ExerciseType(models.TextChoices):
    # Productive skills: open-ended output, graded subjectively via Feedback.
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"
    # Receptive skills: consume a stimulus, answer fixed questions, auto-graded.
    READING = "reading", "Reading"        # stimulus = prompt_text (passage)
    LISTENING = "listening", "Listening"  # stimulus = audio_prompt_url (clip)
    QUIZ = "quiz", "Quiz"                  # no stimulus (vocab/grammar)


class SubmissionType(models.TextChoices):
    # Productive: payload in writing_text / audio_recording_url.
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"
    # Receptive: payload in `answers` JSON, auto-scored into `auto_score`.
    READING = "reading", "Reading"
    LISTENING = "listening", "Listening"
    QUIZ = "quiz", "Quiz"


class SubmissionStatus(models.TextChoices):
    # docs.md Enum submission_status. Lifecycle of grading, not the skill type.
    PENDING = "pending", "Pending"        # submitted, not yet graded
    GRADED = "graded", "Graded"           # a human teacher left Feedback
    AI_GRADED = "ai_graded", "AI Graded"  # an AI evaluator left Feedback


# ---------- User ----------
class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", UserRole.ADMIN)
        extra.setdefault("status", UserStatus.ACTIVE)
        extra.setdefault("full_name", "Admin")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra["is_staff"] is not True or extra["is_superuser"] is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, max_length=255, db_index=True)
    full_name = models.CharField(max_length=100)
    avatar_url = models.CharField(max_length=500, blank=True, null=True)
    role = models.CharField(max_length=20, choices=UserRole.choices)
    status = models.CharField(
        max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE
    )
    # Django auth plumbing — `status` drives app logic; is_active gates auth backend.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name", "role"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


# ---------- Class ----------
class Class(models.Model):
    class_name = models.CharField(max_length=100)
    # docs.md classes.teacher_id [not null]. PROTECT: a teacher with classes
    # cannot be deleted until their classes are reassigned.
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


class ClassStudent(models.Model):
    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="students"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="class_memberships"
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "class_students_lnk"
        unique_together = (("klass", "student"),)

    def __str__(self):
        return f"{self.student_id} in {self.klass_id}"


class LessonPlan(models.Model):
    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="lesson_plans"
    )
    title = models.CharField(max_length=255)
    objectives = models.TextField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lesson_plans"

    def __str__(self):
        return self.title


# ---------- Learning Module ----------
class LearningModule(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    difficulty_level = models.CharField(
        max_length=20, choices=DifficultyLevel.choices, null=True, blank=True
    )
    # Catalog grading/theming — nullable so legacy rows stay valid.
    band = models.CharField(
        max_length=20, choices=BandLevel.choices, null=True, blank=True
    )
    topic = models.CharField(
        max_length=20, choices=Topic.choices, null=True, blank=True
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="modules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learning_modules"

    def __str__(self):
        return self.title


class StudentModuleProgress(models.Model):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="module_progress"
    )
    module = models.ForeignKey(
        LearningModule, on_delete=models.CASCADE, related_name="progress"
    )
    completion_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "student_module_progress"
        unique_together = (("student", "module"),)

    def __str__(self):
        return f"{self.student_id}/{self.module_id}: {self.completion_percentage}%"


class Exercise(models.Model):
    module = models.ForeignKey(
        LearningModule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="exercises",
    )
    title = models.CharField(max_length=255)
    exercise_type = models.CharField(max_length=20, choices=ExerciseType.choices)
    # Catalog grading/theming — independent of the module's values so an
    # exercise can target a narrower band/topic than its module.
    band = models.CharField(
        max_length=20, choices=BandLevel.choices, null=True, blank=True
    )
    topic = models.CharField(
        max_length=20, choices=Topic.choices, null=True, blank=True
    )
    # docs.md exercises.instructions — kept as prompt_text.
    prompt_text = models.TextField()
    # docs.md exercises.content_text — reading passage body (receptive Reading).
    content_text = models.TextField(null=True, blank=True)
    # docs.md exercises.media_url — listening/speaking audio target.
    audio_prompt_url = models.CharField(max_length=500, null=True, blank=True)
    # docs.md exercises.created_by — direct authorship (module may be null).
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="exercises_created",
    )
    # Author's rubric pin (docs/research/02-scoring-rubrics.md FR3). Additive and
    # optional: null falls back to the active default for the exercise_type, then
    # to today's holistic grading. SET_NULL so retiring a template never blocks.
    rubric_template = models.ForeignKey(
        "RubricTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="exercises",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exercises"

    def __str__(self):
        return self.title


class Question(models.Model):
    """A single question on a receptive exercise (reading/listening/quiz).

    The stimulus (reading passage / listening audio) lives on the parent
    ``Exercise`` (``prompt_text`` / ``audio_prompt_url``); each Question hangs
    off that stimulus and carries its own options + answer key.
    """

    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="questions"
    )
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)
    # IELTS "NO MORE THAN TWO WORDS AND/OR A NUMBER" rubric: an over-long answer
    # is marked wrong even when it contains the key. Null = no limit, which is
    # every question authored before this field existed.
    max_words = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "questions"
        ordering = ["order", "id"]

    def __str__(self):
        return f"Q{self.order} of exercise {self.exercise_id}"


class QuestionOption(models.Model):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options"
    )
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "question_options"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.text} ({'correct' if self.is_correct else 'wrong'})"


class Assignment(models.Model):
    # docs.md assignments.class_id / exercise_id [not null]. Self-practice is
    # modelled on Submission.assignment (nullable), NOT on Assignment.
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

    class Meta:
        db_table = "assignments"

    def __str__(self):
        return f"Assignment {self.pk}"


# ---------- Answer-key helpers (shared by Submission and mock tests) ----------
# The `[[answer]]` fill-blank convention is documented in
# english-learning-web/docs/READING_LISTENING_QUIZ_PLAN.md and parsed on the
# client by lib/exercises.ts:parseFillBlank.
BLANK_RE = re.compile(r"\[\[(.*?)\]\]")


def blank_answer_key(question_text: str) -> list:
    """The non-empty ``[[answer]]`` keys in a question's text, in order. An
    all-empty result (``[[]]`` markers, as emitted by the answer-blind mock-test
    runner serializer) means "no key here", so it returns ``[]``.

    A key may list alternates separated by ``|`` — ``[[colour|color]]`` — so a
    40-question IELTS Reading section does not lose a half band to a spelling
    variant. Each element of the returned list is itself a list of accepted
    strings; a key with no ``|`` is simply a one-element list."""
    raw = [m.strip() for m in BLANK_RE.findall(question_text or "")]
    if not any(raw):
        return []
    return [
        [alt.strip() for alt in key.split("|") if alt.strip()] or [""]
        for key in raw
    ]


def strip_blank_answers(question_text: str) -> str:
    """Mask fill-blank keys while keeping the markers, so blank *count* survives
    but the answers do not: ``"The capital is [[Paris]]."`` -> ``"The capital is
    [[]]."``. Used by the mock-test runner serializer (research doc N1)."""
    return BLANK_RE.sub("[[]]", question_text or "")


def normalize_answers(answers) -> dict:
    """Fold either accepted ``answers`` wire shape into
    ``{question_id: {"option_ids": [...], "text": str | None}}``.

    See :meth:`Submission.grade_detail` for the two shapes.
    """
    out: dict = {}
    if not isinstance(answers, dict):
        return out

    responses = answers.get("responses")
    if isinstance(responses, list):  # envelope shape
        for resp in responses:
            if not isinstance(resp, dict):
                continue
            try:
                qid = int(resp.get("question_id"))
            except (TypeError, ValueError):
                continue
            option_id = resp.get("option_id")
            out[qid] = {
                "option_ids": [option_id] if option_id is not None else [],
                "text": resp.get("text"),
            }
        return out

    for key, picked in answers.items():  # legacy map shape
        try:
            qid = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(picked, (list, tuple)):
            option_ids = [p for p in picked if isinstance(p, int)]
        elif isinstance(picked, int):
            option_ids = [picked]
        else:
            option_ids = []
        out[qid] = {
            "option_ids": option_ids,
            "text": picked if isinstance(picked, str) else None,
        }
    return out


_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+")
_EDGE_PUNCTUATION = " .,;:!?\"'“”‘’"


def normalize_blank(value: str) -> str:
    """Fold a fill-blank string to its comparable form: collapsed whitespace,
    stripped edge punctuation, case-folded. Deliberately forgiving — at 40
    questions a trailing full stop must not cost half a band."""
    return _WHITESPACE_RE.sub(" ", value.strip()).strip(_EDGE_PUNCTUATION).casefold()


def _blank_equivalent(given: str, key: str) -> bool:
    """True if a student's blank matches one accepted key. Compares normalized
    forms, then retries with a leading article dropped from both sides so
    "the museum" and "museum" are the same answer."""
    given_norm, key_norm = normalize_blank(given), normalize_blank(key)
    if given_norm == key_norm:
        return True
    return (
        _LEADING_ARTICLE_RE.sub("", given_norm)
        == _LEADING_ARTICLE_RE.sub("", key_norm)
    )


def _blanks_match(keys: list, given_text, max_words=None) -> bool:
    """Compare a fill-blank response against its keys. The client stores blanks
    as a JSON array of strings (``FillBlankInput``); a bare string is accepted
    for a single-blank question.

    ``keys`` is the shape returned by :func:`blank_answer_key` — one list of
    accepted alternates per blank. Matching is whitespace/case/punctuation
    tolerant and article-insensitive (:func:`_blank_equivalent`).

    ``max_words`` applies the IELTS "NO MORE THAN N WORDS" rubric: an answer
    over the limit is wrong even when it contains the key, which is exactly how
    the real exam marks it.
    """
    if not keys:
        return False
    if isinstance(given_text, str):
        try:
            parsed = json.loads(given_text)
        except (ValueError, TypeError):
            parsed = [given_text]
        given = parsed if isinstance(parsed, list) else [parsed]
    elif isinstance(given_text, list):
        given = given_text
    else:
        return False
    if len(given) != len(keys):
        return False

    for response, alternates in zip(given, keys):
        if not isinstance(response, str):
            return False
        if max_words and len(normalize_blank(response).split()) > int(max_words):
            return False
        # An alternate may itself be a list (blank_answer_key's shape) or a bare
        # string (a hand-built key passed straight in).
        options = alternates if isinstance(alternates, (list, tuple)) else [alternates]
        if not any(_blank_equivalent(response, alt) for alt in options):
            return False
    return True


class Submission(models.Model):
    assignment = models.ForeignKey(
        Assignment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="submissions",
    )
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="submissions"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="submissions"
    )
    submission_type = models.CharField(max_length=20, choices=SubmissionType.choices)
    # Productive payload (writing/speaking).
    writing_text = models.TextField(null=True, blank=True)
    audio_recording_url = models.TextField(null=True, blank=True)
    # Receptive payload (reading/listening/quiz): {question_id: [option_id, ...]}.
    answers = models.JSONField(null=True, blank=True)
    auto_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    # docs.md submissions.status — grading lifecycle.
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "submissions"

    def __str__(self):
        return f"Submission {self.pk} by {self.student_id}"

    def grade_detail(self):
        """Auto-grade this submission's ``answers`` against the exercise's
        answer key and return ``{"correct": int, "total": int}``.

        ``total`` counts only *auto-gradable* questions — ones that carry a key:
        an option flagged ``is_correct``, or ``[[answer]]`` blanks in the
        question text. A bare short-answer question has no key and is excluded
        from both numbers (it is graded by a human/AI through ``Feedback``).

        Two ``answers`` wire shapes are accepted, because the web client and the
        original API disagree:

        * legacy map — ``{question_id: [selected_option_id, ...]}``
        * envelope — ``{"version": 1, "responses": [{"question_id", "type",
          "option_id", "text"}, ...]}`` (``lib/exercises.ts:buildAnswers``)

        Mock tests reuse this via ``core/mock_tests.py`` for the raw correct
        count of a receptive section, so both shapes must score identically.
        """
        questions = list(self.exercise.questions.prefetch_related("options"))
        by_question = normalize_answers(self.answers)

        correct = 0
        total = 0
        for q in questions:
            key_set = {o.id for o in q.options.all() if o.is_correct}
            blanks = blank_answer_key(q.text)
            if not key_set and not blanks:
                continue  # no answer key -> not auto-gradable
            total += 1
            given = by_question.get(q.id, {})
            if key_set:
                if set(given.get("option_ids", ())) == key_set:
                    correct += 1
            elif _blanks_match(blanks, given.get("text"), max_words=q.max_words):
                correct += 1
        return {"correct": correct, "total": total}

    def grade(self):
        """Auto-grade a receptive submission and set ``auto_score`` to the
        percentage of auto-gradable questions answered correctly (0–100), or
        ``None`` when the exercise has nothing auto-gradable. Thin wrapper over
        :meth:`grade_detail`."""
        detail = self.grade_detail()
        if not detail["total"]:
            self.auto_score = None
            return None

        from decimal import Decimal, ROUND_HALF_UP

        self.auto_score = (
            Decimal(100 * detail["correct"]) / Decimal(detail["total"])
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.auto_score


class Feedback(models.Model):
    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="feedback"
    )
    reviewer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="feedback_given",
    )
    score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    comments = models.TextField(null=True, blank=True)
    # docs.md evaluations.is_ai_generated.
    is_ai_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feedback"
        verbose_name_plural = "feedback"

    def __str__(self):
        return f"Feedback {self.pk} on {self.submission_id}"


# ---------- AI insights (Gemini coaching, core/ai/assist.py) ----------
class AiInsightKind(models.TextChoices):
    MISTAKE_EXPLANATION = "mistake_explanation", "Mistake explanation"
    FEEDBACK_REVIEW = "feedback_review", "Feedback review"


class AiInsight(models.Model):
    """A stored AI coaching result for a submission. ``payload`` is the
    schema-shaped JSON the assist backend returned (see ``core/ai/assist.py``
    MISTAKES_SCHEMA / REVIEW_SCHEMA). One row per generation; the API serves
    the newest for a (submission, kind) pair so regenerate == insert."""

    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="ai_insights"
    )
    kind = models.CharField(max_length=30, choices=AiInsightKind.choices)
    payload = models.JSONField()
    engine = models.CharField(max_length=80, blank=True, default="")
    requested_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ai_insights_requested",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_insights"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["submission", "kind", "-created_at"])]

    def __str__(self):
        return f"{self.kind} for submission {self.submission_id}"


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


# ---------- AI model registry (docs.md §4 ai_models / §5 Table 11) ----------
class GradingStrictness(models.TextChoices):
    """Admin-tunable rubric severity (RBAC row 13). The AI backend maps this to
    its scoring curve; the mock backend uses it to scale the deterministic score."""
    LENIENT = "lenient", "Lenient"
    STANDARD = "standard", "Standard"
    STRICT = "strict", "Strict"


class AiModel(models.Model):
    """A configurable multimodal-evaluation engine target. Admins register/edit
    rows here (UC-20/21); the AI service layer reads the active row to decide
    which endpoint/version/strictness to evaluate with. Only one row is treated
    as the live model — see ``active()`` below."""

    model_name = models.CharField(max_length=100)
    endpoint_url = models.CharField(max_length=255)
    version_identifier = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    strictness = models.CharField(
        max_length=20,
        choices=GradingStrictness.choices,
        default=GradingStrictness.STANDARD,
    )
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ai_models_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_models"

    def __str__(self):
        return f"{self.model_name} ({self.version_identifier})"

    @classmethod
    def active(cls):
        """The live model row, or ``None`` if none configured. Most-recently
        updated active row wins, so toggling ``is_active`` on a newer row swaps
        engines without disrupting in-flight evaluations (docs.md §5 Table 11)."""
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()


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


# ---------- Mock tests (docs/research/01-mock-tests.md) ----------
class SectionSkill(models.TextChoices):
    LISTENING = "listening", "Listening"
    READING = "reading", "Reading"
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"


class ItemFlow(models.TextChoices):
    """Whether a section's items (parts) are all available at once or gated."""
    FREE = "free", "All parts available at once"
    SEQUENTIAL = "sequential", "One part at a time, no going back"


# Receptive sections auto-grade from `answers`; productive ones wait for a
# Feedback row (teacher or AI) before they have a score.
RECEPTIVE_SKILLS = frozenset({SectionSkill.LISTENING, SectionSkill.READING})
PRODUCTIVE_SKILLS = frozenset({SectionSkill.WRITING, SectionSkill.SPEAKING})


class OverallStrategy(models.TextChoices):
    BAND_AVERAGE = "band_average", "Band average"  # IELTS: mean of bands -> nearest 0.5
    SCALED_SUM = "scaled_sum", "Scaled sum"        # TOEIC: sum of section scaled scores
    # Custom/in-house tests: plain mean of the per-section percentages. Half-band
    # rounding is an IELTS convention and means nothing on a 0-100 scale, so a
    # custom format must not borrow it.
    MEAN_PERCENT = "mean_percent", "Mean percent"


class TestFormat(models.Model):
    """Registry row per exam flavour (ielts_academic, toeic_lr, ...). Adding a
    format is data entry — a row here plus ScoreConversionTable rows — never a
    migration or a code fork."""

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    version = models.CharField(max_length=20, default="2026")  # pins conversion tables
    overall_strategy = models.CharField(
        max_length=20, choices=OverallStrategy.choices
    )
    score_precision = models.DecimalField(
        max_digits=4, decimal_places=2, default=0.5
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "test_formats"
        ordering = ["slug"]

    def __str__(self):
        return f"{self.name} ({self.version})"


class MockTestTemplate(models.Model):
    """A mock test in the platform's official library.

    Platform content, not classroom content: every authenticated user can see
    and sit every template, and admins curate the catalogue — the same shape as
    ``StudyMaterial``. There is deliberately no publish flag and no per-teacher
    ownership gate; a mock test is a feature of the app, like a practice book on
    a shelf, not something a teacher has to release.
    """

    format = models.ForeignKey(
        TestFormat, on_delete=models.PROTECT, related_name="templates"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    difficulty_level = models.CharField(
        max_length=20, choices=DifficultyLevel.choices, null=True, blank=True
    )
    # Provenance only (which admin added the row); never a visibility gate.
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="mock_tests_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mock_test_templates"
        ordering = ["-created_at", "id"]

    def __str__(self):
        return self.title


class TestSection(models.Model):
    template = models.ForeignKey(
        MockTestTemplate, on_delete=models.CASCADE, related_name="sections"
    )
    skill = models.CharField(max_length=20, choices=SectionSkill.choices)
    title = models.CharField(max_length=100)  # "Listening", "Academic Reading", ...
    order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField()  # IELTS 30/60/60/14; TOEIC 45/75
    instructions = models.TextField(null=True, blank=True)
    # How the student moves between the section's items (parts).
    #
    # ``free`` — every item is on screen at once and the student flips between
    # them. This is the real behaviour for IELTS Reading (3 passages, one 60-min
    # block) and Writing (2 tasks, one 60-min block).
    #
    # ``sequential`` — one item at a time, no going back. Real for Listening
    # (four recordings play in order, continuously) and Speaking (three parts of
    # an interview). Enforced server-side in ``core/mock_tests.py``; the client
    # only renders what the server already permits.
    item_flow = models.CharField(
        max_length=20, choices=ItemFlow.choices, default=ItemFlow.FREE
    )

    class Meta:
        db_table = "test_sections"
        ordering = ["order", "id"]
        unique_together = (("template", "order"),)

    def __str__(self):
        return f"{self.title} ({self.skill})"


class TestSectionExercise(models.Model):
    """Composition link: a section is an ordered list of existing Exercises.
    IELTS Reading = 3 reading Exercises (one per passage); IELTS Writing = 2
    writing Exercises (Task 1 / Task 2). Naming follows class_students_lnk."""

    section = models.ForeignKey(
        TestSection, on_delete=models.CASCADE, related_name="items"
    )
    exercise = models.ForeignKey(
        Exercise, on_delete=models.PROTECT, related_name="test_sections"
    )
    order = models.PositiveIntegerField(default=0)
    # Relative weight of this item inside its section's score. IELTS Writing
    # Task 2 is worth twice Task 1, so the Writing band is (T1 + 2*T2) / 3 —
    # see ``mock_tests.on_feedback_created``. Default 1 keeps every section
    # authored before this field a plain unweighted mean.
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    # Speaking-part timing (null everywhere else). IELTS Part 2 gives one minute
    # of preparation and then one to two minutes of talk; Parts 1 and 3 have no
    # preparation. Both are enforced server-side at submit.
    prep_seconds = models.PositiveIntegerField(null=True, blank=True)
    max_record_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "test_section_exercises_lnk"
        ordering = ["order", "id"]
        unique_together = (("section", "exercise"),)

    def __str__(self):
        return f"section {self.section_id} <- exercise {self.exercise_id}"


class ScoreConversionTable(models.Model):
    """Raw correct count (receptive) or Feedback score 0-100 (productive) ->
    band/scaled score. Admin-seeded data, versioned through ``format.version``.

    ``mapping`` is ``{str(raw): str(converted)}`` — IELTS listening
    ``{"39": "9.0", "37": "8.5", ...}``; TOEIC listening ``{"100": "495", ...}``.
    Tables may be sparse: lookup falls back to the nearest lower key
    (``core/mock_tests.py:convert_score``).
    """

    format = models.ForeignKey(
        TestFormat, on_delete=models.CASCADE, related_name="conversions"
    )
    skill = models.CharField(max_length=20, choices=SectionSkill.choices)
    mapping = models.JSONField()
    # Provenance: official ETS/IELTS raw->score tables are unpublished, so
    # seeded tables are prep-industry approximations. Reports label them
    # "estimated" on the strength of this note.
    source_note = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "score_conversion_tables"
        unique_together = (("format", "skill"),)

    def __str__(self):
        return f"{self.format.slug}/{self.skill}"


class AttemptStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"  # every section submitted
    ABANDONED = "abandoned", "Abandoned"


class AttemptMode(models.TextChoices):
    """How freely the student may move between sections.

    ``exam`` reproduces the real sitting: sections in the template's order, no
    skipping. ``practice`` lets a student begin with whichever skill they came
    to work on, which is the point of a practice library — someone with an hour
    for Writing should not have to sit 30 minutes of Listening first.

    Both still allow only one section running at a time, and both still need
    every section scored before an overall band appears. The mode changes the
    order, not the marking.
    """
    EXAM = "exam", "Exam order"
    PRACTICE = "practice", "Any order"


class SectionStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"


class TestAttempt(models.Model):
    template = models.ForeignKey(
        MockTestTemplate, on_delete=models.PROTECT, related_name="attempts"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="test_attempts"
    )
    status = models.CharField(
        max_length=20, choices=AttemptStatus.choices,
        default=AttemptStatus.IN_PROGRESS,
    )
    # Chosen when the attempt is created and fixed for its lifetime: switching
    # mid-sitting would let a student take the easy sections under practice
    # rules and then claim an exam-order result.
    mode = models.CharField(
        max_length=20, choices=AttemptMode.choices, default=AttemptMode.EXAM,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    overall_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "test_attempts"
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"Attempt {self.pk} by {self.student_id}"


class SectionAttempt(models.Model):
    attempt = models.ForeignKey(
        TestAttempt, on_delete=models.CASCADE, related_name="sections"
    )
    section = models.ForeignKey(
        TestSection, on_delete=models.PROTECT, related_name="attempts"
    )
    status = models.CharField(
        max_length=20, choices=SectionStatus.choices,
        default=SectionStatus.NOT_STARTED,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    # started_at + duration + grace. Server-authoritative: the client clock is
    # never trusted, and writes after this instant are rejected.
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Autosave envelope, one row-level UPDATE per save (no per-answer rows):
    # {"answers": {exercise_id: AnswersPayload},
    #  "writing": {exercise_id: text},
    #  "meta": {"audio_played": [exercise_id, ...]}}
    draft_answers = models.JSONField(null=True, blank=True)
    raw_score = models.PositiveIntegerField(null=True, blank=True)  # correct count
    raw_max = models.PositiveIntegerField(null=True, blank=True)    # gradable count
    converted_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "section_attempts"
        ordering = ["section__order", "id"]
        unique_together = (("attempt", "section"),)

    def __str__(self):
        return f"SectionAttempt {self.pk} ({self.status})"


class SectionSubmission(models.Model):
    """Links a SectionAttempt to the ordinary Submissions created at submit time
    (one per exercise in the section) — Submission itself stays untouched, so
    the teacher inbox and grade report keep working unchanged."""

    section_attempt = models.ForeignKey(
        SectionAttempt, on_delete=models.CASCADE, related_name="submissions"
    )
    submission = models.OneToOneField(
        Submission, on_delete=models.CASCADE, related_name="section_link"
    )

    class Meta:
        db_table = "section_submissions_lnk"

    def __str__(self):
        return f"section_attempt {self.section_attempt_id} <- sub {self.submission_id}"


# ---------- Inline writing annotations (docs/research/03-writing-annotations.md) ----------
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

    Anchoring: ``writing_text`` is immutable after submit (no update path in the
    API), so ``[start_offset, end_offset)`` — Unicode code points into that
    string — is a permanently exact anchor (W3C TextPositionSelector).
    ``quoted_text`` duplicates the sliced text (W3C TextQuoteSelector.exact) as a
    server-enforced integrity check and a display fallback.
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


# ---------- Scoring rubrics (docs/research/02-scoring-rubrics.md) ----------
class RubricAggregation(models.TextChoices):
    """How a full set of criterion scores becomes one overall value.

    Data-driven because the IELTS task-level rounding convention is not
    officially published (see research doc 02 §3.1)."""
    MEAN_DOWN_HALF = "mean_down_half", "Mean, rounded DOWN to nearest 0.5 (IELTS convention)"
    MEAN_NEAREST_HALF = "mean_nearest_half", "Mean, rounded to nearest 0.5"
    MEAN = "mean", "Mean (2 decimal places)"
    SUM = "sum", "Sum of criterion scores"


class RubricTemplate(models.Model):
    """A named scoring matrix (e.g. 'IELTS Writing Task 2'). Retire with
    is_active=False — never delete, historic CriterionScores reference it."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True)  # seed idempotency key
    description = models.TextField(null=True, blank=True)
    exercise_type = models.CharField(max_length=20, choices=ExerciseType.choices)
    is_default_for_type = models.BooleanField(default=False)
    # Band scale shared by all criteria in the template.
    scale_min = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    scale_max = models.DecimalField(max_digits=5, decimal_places=1, default=9)
    score_step = models.DecimalField(max_digits=4, decimal_places=1, default=1)
    aggregation = models.CharField(
        max_length=20,
        choices=RubricAggregation.choices,
        default=RubricAggregation.MEAN_DOWN_HALF,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rubric_templates_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rubric_templates"
        constraints = [
            models.UniqueConstraint(
                fields=["exercise_type"],
                condition=models.Q(is_default_for_type=True, is_active=True),
                name="one_default_rubric_per_exercise_type",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @classmethod
    def resolve_for(cls, exercise):
        """Pin on the exercise, else active default for its type, else None."""
        pinned = exercise.rubric_template
        if pinned and pinned.is_active:
            return pinned
        return cls.objects.filter(
            exercise_type=exercise.exercise_type,
            is_default_for_type=True, is_active=True,
        ).first()

    def aggregate(self, values):
        """Native-scale overall from a complete list of criterion Decimals."""
        if self.aggregation == RubricAggregation.SUM:
            return sum(values)
        mean = sum(values) / Decimal(len(values))
        if self.aggregation == RubricAggregation.MEAN_DOWN_HALF:
            return (mean * 2).quantize(Decimal("1"), rounding=ROUND_FLOOR) / 2
        if self.aggregation == RubricAggregation.MEAN_NEAREST_HALF:
            return (mean * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2
        return mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def normalize(self, overall):
        """Native overall -> the platform-wide 0-100 Feedback.score."""
        span = self.scale_max - self.scale_min
        if not span:
            return Decimal("0.00")
        return ((overall - self.scale_min) / span * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)


class RubricCriterion(models.Model):
    template = models.ForeignKey(
        RubricTemplate, on_delete=models.CASCADE, related_name="criteria"
    )
    name = models.CharField(max_length=100)   # "Grammatical Range & Accuracy"
    code = models.SlugField(max_length=50)    # "grammatical_range" — stable analytics key
    description = models.TextField(null=True, blank=True)
    # future-proof; all seeds 1 (IELTS: equal weighting)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rubric_criteria"
        ordering = ["order", "id"]
        unique_together = (("template", "code"),)

    def __str__(self):
        return f"{self.name} ({self.code})"


class RubricBandDescriptor(models.Model):
    """One matrix cell: the official wording for a criterion at a band."""

    criterion = models.ForeignKey(
        RubricCriterion, on_delete=models.CASCADE, related_name="band_descriptors"
    )
    band_value = models.DecimalField(max_digits=5, decimal_places=1)  # 7.0, or 6 for "Level 6 (C1)"
    label = models.CharField(max_length=50, blank=True)  # "Band 7" / "Level 8 (190-200)" / "C1"
    descriptor = models.TextField()

    class Meta:
        db_table = "rubric_band_descriptors"
        ordering = ["band_value", "id"]
        unique_together = (("criterion", "band_value"),)

    def __str__(self):
        return f"{self.criterion_id} @ {self.band_value}"


class CriterionScore(models.Model):
    """A teacher's (or AI's) score for one criterion, attached to the existing
    Feedback row — Feedback stays the unit of 'one review pass'."""

    feedback = models.ForeignKey(
        Feedback, on_delete=models.CASCADE, related_name="criterion_scores"
    )
    # PROTECT: retiring a template must not orphan historic grades.
    criterion = models.ForeignKey(
        RubricCriterion, on_delete=models.PROTECT, related_name="scores"
    )
    score = models.DecimalField(max_digits=5, decimal_places=1)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "criterion_scores"
        unique_together = (("feedback", "criterion"),)

    def __str__(self):
        return f"{self.criterion_id}={self.score} on feedback {self.feedback_id}"


# ---------- Pronunciation practice (docs/research/04-pronunciation-practice.md) ----------
class DrillType(models.TextChoices):
    WORD = "word", "Word"
    SENTENCE = "sentence", "Sentence"
    MINIMAL_PAIR = "minimal_pair", "Minimal Pair"


class PronunciationDrill(models.Model):
    """A repeatable pronunciation target. Deliberately NOT an Exercise row:
    drills are an unlimited low-stakes practice loop, not gradeable coursework
    (see research doc 04 §6 'deliberately not reused')."""

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

    def __str__(self):
        return f"{self.get_drill_type_display()}: {self.target_text}"


class PronunciationAttempt(models.Model):
    """One student recording of one drill, plus the engine's verdict.
    Score fields nullable so a future async path can create-then-fill."""

    drill = models.ForeignKey(
        PronunciationDrill, on_delete=models.CASCADE, related_name="attempts"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="pronunciation_attempts"
    )
    # FIRST REAL FILE FIELD IN THE PROJECT — requires MEDIA_ROOT (see research
    # doc 04 §5 phase 0 / config/settings.py).
    audio_file = models.FileField(upload_to="pronunciation/%Y/%m/")
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    accuracy_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fluency_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    completeness_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    prosody_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Normalized per-word list; each word nests phonemes. Shape in research doc §4.3.
    word_results = models.JSONField(null=True, blank=True)
    # Engine bookkeeping: which backend ("mock"/"azure"), result id, latency…
    engine = models.CharField(max_length=50, blank=True, default="")
    engine_metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pronunciation_attempts"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["student", "drill", "-created_at"])]

    def __str__(self):
        return f"Attempt {self.pk} by {self.student_id} on drill {self.drill_id}"


# ---------- Meeting ----------
class MeetingStatus(models.TextChoices):
    SCHEDULED = "scheduled"  # booked for a future scheduled_at, nobody joined yet
    ACTIVE = "active"
    ENDED = "ended"


class Meeting(models.Model):
    """A live 1:1 teacher-student video room (MVP demo). The row only carries
    room metadata; media flows peer-to-peer via WebRTC, with SDP/ICE signaling
    relayed over ws/meetings/<id>/ (core.consumers.MeetingSignalConsumer).

    Three timestamps drive the UI clock: `scheduled_at` (the teacher's booking,
    null for start-now rooms), `started_at` (stamped when the first peer
    actually joins) and `ended_at`. The live timer counts from started_at; the
    post-meeting duration is ended_at - started_at."""

    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="meetings"
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="meetings_created",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=MeetingStatus.choices, default=MeetingStatus.ACTIVE
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "meetings"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def duration_seconds(self):
        """Final call length once ended, live elapsed while running, None
        before the first peer joins."""
        if self.started_at is None:
            return None
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds())

"""DRF serializers — port of the FastAPI Pydantic schemas.

FK fields are surfaced as ``*_id`` on the wire to match the original API
(e.g. the ``klass`` model field is exposed as ``class_id``).
"""

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
# pyrefly: ignore [missing-import]
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from . import media, mock_tests
from .models import (
    AiInsight,
    Assignment,
    Class,
    ClassStudent,
    CriterionScore,
    Exercise,
    AttemptMode,
    Feedback,
    ItemFlow,
    LearningModule,
    LessonPlan,
    MockTestTemplate,
    PronunciationAttempt,
    PronunciationDrill,
    Question,
    QuestionOption,
    RubricBandDescriptor,
    RubricCriterion,
    RubricTemplate,
    ScoreConversionTable,
    SectionAttempt,
    SectionStatus,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    TestAttempt,
    TestFormat,
    TestSection,
    TestSectionExercise,
    User,
    UserRole,
    WritingAnnotation,
    StudyMaterial,
    AiModel,
    SystemLog,
    strip_blank_answers,
)

# Roles a user may self-assign at registration. Admin is provisioned only
# (seed / createsuperuser), never self-signup — docs.md UC-01, UI_FLOW_BRIEF:75.
SELF_SIGNUP_ROLES = (UserRole.STUDENT, UserRole.TEACHER)


# ---------- Auth / User ----------
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "email", "full_name", "avatar_url",
            "role", "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


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


class UserUpdateSerializer(serializers.ModelSerializer):
    """Self-service / admin update. `status` is only honoured for admins
    (enforced in the view)."""

    class Meta:
        model = User
        fields = ["full_name", "avatar_url", "status"]
        extra_kwargs = {
            "full_name": {"required": False},
            "avatar_url": {"required": False},
            "status": {"required": False},
        }


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


# ---------- Class ----------
class ClassSerializer(serializers.ModelSerializer):
    # Class.teacher_id is NOT NULL in database; drop allow_null=True
    teacher_id = serializers.PrimaryKeyRelatedField(
        source="teacher", queryset=User.objects.all(),
        required=False,
    )

    class Meta:
        model = Class
        fields = ["id", "class_name", "teacher_id", "academic_year", "created_at"]
        read_only_fields = ["id", "created_at"]


class ClassStudentSerializer(serializers.ModelSerializer):
    class_id = serializers.PrimaryKeyRelatedField(source="klass", read_only=True)
    student_id = serializers.PrimaryKeyRelatedField(source="student", read_only=True)

    class Meta:
        model = ClassStudent
        fields = ["id", "class_id", "student_id", "joined_at"]


class EnrollSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()


# ---------- Lesson Plan ----------
class LessonPlanSerializer(serializers.ModelSerializer):
    # Derived from the URL (/classes/{id}/lesson-plans/), so read-only here.
    class_id = serializers.PrimaryKeyRelatedField(source="klass", read_only=True)

    class Meta:
        model = LessonPlan
        fields = [
            "id", "class_id", "title", "objectives",
            "start_date", "end_date", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------- Learning Module ----------
class LearningModuleSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = LearningModule
        fields = [
            "id", "title", "description", "difficulty_level",
            "created_by", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]


class ProgressUpsertSerializer(serializers.Serializer):
    module_id = serializers.IntegerField()
    completion_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=Decimal("0"), max_value=Decimal("100"),
    )


class ProgressSerializer(serializers.ModelSerializer):
    student_id = serializers.PrimaryKeyRelatedField(source="student", read_only=True)
    module_id = serializers.PrimaryKeyRelatedField(source="module", read_only=True)

    class Meta:
        model = StudentModuleProgress
        fields = [
            "id", "student_id", "module_id",
            "completion_percentage", "last_accessed_at",
        ]


# ---------- Exercise / Questions ----------
class QuestionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ["id", "text", "is_correct", "order"]
        read_only_fields = ["id"]


class QuestionSerializer(serializers.ModelSerializer):
    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all()
    )
    options = QuestionOptionSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = [
            "id", "exercise_id", "text", "order", "max_words",
            "options", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ExerciseSerializer(serializers.ModelSerializer):
    module_id = serializers.PrimaryKeyRelatedField(
        source="module", queryset=LearningModule.objects.all(),
        required=False, allow_null=True,
    )
    # Authorship — set by the view on create, read-only on the wire.
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    # Present only on receptive exercises (reading/listening/quiz).
    questions = QuestionSerializer(many=True, read_only=True)
    # Author's rubric pin (docs/research/02-scoring-rubrics.md FR3). Optional.
    rubric_template_id = serializers.PrimaryKeyRelatedField(
        source="rubric_template", queryset=RubricTemplate.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = Exercise
        fields = [
            "id", "module_id", "title", "exercise_type",
            "prompt_text", "content_text", "audio_prompt_url",
            "created_by", "questions", "rubric_template_id", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]


# ---------- Assignment ----------
class AssignmentSerializer(serializers.ModelSerializer):
    # class_id derived from URL; assigned_by set to the requesting teacher.
    class_id = serializers.PrimaryKeyRelatedField(source="klass", read_only=True)
    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = Assignment
        fields = [
            "id", "class_id", "exercise_id",
            "assigned_by", "due_date", "created_at",
        ]
        read_only_fields = ["id", "assigned_by", "created_at"]


# ---------- Submission ----------
class SubmissionSerializer(serializers.ModelSerializer):
    # `audio_recording_url` stays the raw stored value (the client writes it on
    # create). `audio_url` is the playable, signed form of the same file —
    # clients should render this one.
    audio_url = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_audio_url(self, obj):
        if not obj.audio_recording_url:
            return None
        return (
            media.signed_url(obj.audio_recording_url, self.context.get("request"))
            or obj.audio_recording_url  # external URL we did not store: pass through
        )

    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all()
    )
    assignment_id = serializers.PrimaryKeyRelatedField(
        source="assignment", queryset=Assignment.objects.all(),
        required=False, allow_null=True,
    )
    student_id = serializers.PrimaryKeyRelatedField(source="student", read_only=True)

    class Meta:
        model = Submission
        fields = [
            "id", "exercise_id", "assignment_id", "submission_type",
            "writing_text", "audio_recording_url",
            "answers", "auto_score", "status", "student_id", "submitted_at",
            "audio_url",
        ]
        read_only_fields = [
            "id", "student_id", "auto_score", "status", "submitted_at", "audio_url",
        ]


# ---------- Feedback + rubric criterion scores (docs/research/02-scoring-rubrics.md) ----------
class CriterionScoreSerializer(serializers.ModelSerializer):
    criterion_id = serializers.PrimaryKeyRelatedField(
        source="criterion", queryset=RubricCriterion.objects.all()
    )

    class Meta:
        model = CriterionScore
        fields = ["id", "criterion_id", "score", "note"]
        read_only_fields = ["id"]


class FeedbackSerializer(serializers.ModelSerializer):
    submission_id = serializers.PrimaryKeyRelatedField(
        source="submission", queryset=Submission.objects.all()
    )
    reviewer_id = serializers.PrimaryKeyRelatedField(source="reviewer", read_only=True)
    score = serializers.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=Decimal("0"), max_value=Decimal("100"),
        required=False, allow_null=True,
    )
    # Optional rubric matrix. When present the server computes the overall and
    # writes the normalized 0-100 value into `score` (client score is ignored).
    criterion_scores = CriterionScoreSerializer(many=True, required=False)
    rubric_template_id = serializers.SerializerMethodField()
    rubric_overall = serializers.SerializerMethodField()  # native scale, e.g. "6.5"

    class Meta:
        model = Feedback
        fields = [
            "id", "submission_id", "reviewer_id",
            "score", "comments", "is_ai_generated", "created_at",
            "criterion_scores", "rubric_template_id", "rubric_overall",
        ]
        read_only_fields = ["id", "reviewer_id", "is_ai_generated", "created_at"]

    @staticmethod
    def _scored_template(obj):
        scores = list(obj.criterion_scores.all())
        return scores[0].criterion.template if scores else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_rubric_template_id(self, obj):
        template = self._scored_template(obj)
        return template.id if template else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_rubric_overall(self, obj):
        scores = list(obj.criterion_scores.all())
        if not scores:
            return None
        template = scores[0].criterion.template
        return str(template.aggregate([s.score for s in scores]))

    def validate(self, attrs):
        scores = attrs.get("criterion_scores")
        if not scores:
            return attrs
        submission = attrs.get("submission")
        if submission is None:
            raise serializers.ValidationError(
                "submission_id is required to attach criterion scores."
            )
        if submission.submission_type not in (
            SubmissionType.WRITING, SubmissionType.SPEAKING
        ):
            raise serializers.ValidationError(
                "Rubric grading applies to writing/speaking submissions only."
            )
        template = RubricTemplate.resolve_for(submission.exercise)
        if template is None:
            raise serializers.ValidationError(
                "No rubric template is configured for this exercise."
            )
        expected_ids = [c.id for c in template.criteria.all()]
        given_ids = [row["criterion"].id for row in scores]
        if any(cid not in expected_ids for cid in given_ids):
            raise serializers.ValidationError(
                "All criterion scores must belong to the exercise's resolved "
                "rubric template."
            )
        # Complete set, each criterion exactly once (unique_together guards the
        # DB; this gives the friendly 400).
        if sorted(given_ids) != sorted(expected_ids):
            raise serializers.ValidationError(
                "Provide exactly one score for every criterion of the rubric "
                "template."
            )
        step = template.score_step
        for row in scores:
            value = row["score"]
            if value < template.scale_min or value > template.scale_max:
                raise serializers.ValidationError(
                    f"Score {value} is outside the template scale "
                    f"[{template.scale_min}, {template.scale_max}]."
                )
            if step and (value - template.scale_min) % step != 0:
                raise serializers.ValidationError(
                    f"Score {value} is not a multiple of the score step {step}."
                )
        attrs["_rubric_template"] = template
        return attrs

    def create(self, validated_data):
        scores_data = validated_data.pop("criterion_scores", None)
        template = validated_data.pop("_rubric_template", None)
        if not scores_data:
            validated_data.pop("_rubric_template", None)
            return super().create(validated_data)
        with transaction.atomic():
            # Server-authoritative (NFR3): any client-supplied score is ignored
            # when a rubric matrix is present.
            validated_data.pop("score", None)
            feedback = Feedback.objects.create(**validated_data)
            CriterionScore.objects.bulk_create([
                CriterionScore(
                    feedback=feedback,
                    criterion=row["criterion"],
                    score=row["score"],
                    note=row.get("note"),
                )
                for row in scores_data
            ])
            overall = template.aggregate([row["score"] for row in scores_data])
            feedback.score = template.normalize(overall)
            feedback.save(update_fields=["score"])
        return feedback


# ---------- AI coaching (core/ai/assist.py) ----------
class CriterionScoreDraftSerializer(serializers.Serializer):
    """A rubric cell in a *draft* grade (nothing is written)."""
    criterion_id = serializers.PrimaryKeyRelatedField(
        source="criterion", queryset=RubricCriterion.objects.all()
    )
    score = serializers.DecimalField(max_digits=5, decimal_places=1)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class FeedbackReviewRequestSerializer(serializers.Serializer):
    """Teacher's draft feedback to be reviewed by the AI before (or after) posting."""
    score = serializers.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=Decimal("0"), max_value=Decimal("100"),
        required=False, allow_null=True,
    )
    comments = serializers.CharField(required=False, allow_blank=True, default="")
    criterion_scores = CriterionScoreDraftSerializer(many=True, required=False)

    def validate(self, attrs):
        if not (attrs.get("comments") or "").strip() and attrs.get("score") is None \
                and not attrs.get("criterion_scores"):
            raise serializers.ValidationError(
                "Provide comments, a score, or criterion scores to review."
            )
        return attrs


class AiRecommendationSerializer(serializers.Serializer):
    area = serializers.CharField()  # specificity|tone|actionability|accuracy|coverage|score_alignment|language_level
    issue = serializers.CharField()
    suggestion = serializers.CharField()


class FeedbackReviewSerializer(serializers.Serializer):
    """Response of POST /submissions/{id}/ai-review-feedback/ (not persisted)."""
    summary = serializers.CharField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    strengths = serializers.ListField(child=serializers.CharField())
    recommendations = AiRecommendationSerializer(many=True)
    score_alignment = serializers.CharField()
    suggested_comment = serializers.CharField()
    engine = serializers.CharField()


class AiMistakeSerializer(serializers.Serializer):
    location = serializers.CharField()
    student_answer = serializers.CharField(allow_blank=True)
    correction = serializers.CharField(allow_blank=True)
    category = serializers.CharField()  # grammar|vocabulary|spelling|punctuation|coherence|task_response|comprehension|inference|detail|other
    explanation = serializers.CharField()
    tip = serializers.CharField(allow_blank=True)


class MistakeExplanationSerializer(serializers.Serializer):
    summary = serializers.CharField()
    mistakes = AiMistakeSerializer(many=True)
    strengths = serializers.ListField(child=serializers.CharField())
    practice_suggestions = serializers.ListField(child=serializers.CharField())
    engine = serializers.CharField()


class AiInsightSerializer(serializers.ModelSerializer):
    """A stored coaching result. ``payload`` is MistakeExplanation-shaped for
    kind=mistake_explanation (the only kind persisted today)."""
    submission_id = serializers.PrimaryKeyRelatedField(source="submission", read_only=True)
    payload = MistakeExplanationSerializer(read_only=True)

    class Meta:
        model = AiInsight
        fields = ["id", "submission_id", "kind", "payload", "engine", "created_at"]
        read_only_fields = fields


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


# ---------- Submission Inbox ----------
class SubmissionInboxSerializer(SubmissionSerializer):
    """Read-only inbox row: existing submission fields + grading-state
    annotations derived from Feedback. Honors docs.md §6 directive #1 —
    grading attribution is decided by is_ai_generated, never by assuming a
    teacher reviewer exists."""

    is_graded = serializers.SerializerMethodField()
    grading_source = serializers.SerializerMethodField()    # "teacher" | "ai" | None
    latest_score = serializers.SerializerMethodField()
    student_name = serializers.CharField(
        source="student.full_name", read_only=True
    )

    class Meta(SubmissionSerializer.Meta):
        fields = SubmissionSerializer.Meta.fields + [
            "is_graded", "grading_source",
            "latest_score", "student_name",
        ]

    def _latest_feedback(self, obj):
        fbs = list(obj.feedback.all())
        return max(fbs, key=lambda f: (f.created_at, f.id)) if fbs else None

    def get_is_graded(self, obj):
        return bool(obj.feedback.all())

    def get_grading_source(self, obj):
        fb = self._latest_feedback(obj)
        if fb is None:
            return None
        return "ai" if fb.is_ai_generated else "teacher"

    def get_latest_score(self, obj):
        fb = self._latest_feedback(obj)
        return str(fb.score) if (fb and fb.score is not None) else None


# ---------- AI model registry ----------
class AiModelSerializer(serializers.ModelSerializer):
    # Server-set on write to the acting admin (view does perform_create/update);
    # surfaced read-only on the wire as updated_by_id.
    updated_by_id = serializers.PrimaryKeyRelatedField(
        source="updated_by", read_only=True
    )

    class Meta:
        model = AiModel
        fields = [
            "id", "model_name", "endpoint_url", "version_identifier",
            "is_active", "strictness", "updated_by_id", "updated_at",
        ]
        read_only_fields = ["id", "updated_by_id", "updated_at"]


# ---------- AI practice (RBAC row 12, UC-09) ----------
class AiPracticeSerializer(serializers.Serializer):
    """Ad-hoc student practice against an exercise — no Assignment required.
    Mirrors SubmissionSerializer's writable payload fields."""
    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all()
    )
    writing_text = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    audio_recording_url = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )


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
    """One normalized row in the admin activity feed (read-only)."""

    type = serializers.CharField()        # "system_log" | "user" | "submission" | "feedback"
    id = serializers.IntegerField()
    summary = serializers.CharField()
    timestamp = serializers.DateTimeField()


class HealthCheckSerializer(serializers.Serializer):
    """Anonymous liveness probe payload — see HealthCheckView."""

    status = serializers.CharField()        # "ok" | "degraded"
    database = serializers.CharField()      # "ok" | "error"
    db_latency_ms = serializers.FloatField()


class HealthSerializer(serializers.Serializer):
    """System health snapshot (read-only) — see AdminHealthView."""

    status = serializers.CharField()                  # "ok" | "degraded"
    database = serializers.CharField()                # "ok" | "error"
    db_latency_ms = serializers.FloatField()
    counts = serializers.DictField(child=serializers.IntegerField())
    server_time = serializers.DateTimeField()


# ---------- Mock tests (docs/research/01-mock-tests.md) ----------
class TestFormatSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestFormat
        fields = [
            "id", "slug", "name", "version",
            "overall_strategy", "score_precision", "is_active",
        ]
        read_only_fields = ["id"]


class ScoreConversionTableSerializer(serializers.ModelSerializer):
    format_id = serializers.PrimaryKeyRelatedField(source="format", read_only=True)

    class Meta:
        model = ScoreConversionTable
        fields = ["id", "format_id", "skill", "mapping", "source_note", "updated_at"]
        read_only_fields = ["id", "format_id", "updated_at"]


class TestSectionExerciseSerializer(serializers.ModelSerializer):
    """Authoring view of a section item — the exercise id plus enough metadata
    to render a template outline without fetching every exercise."""

    exercise_id = serializers.PrimaryKeyRelatedField(
        source="exercise", queryset=Exercise.objects.all()
    )
    exercise_title = serializers.CharField(source="exercise.title", read_only=True)
    exercise_type = serializers.CharField(
        source="exercise.exercise_type", read_only=True
    )
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = TestSectionExercise
        fields = [
            "id", "exercise_id", "exercise_title", "exercise_type",
            "question_count", "order", "weight",
            "prep_seconds", "max_record_seconds",
        ]
        read_only_fields = ["id"]

    def get_question_count(self, obj) -> int:
        return obj.exercise.questions.count()


class TestSectionSerializer(serializers.ModelSerializer):
    items = TestSectionExerciseSerializer(many=True, required=False)

    class Meta:
        model = TestSection
        fields = [
            "id", "skill", "title", "order",
            "duration_minutes", "instructions", "item_flow", "items",
        ]
        read_only_fields = ["id"]


class MockTestTemplateSerializer(serializers.ModelSerializer):
    """Library + authoring shape. Nested sections/items are writable so an admin
    can POST a whole test in one call (the composition *is* the test — there is
    nothing useful to create without it). No publish flag: every row in the
    library is live for every user."""

    format_id = serializers.PrimaryKeyRelatedField(
        source="format", queryset=TestFormat.objects.all()
    )
    format_slug = serializers.CharField(source="format.slug", read_only=True)
    format_name = serializers.CharField(source="format.name", read_only=True)
    created_by_id = serializers.PrimaryKeyRelatedField(
        source="created_by", read_only=True
    )
    sections = TestSectionSerializer(many=True, required=False)
    total_duration_minutes = serializers.SerializerMethodField()

    class Meta:
        model = MockTestTemplate
        fields = [
            "id", "format_id", "format_slug", "format_name", "title",
            "description", "difficulty_level", "created_by_id",
            "sections", "total_duration_minutes", "created_at",
        ]
        read_only_fields = ["id", "created_by_id", "created_at"]

    def get_total_duration_minutes(self, obj) -> int:
        return sum(section.duration_minutes for section in obj.sections.all())

    def create(self, validated_data):
        sections = validated_data.pop("sections", [])
        template = MockTestTemplate.objects.create(**validated_data)
        self._write_sections(template, sections)
        return template

    def update(self, instance, validated_data):
        sections = validated_data.pop("sections", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if sections is not None:
            # Sections are a composition, not independent resources: replacing
            # the list wholesale keeps ordering coherent. PROTECT on
            # SectionAttempt.section blocks this once anyone has sat the test.
            instance.sections.all().delete()
            self._write_sections(instance, sections)
        return instance

    @staticmethod
    def _write_sections(template, sections):
        for index, section_data in enumerate(sections):
            items = section_data.pop("items", [])
            section_data.setdefault("order", index)
            section = TestSection.objects.create(template=template, **section_data)
            for item_index, item in enumerate(items):
                item.setdefault("order", item_index)
                TestSectionExercise.objects.create(section=section, **item)


# ----- Answer-blind runner payload (research doc N1) -----
# The authoring serializers above expose `is_correct` and the raw `[[answer]]`
# keys. The runner MUST NOT: a student sitting a test can read any payload the
# browser receives. These are separate classes rather than a flag so no future
# edit can accidentally leak the key through the wrong code path.
class TestRunnerOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ["id", "text", "order"]      # deliberately no `is_correct`


class TestRunnerQuestionSerializer(serializers.ModelSerializer):
    options = TestRunnerOptionSerializer(many=True, read_only=True)
    text = serializers.SerializerMethodField()

    class Meta:
        model = Question
        # `max_words` is the IELTS "NO MORE THAN N WORDS" rubric. Safe to ship to
        # the runner — it constrains the answer, it does not hint at it — and the
        # client needs it to warn before an over-long answer is marked wrong.
        fields = ["id", "text", "order", "max_words", "options"]

    def get_text(self, obj) -> str:
        # Keeps the empty `[[]]` markers so blank *count* survives and the web
        # client's resolveQuestionType/FillBlankInput work unmodified.
        return strip_blank_answers(obj.text)


class TestRunnerExerciseSerializer(serializers.ModelSerializer):
    """One part of a live section. Per-part timing comes from the section's
    ``TestSectionExercise`` row, handed in through ``context["items"]`` as
    ``{exercise_id: TestSectionExercise}``, so the runner gets a flat payload
    instead of having to join the composition table itself."""

    questions = TestRunnerQuestionSerializer(many=True, read_only=True)
    prep_seconds = serializers.SerializerMethodField()
    max_record_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = [
            "id", "title", "exercise_type", "prompt_text",
            "content_text", "audio_prompt_url", "questions",
            "prep_seconds", "max_record_seconds",
        ]

    def _item(self, obj):
        return (self.context.get("items") or {}).get(obj.id)

    def get_prep_seconds(self, obj) -> int | None:
        item = self._item(obj)
        return item.prep_seconds if item else None

    def get_max_record_seconds(self, obj) -> int | None:
        item = self._item(obj)
        return item.max_record_seconds if item else None


class SectionAttemptSerializer(serializers.ModelSerializer):
    """One section of a live attempt. Exercises are inlined with the
    answer-blind serializer so the runner never has to call
    /api/exercises/{id}/ (which exposes the answer key)."""

    section_id = serializers.PrimaryKeyRelatedField(source="section", read_only=True)
    skill = serializers.CharField(source="section.skill", read_only=True)
    title = serializers.CharField(source="section.title", read_only=True)
    order = serializers.IntegerField(source="section.order", read_only=True)
    duration_minutes = serializers.IntegerField(
        source="section.duration_minutes", read_only=True
    )
    instructions = serializers.CharField(
        source="section.instructions", read_only=True
    )
    item_flow = serializers.CharField(source="section.item_flow", read_only=True)
    open_exercise_ids = serializers.SerializerMethodField()
    exercises = serializers.SerializerMethodField()

    class Meta:
        model = SectionAttempt
        fields = [
            "id", "section_id", "skill", "title", "order", "duration_minutes",
            "instructions", "item_flow", "open_exercise_ids", "status",
            "started_at", "expires_at", "completed_at", "draft_answers",
            "raw_score", "raw_max", "converted_score", "exercises",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_open_exercise_ids(self, obj):
        """Which parts the student may answer right now. A ``free`` section
        returns all of them; a ``sequential`` one returns exactly the part in
        hand. The server enforces this on write regardless — the field exists so
        the runner can render the rule rather than guess at it."""
        if obj.status == SectionStatus.NOT_STARTED:
            return []
        return mock_tests.open_item_ids(obj.section, obj.draft_answers)

    @extend_schema_field(TestRunnerExerciseSerializer(many=True))
    def get_exercises(self, obj):
        # Content is withheld until the student starts the section: shipping a
        # later section's passages early would hand out reading time for free.
        if obj.status == SectionStatus.NOT_STARTED:
            return []
        items = list(obj.section.items.all())
        return TestRunnerExerciseSerializer(
            [item.exercise for item in items],
            many=True,
            context={"items": {item.exercise_id: item for item in items}},
        ).data


class TestAttemptSerializer(serializers.ModelSerializer):
    """Attempt detail — the single source the runner resumes from.
    ``server_time`` lets the client correct its own clock skew instead of
    trusting `Date.now()` (research doc N3)."""

    template_id = serializers.PrimaryKeyRelatedField(
        source="template", read_only=True
    )
    template_title = serializers.CharField(source="template.title", read_only=True)
    format_slug = serializers.CharField(
        source="template.format.slug", read_only=True
    )
    student_id = serializers.PrimaryKeyRelatedField(source="student", read_only=True)
    sections = SectionAttemptSerializer(many=True, read_only=True)
    server_time = serializers.SerializerMethodField()

    class Meta:
        model = TestAttempt
        fields = [
            "id", "template_id", "template_title", "format_slug", "student_id",
            "status", "mode", "started_at", "completed_at", "overall_score",
            "sections", "server_time",
        ]
        read_only_fields = fields

    def get_server_time(self, obj) -> datetime:
        return timezone.now()


class TestAttemptListSerializer(serializers.ModelSerializer):
    """Lightweight row for the catalog's "my attempts" list and resume banner —
    no section payloads, just the two counts a progress bar needs. Sending the
    sections themselves would mean shipping every passage to render a bar."""

    template_id = serializers.PrimaryKeyRelatedField(
        source="template", read_only=True
    )
    template_title = serializers.CharField(source="template.title", read_only=True)
    format_slug = serializers.CharField(
        source="template.format.slug", read_only=True
    )
    sections_total = serializers.SerializerMethodField()
    sections_completed = serializers.SerializerMethodField()

    class Meta:
        model = TestAttempt
        fields = [
            "id", "template_id", "template_title", "format_slug",
            "status", "mode", "started_at", "completed_at", "overall_score",
            "sections_total", "sections_completed",
        ]
        read_only_fields = fields

    def get_sections_total(self, obj) -> int:
        return len(obj.sections.all())

    def get_sections_completed(self, obj) -> int:
        return sum(
            1 for section in obj.sections.all()
            if section.status == SectionStatus.COMPLETED
        )


class TestAttemptCreateSerializer(serializers.Serializer):
    """Body for starting an attempt. ``exam`` by default, so a client that says
    nothing gets the real sitting rather than the relaxed one."""

    mode = serializers.ChoiceField(
        choices=AttemptMode.choices, default=AttemptMode.EXAM,
        help_text=(
            "`exam` takes sections in the template's order; `practice` lets the "
            "student start with any section. Fixed once the attempt exists."
        ),
    )


class SectionDraftSerializer(serializers.Serializer):
    """Autosave body. The envelope is re-normalized server-side
    (core/mock_tests.py:normalize_draft), so field types stay loose here."""

    answers = serializers.DictField(required=False)
    writing = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False
    )
    meta = serializers.DictField(required=False)


class SectionAdvanceSerializer(serializers.Serializer):
    """Body for closing one part of a sequential section."""

    exercise_id = serializers.IntegerField()


class SectionSubmitSerializer(serializers.Serializer):
    """Submit body: an optional final draft flush plus speaking recordings.

    Recordings are attached only here, never autosaved — base64 data URLs would
    bloat every draft write (frontend doc risk 4).
    """

    draft = SectionDraftSerializer(required=False)
    recordings = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False,
        help_text="exercise_id -> audio recording URL / data URL",
    )


class SectionScoreSerializer(serializers.Serializer):
    """One row of the score report (read-only projection, not a model)."""

    section_attempt_id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    skill = serializers.CharField()
    status = serializers.CharField()
    raw_score = serializers.IntegerField(allow_null=True)
    raw_max = serializers.IntegerField(allow_null=True)
    converted_score = serializers.DecimalField(
        max_digits=6, decimal_places=2, allow_null=True
    )
    pending_grading = serializers.BooleanField()
    submission_ids = serializers.ListField(child=serializers.IntegerField())


class TestAttemptReportSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    template_id = serializers.IntegerField()
    template_title = serializers.CharField()
    format = serializers.CharField()
    status = serializers.CharField()
    overall_score = serializers.DecimalField(
        max_digits=6, decimal_places=2, allow_null=True
    )
    partial = serializers.BooleanField()
    # Conversion tables are prep-industry approximations — the report must say so.
    estimated = serializers.BooleanField()
    started_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    sections = SectionScoreSerializer(many=True)
    server_time = serializers.DateTimeField()


# ----- Portable test document (export / bulk import) -----
# Exercises travel by content, not by id. An id is only meaningful in the
# database that issued it, so an id-based export could not be imported anywhere
# — which is the entire point of having an export.
class DocumentOptionSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500)
    is_correct = serializers.BooleanField(default=False)
    order = serializers.IntegerField(default=0)


class DocumentQuestionSerializer(serializers.Serializer):
    text = serializers.CharField()
    order = serializers.IntegerField(default=0)
    max_words = serializers.IntegerField(required=False, allow_null=True)
    options = DocumentOptionSerializer(many=True, required=False)


class DocumentExerciseSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    exercise_type = serializers.CharField(max_length=20)
    prompt_text = serializers.CharField(allow_blank=True)
    content_text = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    audio_prompt_url = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=500
    )
    questions = DocumentQuestionSerializer(many=True, required=False)


class DocumentItemSerializer(serializers.Serializer):
    order = serializers.IntegerField(default=0)
    weight = serializers.DecimalField(max_digits=4, decimal_places=2, default=1)
    prep_seconds = serializers.IntegerField(required=False, allow_null=True)
    max_record_seconds = serializers.IntegerField(required=False, allow_null=True)
    exercise = DocumentExerciseSerializer()


class DocumentSectionSerializer(serializers.Serializer):
    skill = serializers.CharField(max_length=20)
    title = serializers.CharField(max_length=100)
    order = serializers.IntegerField(default=0)
    duration_minutes = serializers.IntegerField()
    instructions = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    item_flow = serializers.CharField(max_length=20, default=ItemFlow.FREE)
    items = DocumentItemSerializer(many=True)


class MockTestDocumentSerializer(serializers.Serializer):
    """One whole mock test as a self-contained JSON document.

    ``format_slug`` rather than ``format_id``: the slug is the stable name of an
    exam flavour across environments, an id is not. Import fails loudly if the
    slug is unknown — silently inventing a format would produce a test that
    scores against nothing.
    """

    format_slug = serializers.SlugField(max_length=50)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    difficulty_level = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=20
    )
    sections = DocumentSectionSerializer(many=True)


# ---------- Rubric templates (read) — docs/research/02-scoring-rubrics.md §4.3 ----------
class RubricBandDescriptorSerializer(serializers.ModelSerializer):
    class Meta:
        model = RubricBandDescriptor
        fields = ["id", "band_value", "label", "descriptor"]


class RubricCriterionSerializer(serializers.ModelSerializer):
    band_descriptors = RubricBandDescriptorSerializer(many=True, read_only=True)

    class Meta:
        model = RubricCriterion
        fields = [
            "id", "name", "code", "description", "weight", "order",
            "band_descriptors",
        ]


class RubricTemplateSerializer(serializers.ModelSerializer):
    criteria = RubricCriterionSerializer(many=True, read_only=True)

    class Meta:
        model = RubricTemplate
        fields = [
            "id", "name", "slug", "description", "exercise_type",
            "is_default_for_type", "scale_min", "scale_max", "score_step",
            "aggregation", "is_active", "criteria", "created_at",
        ]


# ---------- Inline writing annotations — docs/research/03-writing-annotations.md §4.3 ----------
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
        read_only_fields = [
            "id", "author_id", "is_acknowledged", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        submission = attrs.get("submission") or self.instance.submission
        start = attrs.get(
            "start_offset", getattr(self.instance, "start_offset", None)
        )
        end = attrs.get(
            "end_offset", getattr(self.instance, "end_offset", None)
        )

        if submission.submission_type != SubmissionType.WRITING or not submission.writing_text:
            raise serializers.ValidationError(
                "Annotations are only supported on writing submissions."
            )
        text = submission.writing_text
        if start is None or end is None or not (0 <= start < end <= len(text)):
            raise serializers.ValidationError(
                "Offsets out of range for writing_text."
            )

        # W3C TextQuoteSelector integrity check: the client's quote must equal the
        # server-side slice, else the client's offset mapping is buggy (e.g.
        # UTF-16 vs code points) and we refuse to store a drifted anchor.
        expected = text[start:end]
        quoted = attrs.get("quoted_text")
        if quoted is not None and quoted != expected:
            raise serializers.ValidationError(
                {"quoted_text": "Does not match writing_text[start:end] — offset mismatch."}
            )
        attrs["quoted_text"] = expected  # server value is canonical
        return attrs


# ---------- Pronunciation practice — docs/research/04-pronunciation-practice.md §4.3 ----------
class PronunciationDrillSerializer(serializers.ModelSerializer):
    module_id = serializers.PrimaryKeyRelatedField(
        source="module", queryset=LearningModule.objects.all(),
        required=False, allow_null=True,
    )
    created_by_id = serializers.PrimaryKeyRelatedField(
        source="created_by", read_only=True
    )

    class Meta:
        model = PronunciationDrill
        fields = [
            "id", "target_text", "contrast_text", "phoneme_hint",
            "drill_type", "difficulty_level", "module_id", "created_by_id",
            "created_at",
        ]
        read_only_fields = ["id", "created_by_id", "created_at"]


class PronunciationAttemptSerializer(serializers.ModelSerializer):
    """Read-mostly. The create path takes only the uploaded file (drill from the
    URL, student from auth), so no writable fields are exposed here."""

    drill_id = serializers.PrimaryKeyRelatedField(source="drill", read_only=True)
    student_id = serializers.PrimaryKeyRelatedField(source="student", read_only=True)
    audio_url = serializers.SerializerMethodField()

    class Meta:
        model = PronunciationAttempt
        fields = [
            "id", "drill_id", "student_id", "audio_url",
            "overall_score", "accuracy_score", "fluency_score",
            "completeness_score", "prosody_score", "word_results",
            "engine", "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_audio_url(self, obj):
        """Short-lived signed link (core/media.py). Plays in an <audio> tag,
        which cannot send an Authorization header, and expires afterwards."""
        if not obj.audio_file:
            return None
        return media.signed_url(obj.audio_file.name, self.context.get("request")) or None


class PronunciationAttemptCreateSerializer(serializers.Serializer):
    """Multipart upload body for a new attempt — a single audio file. Documented
    separately so drf-spectacular emits a multipart request schema."""

    audio = serializers.FileField()

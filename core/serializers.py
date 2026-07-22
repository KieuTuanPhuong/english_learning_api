"""DRF serializers — port of the FastAPI Pydantic schemas.

FK fields are surfaced as ``*_id`` on the wire to match the original API
(e.g. the ``klass`` model field is exposed as ``class_id``).
"""

from decimal import Decimal

from rest_framework import serializers

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
    Submission,
    User,
    UserRole,
    StudyMaterial,
    AiModel,
    SystemLog,
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
        fields = ["id", "exercise_id", "text", "order", "options", "created_at"]
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

    class Meta:
        model = Exercise
        fields = [
            "id", "module_id", "title", "exercise_type",
            "prompt_text", "content_text", "audio_prompt_url",
            "created_by", "questions", "created_at",
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
        ]
        read_only_fields = ["id", "student_id", "auto_score", "status", "submitted_at"]


# ---------- Feedback ----------
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

    class Meta:
        model = Feedback
        fields = [
            "id", "submission_id", "reviewer_id",
            "score", "comments", "is_ai_generated", "created_at",
        ]
        read_only_fields = ["id", "reviewer_id", "is_ai_generated", "created_at"]


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


class HealthSerializer(serializers.Serializer):
    """System health snapshot (read-only) — see AdminHealthView."""

    status = serializers.CharField()                  # "ok" | "degraded"
    database = serializers.CharField()                # "ok" | "error"
    db_latency_ms = serializers.FloatField()
    counts = serializers.DictField(child=serializers.IntegerField())
    server_time = serializers.DateTimeField()

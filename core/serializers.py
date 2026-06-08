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
)


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

    class Meta:
        model = User
        fields = ["id", "email", "password", "full_name", "avatar_url", "role"]
        read_only_fields = ["id"]

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
    teacher_id = serializers.PrimaryKeyRelatedField(
        source="teacher", queryset=User.objects.all(),
        required=False, allow_null=True,
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
    # Present only on receptive exercises (reading/listening/quiz).
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Exercise
        fields = [
            "id", "module_id", "title", "exercise_type",
            "prompt_text", "audio_prompt_url", "questions", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


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
            "answers", "auto_score", "student_id", "submitted_at",
        ]
        read_only_fields = ["id", "student_id", "auto_score", "submitted_at"]


# ---------- Feedback ----------
class FeedbackSerializer(serializers.ModelSerializer):
    submission_id = serializers.PrimaryKeyRelatedField(
        source="submission", queryset=Submission.objects.all()
    )
    reviewer_id = serializers.PrimaryKeyRelatedField(source="reviewer", read_only=True)

    class Meta:
        model = Feedback
        fields = [
            "id", "submission_id", "reviewer_id",
            "score", "comments", "created_at",
        ]
        read_only_fields = ["id", "reviewer_id", "created_at"]

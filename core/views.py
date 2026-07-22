"""DRF views — port of the FastAPI routers.

URL scheme adopts DRF conventions under /api/ (see config/urls.py). Role and
ownership rules mirror the original `require_roles` dependencies.
"""

import csv

from django.contrib.auth import authenticate
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
# pyrefly: ignore [missing-import]
from drf_spectacular.types import OpenApiTypes
# pyrefly: ignore [missing-import]
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
# pyrefly: ignore [missing-import]
from rest_framework_simplejwt.tokens import RefreshToken

from . import serializers as s
from .models import (
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    SubmissionStatus,
    User,
    UserRole,
    StudyMaterial,
    AiModel,
    SystemLog,
)
from .permissions import (
    IsActiveUser, IsAdmin, IsStudent, IsTeacherOrAdmin,
    can_view_submission_feedback,
)
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .ai import evaluate_submission

def _broadcast_class(class_id, payload):
    """Best-effort real-time push to a class group. Never breaks the HTTP request."""
    layer = get_channel_layer()
    if layer is None or class_id is None:
        return
    try:
        async_to_sync(layer.group_send)(
            f"class_{class_id}", {"type": "notify", "payload": payload}
        )
    except Exception:
        pass  # real-time is best-effort; the DB write already succeeded

# Standard permission stack shared by every authenticated endpoint.
_BASE = [IsAuthenticated, IsActiveUser]


def _perms(*extra):
    return [p() for p in (*_BASE, *extra)]


def _log_status_change(admin, target, new_status):
    """Write a SystemLog audit row for an admin-driven user status change.
    Only called from admin-gated write paths in UserViewSet."""
    SystemLog.objects.create(
        admin=admin,
        action_description=(
            f"Set status of user {target.id} ({target.email}) to '{new_status}'"
        ),
        target_status=new_status,
    )


# ============================================================ Auth
@extend_schema(
    tags=["auth"],
    summary="Register a new user",
    request=s.RegisterSerializer,
    responses={201: s.UserSerializer},
    auth=[],
)
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = s.RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if User.objects.filter(email=ser.validated_data["email"]).exists():
            raise ValidationError({"email": "Email already registered"})
        user = ser.save()
        return Response(s.UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["auth"],
    summary="Log in with email + password",
    request=s.LoginSerializer,
    responses={
        200: inline_serializer(
            name="TokenPair",
            fields={
                "access_token": drf_serializers.CharField(),
                "refresh_token": drf_serializers.CharField(),
                "token_type": drf_serializers.CharField(default="bearer"),
            },
        ),
        401: OpenApiResponse(description="Incorrect email or password"),
    },
    auth=[],
)
class LoginView(APIView):
    """Email + password -> JWT access/refresh pair."""

    permission_classes = [AllowAny]

    def post(self, request):
        ser = s.LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=ser.validated_data["email"],
            password=ser.validated_data["password"],
        )
        if user is None:
            raise AuthenticationFailed("Incorrect email or password")
        refresh = RefreshToken.for_user(user)
        return Response({
            "access_token": str(refresh.access_token),
            "refresh_token": str(refresh),
            "token_type": "bearer",
        })


# ============================================================ Users
@extend_schema(tags=["users"])
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("id")
    serializer_class = s.UserSerializer

    def get_queryset(self):
        qs = User.objects.all().order_by("id")
        if self.action != "list":
            return qs
        role = self.request.query_params.get("role")
        status_ = self.request.query_params.get("status")
        search = self.request.query_params.get("search")
        if role:
            qs = qs.filter(role=role)
        if status_:
            qs = qs.filter(status=status_)
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(full_name__icontains=search))
        return qs

    def get_permissions(self):
        if self.action in ("list", "destroy", "update", "partial_update"):
            return _perms(IsAdmin)
        return _perms()

    @extend_schema(
        summary="List users (admin) — filterable by role/status/search",
        parameters=[
            OpenApiParameter("role", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Filter by role: admin | teacher | student"),
            OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Filter by status: active | suspended | inactive"),
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY,
                             description="Case-insensitive match on email or full_name"),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Get or update the current user's own profile",
        request=s.UserUpdateSerializer,
        responses={200: s.UserSerializer},
    )
    @action(detail=False, methods=["get", "put", "patch"])
    def me(self, request):
        if request.method == "GET":
            return Response(s.UserSerializer(request.user).data)
        ser = s.UserUpdateSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        old_status = request.user.status
        if "status" in data and request.user.role != UserRole.ADMIN:
            data.pop("status")
        for field, value in data.items():
            setattr(request.user, field, value)
        request.user.save()
        if "status" in data and request.user.status != old_status:
            _log_status_change(request.user, request.user, request.user.status)
        return Response(s.UserSerializer(request.user).data)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.user.role != UserRole.ADMIN and request.user.id != obj.id:
            raise PermissionDenied("Forbidden")
        return Response(s.UserSerializer(obj).data)

    def update(self, request, *args, **kwargs):
        # Admin-only edit of an arbitrary user (mirrors admin_update_user).
        obj = self.get_object()
        old_status = obj.status
        ser = s.UserUpdateSerializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        # Audit any admin-driven status change (UC-19; docs.md §5 Table 12).
        if "status" in ser.validated_data and obj.status != old_status:
            _log_status_change(request.user, obj, obj.status)
        return Response(s.UserSerializer(obj).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


# ============================================================ Classes
@extend_schema(tags=["classes"])
class ClassViewSet(viewsets.ModelViewSet):
    serializer_class = s.ClassSerializer

    def get_queryset(self):
        qs = Class.objects.all().order_by("id")
        user = self.request.user
        if self.action == "list":
            if user.role == UserRole.TEACHER:
                return qs.filter(teacher=user)
            if user.role == UserRole.STUDENT:
                return qs.filter(students__student=user).distinct()
        return qs

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update"):
            return _perms(IsTeacherOrAdmin)
        if self.action == "destroy":
            return _perms(IsAdmin)
        return _perms()

    def perform_create(self, serializer):
        teacher = serializer.validated_data.get("teacher")
        if self.request.user.role == UserRole.TEACHER:
            teacher = self.request.user
        serializer.save(teacher=teacher)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if user.role == UserRole.TEACHER and obj.teacher_id != user.id:
            raise PermissionDenied("Not your class")
        ser = self.get_serializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        # Only admin may set/change the class teacher (mirrors the `status`
        # lock in UserViewSet.me). For everyone else, drop teacher_id so the
        # existing owner is preserved. Spec: docs.md RBAC rows 14-15.
        if user.role != UserRole.ADMIN:
            ser.validated_data.pop("teacher", None)
        ser.save()
        return Response(ser.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def _require_class_owner(self, request, cls):
        user = request.user
        if user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        if user.role == UserRole.TEACHER and cls.teacher_id != user.id:
            raise PermissionDenied("Not your class")

    @extend_schema(
        summary="List students in a class, or enroll one",
        request=s.EnrollSerializer,
        responses={
            200: s.UserSerializer(many=True),
            201: s.ClassStudentSerializer,
        },
    )
    @action(detail=True, methods=["get", "post"], url_path="students")
    def students(self, request, pk=None):
        cls = self.get_object()
        if request.method == "GET":
            qs = User.objects.filter(class_memberships__klass=cls).order_by("id")
            return Response(s.UserSerializer(qs, many=True).data)
        self._require_class_owner(request, cls)
        ser = s.EnrollSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        student = User.objects.filter(pk=ser.validated_data["student_id"]).first()
        if not student or student.role != UserRole.STUDENT:
            raise ValidationError("Target user is not a student")
        if ClassStudent.objects.filter(klass=cls, student=student).exists():
            raise ValidationError("Already enrolled")
        link = ClassStudent.objects.create(klass=cls, student=student)
        return Response(
            s.ClassStudentSerializer(link).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Remove a student from a class",
        parameters=[
            OpenApiParameter(
                "student_id", OpenApiTypes.INT, OpenApiParameter.PATH,
                description="ID of the student to unenroll",
            ),
        ],
        responses={204: OpenApiResponse(description="Enrollment removed")},
    )
    @action(
        detail=True, methods=["delete"],
        url_path=r"students/(?P<student_id>[^/.]+)",
    )
    def remove_student(self, request, pk=None, student_id=None):
        cls = self.get_object()
        self._require_class_owner(request, cls)
        link = ClassStudent.objects.filter(klass=cls, student_id=student_id).first()
        if not link:
            raise NotFound("Enrollment not found")
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="List or create lesson plans for a class",
        request=s.LessonPlanSerializer,
        responses={200: s.LessonPlanSerializer(many=True), 201: s.LessonPlanSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path="lesson-plans")
    def lesson_plans(self, request, pk=None):
        cls = self.get_object()
        if request.method == "GET":
            qs = LessonPlan.objects.filter(klass=cls).order_by("id")
            return Response(s.LessonPlanSerializer(qs, many=True).data)
        self._require_class_owner(request, cls)
        ser = s.LessonPlanSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        lp = ser.save(klass=cls)
        return Response(
            s.LessonPlanSerializer(lp).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="List or create assignments for a class",
        request=s.AssignmentSerializer,
        responses={200: s.AssignmentSerializer(many=True), 201: s.AssignmentSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path="assignments")
    def assignments(self, request, pk=None):
        cls = self.get_object()
        if request.method == "GET":
            qs = Assignment.objects.filter(klass=cls).order_by("id")
            return Response(s.AssignmentSerializer(qs, many=True).data)
        self._require_class_owner(request, cls)
        ser = s.AssignmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = ser.save(klass=cls, assigned_by=request.user)
        _broadcast_class(
            obj.klass_id,
            {"event": "assignment_created", "assignment_id": obj.id,
             "exercise_id": obj.exercise_id, "due_date": str(obj.due_date)},
        )
        return Response(
            s.AssignmentSerializer(obj).data, status=status.HTTP_201_CREATED
        )


# ============================================================ Modules
@extend_schema(tags=["modules"])
class ModuleViewSet(viewsets.ModelViewSet):
    queryset = LearningModule.objects.all().order_by("id")
    serializer_class = s.LearningModuleSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update"):
            return _perms(IsTeacherOrAdmin)
        if self.action == "destroy":
            return _perms(IsAdmin)
        return _perms()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        user = request.user
        if user.role == UserRole.TEACHER and obj.created_by_id != user.id:
            raise PermissionDenied("Not your module")
        ser = self.get_serializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @extend_schema(
        summary="List or create exercises within a module",
        request=s.ExerciseSerializer,
        responses={200: s.ExerciseSerializer(many=True), 201: s.ExerciseSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path="exercises")
    def exercises(self, request, pk=None):
        module = self.get_object()
        if request.method == "GET":
            qs = Exercise.objects.filter(module=module).order_by("id")
            return Response(s.ExerciseSerializer(qs, many=True).data)
        user = request.user
        if user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        if user.role == UserRole.TEACHER and module.created_by_id != user.id:
            raise PermissionDenied("Not your module")
        ser = s.ExerciseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = ser.save(module=module, created_by=user)
        return Response(
            s.ExerciseSerializer(obj).data, status=status.HTTP_201_CREATED
        )


# ============================================================ Exercises
@extend_schema(tags=["exercises"])
class ExerciseViewSet(
    mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    queryset = Exercise.objects.all().order_by("id")
    serializer_class = s.ExerciseSerializer

    def get_permissions(self):
        if self.action == "destroy":
            return _perms(IsTeacherOrAdmin)
        return _perms()

    @extend_schema(
        summary="List submissions for an exercise (teacher/admin)",
        responses={200: s.SubmissionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="submissions")
    def submissions(self, request, pk=None):
        if request.user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        ex = self.get_object()
        qs = Submission.objects.filter(exercise=ex).order_by("id")
        return Response(s.SubmissionSerializer(qs, many=True).data)


# ============================================================ Submissions
@extend_schema(tags=["submissions"])
class SubmissionViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Submission.objects.all().order_by("id")
    serializer_class = s.SubmissionSerializer

    def get_permissions(self):
        if self.action == "create":
            return _perms(IsStudent)
        return _perms()

    # Receptive skills are auto-graded from `answers`; productive skills
    # (writing/speaking) are graded by a human/AI later via Feedback.
    _RECEPTIVE = {
        SubmissionType.READING, SubmissionType.LISTENING, SubmissionType.QUIZ,
    }

    def perform_create(self, serializer):
        submission = serializer.save(student=self.request.user)
        if submission.submission_type in self._RECEPTIVE:
            submission.grade()
            submission.save(update_fields=["auto_score"])

    @extend_schema(
        summary="List the current student's own submissions",
        responses={200: s.SubmissionSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = Submission.objects.filter(student=request.user).order_by("id")
        return Response(s.SubmissionSerializer(qs, many=True).data)

    @extend_schema(
        summary="List feedback on a submission (owner / reviewer / class teacher / admin)",
        responses={
            200: s.FeedbackSerializer(many=True),
            403: OpenApiResponse(description="Not permitted to view this feedback"),
        },
    )
    @action(detail=True, methods=["get"], url_path="feedback")
    def feedback(self, request, pk=None):
        sub = self.get_object()
        if not can_view_submission_feedback(request.user, sub):
             raise PermissionDenied("Not permitted to view this feedback")
        qs = Feedback.objects.filter(submission=sub).order_by("id")
        return Response(s.FeedbackSerializer(qs, many=True).data)

    @extend_schema(
        summary="Teacher inbox: submissions across the teacher's own exercises",
        description=(
            "Aggregates submissions for every exercise the requesting teacher "
            "owns (Exercise.created_by, with module.created_by fallback), "
            "including self-practice submissions where assignment is null. "
            "Admins see all submissions. Optional filters: status, class_id, "
            "exercise_id."
        ),
        parameters=[
            OpenApiParameter(
                "status", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by submission status (stored values): pending | graded | ai_graded",
                enum=list(SubmissionStatus.values),
            ),
            OpenApiParameter(
                "class_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Filter to submissions whose assignment targets this class",
            ),
            OpenApiParameter(
                "exercise_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Filter to a single exercise",
            ),
        ],
        responses={200: s.SubmissionInboxSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="inbox")
    def inbox(self, request):
        user = request.user
        if user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")

        qs = (
            Submission.objects.all()
            .select_related("exercise", "student", "assignment")
            .prefetch_related("feedback")
            .order_by("-submitted_at", "-id")
        )

        # Ownership scope — teachers see only their own exercises' submissions.
        # Admins see everything, so skip the ownership filter for them.
        if user.role == UserRole.TEACHER:
            qs = qs.filter(
                Q(exercise__created_by=user)
                | Q(exercise__module__created_by=user)
            )

        # --- Optional filters ---
        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in SubmissionStatus.values:
                raise ValidationError({"status": "Invalid submission status"})
            qs = qs.filter(status=status_param)

        exercise_id = request.query_params.get("exercise_id")
        if exercise_id:
            qs = qs.filter(exercise_id=exercise_id)

        class_id = request.query_params.get("class_id")
        if class_id:
            qs = qs.filter(assignment__klass_id=class_id)

        qs = qs.distinct()
        return Response(s.SubmissionInboxSerializer(qs, many=True).data)

    @extend_schema(
        tags=["ai"],
        summary="Request AI feedback for a submission (UC-17/18)",
        request=None,
        responses={201: s.FeedbackSerializer},
    )
    @action(detail=True, methods=["post"], url_path="ai-evaluate")
    def ai_evaluate(self, request, pk=None):
        """Teacher/Admin triggers AI grading on an existing submission. Runs the
        active backend SYNCHRONOUSLY (MVP) and writes an is_ai_generated Feedback
        row (reviewer=None). Structured to move to an async worker later."""
        if request.user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        submission = self.get_object()
        try:
            fb = evaluate_submission(submission)
        except (ValueError, NotImplementedError) as exc:
            # ValueError -> missing media; NotImplementedError -> RealBackend not wired.
            raise ValidationError(str(exc))
        return Response(
            s.FeedbackSerializer(fb).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["ai"],
        summary="Autonomous AI skill practice (RBAC row 12, UC-09)",
        request=s.AiPracticeSerializer,
        responses={201: inline_serializer(
            name="AiPracticeResult",
            fields={
                "submission": s.SubmissionSerializer(),
                "feedback": s.FeedbackSerializer(),
            },
        )},
    )
    @action(
        detail=False, methods=["post"], url_path="ai-practice",
        permission_classes=_BASE + [IsStudent],
    )
    def ai_practice(self, request):
        """Student practices an exercise off-assignment and gets immediate AI
        feedback via the mock backend. Creates a Submission (assignment=None),
        derives submission_type from the exercise, then evaluates it."""
        ser = s.AiPracticeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        exercise = ser.validated_data["exercise"]
        submission = Submission.objects.create(
            exercise=exercise,
            student=request.user,
            assignment=None,
            submission_type=exercise.exercise_type,
            writing_text=ser.validated_data.get("writing_text"),
            audio_recording_url=ser.validated_data.get("audio_recording_url"),
        )
        try:
            fb = evaluate_submission(submission)
        except (ValueError, NotImplementedError) as exc:
            raise ValidationError(str(exc))
        return Response(
            {
                "submission": s.SubmissionSerializer(submission).data,
                "feedback": s.FeedbackSerializer(fb).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ============================================================ Feedback
@extend_schema(tags=["feedback"])
class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = s.FeedbackSerializer

    def get_queryset(self):
        # Base queryset, scoped so any future list/retrieve action cannot leak
        # other users' feedback. Create (the only current action) ignores this.
        qs = Feedback.objects.all().order_by("id")
        user = self.request.user
        if user.role == UserRole.ADMIN:
            return qs
        if user.role == UserRole.TEACHER:
            return qs.filter(
                Q(reviewer=user) | Q(submission__assignment__klass__teacher=user)
            ).distinct()
        # Student: only feedback on their own submissions.
        return qs.filter(submission__student=user)

    def get_permissions(self):
        return _perms(IsTeacherOrAdmin)

    def perform_create(self, serializer):
        is_ai = serializer.validated_data.get("is_ai_generated", False)
        if is_ai:
            feedback = serializer.save(reviewer=None)
            new_status = SubmissionStatus.AI_GRADED
        else:
            feedback = serializer.save(reviewer=self.request.user)
            new_status = SubmissionStatus.GRADED

        sub = feedback.submission
        sub.status = new_status
        sub.save(update_fields=["status"])

        klass_id = getattr(sub.assignment, "klass_id", None)
        _broadcast_class(
            klass_id,
            {"event": "feedback_posted", "submission_id": feedback.submission_id,
             "score": str(feedback.score) if feedback.score is not None else None},
        )


# ============================================================ Progress
@extend_schema(tags=["progress"])
class ProgressViewSet(viewsets.GenericViewSet):
    queryset = StudentModuleProgress.objects.all().order_by("id")
    serializer_class = s.ProgressSerializer

    def get_permissions(self):
        if self.action == "create":
            return _perms(IsStudent)
        return _perms()

    @extend_schema(
        summary="Upsert the current student's progress for a module",
        request=s.ProgressUpsertSerializer,
        responses={200: s.ProgressSerializer},
    )
    def create(self, request, *args, **kwargs):
        """Upsert progress for (student, module). POST /api/progress/."""
        ser = s.ProgressUpsertSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not LearningModule.objects.filter(
            pk=ser.validated_data["module_id"]
        ).exists():
            raise ValidationError({"module_id": "Module not found"})
        obj, _ = StudentModuleProgress.objects.update_or_create(
            student=request.user,
            module_id=ser.validated_data["module_id"],
            defaults={
                "completion_percentage": ser.validated_data["completion_percentage"],
                "last_accessed_at": timezone.now(),
            },
        )
        return Response(s.ProgressSerializer(obj).data)

    @extend_schema(
        summary="List the current student's module progress",
        responses={200: s.ProgressSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = StudentModuleProgress.objects.filter(student=request.user).order_by("id")
        return Response(s.ProgressSerializer(qs, many=True).data)


# ============================================================ Study Materials
@extend_schema(tags=["study-materials"])
class StudyMaterialViewSet(viewsets.ModelViewSet):
    """Official study-documents library.

    Read (list/retrieve) is open to every authenticated, active user (RBAC
    row 7 — "Read & Save Study Documents"). Write (create/update/destroy) is
    Admin-only (RBAC row 6 — "Manage Official Study Materials Library")."""

    queryset = StudyMaterial.objects.all().order_by("-created_at", "id")
    serializer_class = s.StudyMaterialSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return _perms(IsAdmin)
        return _perms()

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)


# ============================================================ AI models
@extend_schema(tags=["ai"])
class AiModelViewSet(viewsets.ModelViewSet):
    """Admin-managed multimodal-evaluation engine registry (docs.md UC-20/21,
    RBAC row 13). Toggling is_active/strictness reconfigures the live AI grader."""

    queryset = AiModel.objects.all().order_by("id")
    serializer_class = s.AiModelSerializer

    def get_permissions(self):
        return _perms(IsAdmin)   # all actions admin-only

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


# ============================================================ Dashboard helpers
def _ungraded_for_teacher(teacher):
    """Submissions on the teacher's OWN exercises that have NO Feedback yet.

    Ownership scope is identical to SubmissionViewSet.inbox:
    primary = Exercise.created_by; legacy fallback = module.created_by.
    Self-practice (assignment IS NULL) is included.
    """
    return (
        Submission.objects
        .filter(
            Q(exercise__created_by=teacher)
            | Q(exercise__module__created_by=teacher)
        )
        .filter(feedback__isnull=True)
        .distinct()
    )


# ============================================================ Dashboard
@extend_schema(
    tags=["dashboard"],
    summary="Role-aware dashboard aggregate (student / teacher / admin)",
    description=(
        "Returns a different payload shape per role. Student: due/upcoming "
        "assignments, in-progress modules, recent feedback. Teacher: class & "
        "student counts, ungraded-submission count, recent activity. Admin: "
        "user counts by role and platform totals + recent rows."
    ),
    responses={200: inline_serializer(
        name="Dashboard",
        fields={
            "role": drf_serializers.CharField(),
            "generated_at": drf_serializers.DateTimeField(),
            "data": drf_serializers.DictField(),
        },
    )},
)
class DashboardView(APIView):
    permission_classes = _BASE

    def get(self, request):
        user = request.user
        if user.role == UserRole.STUDENT:
            data = self._student(user)
        elif user.role == UserRole.TEACHER:
            data = self._teacher(user)
        else:  # admin
            data = self._admin()
        return Response({
            "role": user.role,
            "generated_at": timezone.now(),
            "data": data,
        })

    # ---- Student (UC-05, RBAC row 4) ----
    def _student(self, user):
        now = timezone.now()
        due = (
            Assignment.objects
            .filter(klass__students__student=user, due_date__gte=now)
            .order_by("due_date")[:10]
        )
        in_progress = (
            StudentModuleProgress.objects
            .filter(student=user, completion_percentage__lt=100)
            .order_by("-last_accessed_at")[:10]
        )
        recent_feedback = (
            Feedback.objects
            .filter(submission__student=user)
            .order_by("-created_at")[:5]
        )
        return {
            "due_assignments": s.AssignmentSerializer(due, many=True).data,
            "in_progress_modules": s.ProgressSerializer(in_progress, many=True).data,
            "recent_feedback": s.FeedbackSerializer(recent_feedback, many=True).data,
        }

    # ---- Teacher (UC-11, RBAC row 5) ----
    def _teacher(self, user):
        own_classes = Class.objects.filter(teacher=user)
        enrolled = ClassStudent.objects.filter(klass__teacher=user)
        ungraded_qs = _ungraded_for_teacher(user)
        recent = ungraded_qs.order_by("-submitted_at")[:10]
        return {
            "class_count": own_classes.count(),
            "enrolled_student_count": enrolled.values("student").distinct().count(),
            "ungraded_submission_count": ungraded_qs.count(),
            "classes": s.ClassSerializer(own_classes.order_by("id"), many=True).data,
            "recent_ungraded": s.SubmissionSerializer(recent, many=True).data,
        }

    # ---- Admin (§3.4) ----
    def _admin(self):
        by_role = {
            row["role"]: row["n"]
            for row in User.objects.values("role").annotate(n=Count("id"))
        }
        return {
            "user_counts_by_role": {
                UserRole.ADMIN: by_role.get(UserRole.ADMIN, 0),
                UserRole.TEACHER: by_role.get(UserRole.TEACHER, 0),
                UserRole.STUDENT: by_role.get(UserRole.STUDENT, 0),
            },
            "totals": {
                "classes": Class.objects.count(),
                "modules": LearningModule.objects.count(),
                "exercises": Exercise.objects.count(),
                "submissions": Submission.objects.count(),
                "feedback": Feedback.objects.count(),
            },
            "recent_users": s.UserSerializer(
                User.objects.order_by("-created_at")[:5], many=True
            ).data,
            "recent_submissions": s.SubmissionSerializer(
                Submission.objects.order_by("-submitted_at")[:5], many=True
            ).data,
            "recent_feedback": s.FeedbackSerializer(
                Feedback.objects.order_by("-created_at")[:5], many=True
            ).data,
        }


# ============================================================ Reports
@extend_schema(
    tags=["reports"],
    summary="Export a class grade report (CSV; PDF deferred)",
    parameters=[
        OpenApiParameter(
            "class_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True,
            description="Class to report on. Teachers may only export their own.",
        ),
        OpenApiParameter(
            "format", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            enum=["csv", "pdf"], default="csv",
            description="csv (default, implemented) or pdf (deferred — 501 unless reportlab hook enabled).",
        ),
    ],
    responses={
        (200, "text/csv"): OpenApiTypes.BINARY,
        403: OpenApiResponse(description="Not your class"),
        404: OpenApiResponse(description="Class not found"),
        501: OpenApiResponse(description="PDF export not enabled"),
    },
)
class GradeReportView(APIView):
    permission_classes = [*_BASE, IsTeacherOrAdmin]

    def get(self, request):
        class_id = request.query_params.get("class_id")
        if not class_id:
            raise ValidationError({"class_id": "Required"})
        cls = Class.objects.filter(pk=class_id).first()
        if not cls:
            raise NotFound("Class not found")
        # Teacher scoping: own classes only; admin: any.
        if request.user.role == UserRole.TEACHER and cls.teacher_id != request.user.id:
            raise PermissionDenied("Not your class")

        rows = self._rows(cls)
        fmt = request.query_params.get("format", "csv").lower()
        if fmt == "pdf":
            return self._pdf(cls, rows)
        return self._csv(cls, rows)

    def _rows(self, cls):
        subs = (
            Submission.objects
            .filter(assignment__klass=cls)
            .select_related("student", "exercise")
            .prefetch_related("feedback")
            .order_by("student__full_name", "submitted_at")
        )
        out = []
        for sub in subs:
            fb = sub.feedback.order_by("-created_at").first()
            out.append({
                "student_id": sub.student_id,
                "student_name": sub.student.full_name,
                "student_email": sub.student.email,
                "exercise": sub.exercise.title,
                "submission_type": sub.submission_type,
                "auto_score": sub.auto_score if sub.auto_score is not None else "",
                "feedback_score": fb.score if fb and fb.score is not None else "",
                "graded": "yes" if fb else "no",
                "is_ai_generated": (
                    getattr(fb, "is_ai_generated", False) if fb else ""
                ),
                "submitted_at": sub.submitted_at.isoformat(),
            })
        return out

    _COLUMNS = [
        "student_id", "student_name", "student_email", "exercise",
        "submission_type", "auto_score", "feedback_score", "graded",
        "is_ai_generated", "submitted_at",
    ]

    def _csv(self, cls, rows):
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = (
            f'attachment; filename="grades_class_{cls.pk}.csv"'
        )
        writer = csv.DictWriter(resp, fieldnames=self._COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return resp

    def _pdf(self, cls, rows):
        from django.conf import settings as dj_settings
        if not getattr(dj_settings, "REPORTS_PDF_ENABLED", False):
            return Response(
                {"detail": "PDF export not enabled; use format=csv."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        raise NotImplementedError


# ============================================================ Admin ops & audit
@extend_schema(tags=["admin"])
class AdminActivityView(APIView):
    """Recent admin activity feed (read-only, Admin only)."""

    permission_classes = _BASE + [IsAdmin]

    @extend_schema(
        summary="Recent admin activity (audit logs + new users/submissions/feedback)",
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY,
                             description="Max rows to return (default 50, max 200)"),
        ],
        responses={200: s.ActivityItemSerializer(many=True)},
    )
    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50

        items = []
        for log in SystemLog.objects.all().order_by("-timestamp")[:limit]:
            items.append({
                "type": "system_log", "id": log.id,
                "summary": log.action_description, "timestamp": log.timestamp,
            })
        for u in User.objects.all().order_by("-created_at")[:limit]:
            items.append({
                "type": "user", "id": u.id,
                "summary": f"User registered: {u.full_name} ({u.role})",
                "timestamp": u.created_at,
            })
        for sub in (
            Submission.objects.select_related("student").order_by("-submitted_at")[:limit]
        ):
            items.append({
                "type": "submission", "id": sub.id,
                "summary": (
                    f"Submission #{sub.id} by user {sub.student_id} "
                    f"({sub.submission_type})"
                ),
                "timestamp": sub.submitted_at,
            })
        for fb in (
            Feedback.objects.select_related("submission").order_by("-created_at")[:limit]
        ):
            by = "AI" if fb.is_ai_generated else f"user {fb.reviewer_id}"
            items.append({
                "type": "feedback", "id": fb.id,
                "summary": f"Feedback #{fb.id} left on submission {fb.submission_id} by {by}",
                "timestamp": fb.created_at,
            })

        items.sort(key=lambda x: x["timestamp"], reverse=True)
        return Response(s.ActivityItemSerializer(items[:limit], many=True).data)


@extend_schema(tags=["admin"])
class AdminHealthView(APIView):
    """System health snapshot (read-only, Admin only)."""

    permission_classes = _BASE + [IsAdmin]

    @extend_schema(
        summary="System health probe (database check + entity counts)",
        responses={200: s.HealthSerializer},
    )
    def get(self, request):
        import time
        from django.db import connection

        db_status = "ok"
        latency = 0.0
        start = time.perf_counter()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            latency = (time.perf_counter() - start) * 1000.0
        except Exception:
            db_status = "error"

        counts = {
            "users": User.objects.count(),
            "classes": Class.objects.count(),
            "modules": LearningModule.objects.count(),
            "exercises": Exercise.objects.count(),
            "submissions": Submission.objects.count(),
            "feedback": Feedback.objects.count(),
            "study_materials": StudyMaterial.objects.count(),
            "ai_models": AiModel.objects.count(),
            "system_logs": SystemLog.objects.count(),
        }

        status_val = "ok"
        if db_status == "error" or latency > 500.0:
            status_val = "degraded"

        payload = {
            "status": status_val,
            "database": db_status,
            "db_latency_ms": round(latency, 2),
            "counts": counts,
            "server_time": timezone.now(),
        }
        return Response(s.HealthSerializer(payload).data)

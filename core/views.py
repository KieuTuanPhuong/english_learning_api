"""DRF views — port of the FastAPI routers.

URL scheme adopts DRF conventions under /api/ (see config/urls.py). Role and
ownership rules mirror the original `require_roles` dependencies.
"""

import csv
import logging

from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage
from django.db.models import Count, ProtectedError, Q
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
    APIException,
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
# pyrefly: ignore [missing-import]
from rest_framework_simplejwt.tokens import RefreshToken

from . import serializers as s
from .models import (
    AiInsight,
    AiInsightKind,
    Assignment,
    BandLevel,
    Class,
    ClassStudent,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    LessonPlan,
    Meeting,
    MeetingStatus,
    MockTestTemplate,
    PronunciationAttempt,
    PronunciationDrill,
    RubricTemplate,
    ScoreConversionTable,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    SubmissionStatus,
    TestAttempt,
    TestFormat,
    TestSectionExercise,
    Topic,
    User,
    UserRole,
    WritingAnnotation,
    StudyMaterial,
    AiModel,
    SystemLog,
)
from .permissions import (
    IsActiveUser, IsAdmin, IsStudent, IsTeacherOrAdmin,
    can_view_attempt, can_view_submission_feedback, _teacher_may_annotate,
)
from . import media, mock_tests
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .ai import evaluate_submission
from .ai import assist
from .ai.gemini import GeminiError
from .ai.pronunciation import assess_attempt
from .audio import (
    MOCK_TEST_MAX_DURATION_SECONDS,
    MOCK_TEST_MAX_UPLOAD_BYTES,
    validate_upload,
)

logger = logging.getLogger(__name__)


class AiUnavailable(APIException):
    """Gemini transport/quota failure -> 502 so the client can retry."""
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "ai_unavailable"


def _ai_call(fn, *args, **kwargs):
    """Run an AI-layer call and map failures to HTTP: bad input / unwired
    backend / missing key -> 400, Gemini unavailable -> 502."""
    try:
        return fn(*args, **kwargs)
    except (ValueError, NotImplementedError, ImproperlyConfigured) as exc:
        raise ValidationError(str(exc))
    except GeminiError as exc:
        raise AiUnavailable(f"AI service unavailable: {exc}")


class MediaUploadThrottle(UserRateThrottle):
    """Upload cap for the shared audio endpoint. Generous enough for a Speaking
    section (three parts, plus re-records) but not for a scripted flood."""

    scope = "media_upload"

    def get_rate(self):
        return "60/hour"


class PronunciationAttemptThrottle(UserRateThrottle):
    """Attempt-create rate cap (research doc 04 N3 / risk 'attempt spam'):
    30 recordings per hour per student. Self-contained rate so no global
    DEFAULT_THROTTLE_RATES entry is needed."""

    scope = "pronunciation_attempt"

    def get_rate(self):
        return "30/hour"

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

    def get_queryset(self):
        qs = LearningModule.objects.all().order_by("id")
        if self.action == "list":
            band = self.request.query_params.get("band")
            topic = self.request.query_params.get("topic")
            if band:
                qs = qs.filter(band=band)
            if topic:
                qs = qs.filter(topic=topic)
        return qs

    @extend_schema(
        summary="List modules, filterable by band and topic",
        parameters=[
            OpenApiParameter(
                "band", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by target band range (e.g. band_5_6)",
                enum=[c[0] for c in BandLevel.choices],
            ),
            OpenApiParameter(
                "topic", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by topic (e.g. life, sports)",
                enum=[c[0] for c in Topic.choices],
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

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
        parameters=[
            OpenApiParameter(
                "band", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by target band range (GET only)",
                enum=[c[0] for c in BandLevel.choices],
            ),
            OpenApiParameter(
                "topic", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Filter by topic (GET only)",
                enum=[c[0] for c in Topic.choices],
            ),
        ],
        responses={200: s.ExerciseSerializer(many=True), 201: s.ExerciseSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path="exercises")
    def exercises(self, request, pk=None):
        module = self.get_object()
        if request.method == "GET":
            qs = Exercise.objects.filter(module=module).order_by("id")
            band = request.query_params.get("band")
            topic = request.query_params.get("topic")
            if band:
                qs = qs.filter(band=band)
            if topic:
                qs = qs.filter(topic=topic)
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
def _filter_exercise_catalog(qs, params, drop=None):
    """Apply the catalog's browse filters. `drop` skips one dimension so the
    facet counts can answer "what would I get if I switched band/topic?"."""
    band, topic = params.get("band"), params.get("topic")
    ex_type, module_id, search = params.get("type"), params.get("module_id"), params.get("q")
    if band and drop != "band":
        qs = qs.filter(band=band)
    if topic and drop != "topic":
        qs = qs.filter(topic=topic)
    if ex_type and drop != "type":
        qs = qs.filter(exercise_type=ex_type)
    if module_id:
        qs = qs.filter(module_id=module_id)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(prompt_text__icontains=search))
    return qs


class ExerciseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Exercise.objects.all().order_by("id")
    serializer_class = s.ExerciseSerializer

    def get_serializer_class(self):
        # The catalog list uses the light, answer-free row serializer.
        if self.action == "list":
            return s.ExerciseListSerializer
        return s.ExerciseSerializer

    def get_queryset(self):
        qs = Exercise.objects.all().order_by("id")
        if self.action != "list":
            return qs
        # Group the catalog by band then topic. Postgres sorts NULLs last on
        # ASC, so exercises with no band/topic yet fall to the end rather than
        # heading a list whose whole point is choosing by band or topic.
        qs = (
            qs.select_related("module")
            .annotate(question_count=Count("questions"))
            .order_by("band", "topic", "id")
        )
        return _filter_exercise_catalog(qs, self.request.query_params)

    def get_permissions(self):
        if self.action == "destroy":
            return _perms(IsTeacherOrAdmin)
        return _perms()

    @extend_schema(
        summary="Browse the exercise catalog by topic, band, type or keyword",
        description=(
            "Open to every authenticated user — this is how a student picks "
            "practice by topic or band. Rows omit `questions` (and therefore "
            "answer keys); fetch an exercise's detail to work on it."
        ),
        parameters=[
            OpenApiParameter(
                "band", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Target band range, e.g. band_5_6",
                enum=[c[0] for c in BandLevel.choices],
            ),
            OpenApiParameter(
                "topic", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Topic, e.g. life or sports",
                enum=[c[0] for c in Topic.choices],
            ),
            OpenApiParameter(
                "type", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Exercise type",
                enum=[c[0] for c in ExerciseType.choices],
            ),
            OpenApiParameter(
                "module_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Restrict to one learning module",
            ),
            OpenApiParameter(
                "q", OpenApiTypes.STR, OpenApiParameter.QUERY,
                description="Case-insensitive search over title and prompt",
            ),
        ],
        responses={200: s.ExerciseListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Counts per topic, band and type for the catalog filters",
        description=(
            "Lets the browse UI label each filter chip with how many exercises "
            "it holds and hide empty ones. Each dimension's counts ignore its "
            "own filter, so they show what switching to another band or topic "
            "would give."
        ),
        parameters=[
            OpenApiParameter("band", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("topic", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("type", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("module_id", OpenApiTypes.INT, OpenApiParameter.QUERY),
            OpenApiParameter("q", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses={
            200: inline_serializer(
                name="ExerciseFacets",
                fields={
                    "total": drf_serializers.IntegerField(),
                    "bands": drf_serializers.DictField(child=drf_serializers.IntegerField()),
                    "topics": drf_serializers.DictField(child=drf_serializers.IntegerField()),
                    "types": drf_serializers.DictField(child=drf_serializers.IntegerField()),
                },
            )
        },
    )
    @action(detail=False, methods=["get"], url_path="facets")
    def facets(self, request):
        params = request.query_params

        def counts(field, drop):
            rows = (
                _filter_exercise_catalog(Exercise.objects.all(), params, drop=drop)
                .values(field)
                .annotate(n=Count("id"))
            )
            # Untagged rows (null band/topic) aren't a choosable filter.
            return {r[field]: r["n"] for r in rows if r[field]}

        return Response({
            "total": _filter_exercise_catalog(Exercise.objects.all(), params).count(),
            "bands": counts("band", drop="band"),
            "topics": counts("topic", drop="topic"),
            "types": counts("exercise_type", drop="type"),
        })

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

    @extend_schema(
        summary="Resolved rubric template for this exercise (pin → type default → null)",
        description=(
            "Returns the RubricTemplate that grading this exercise's productive "
            "submissions should use: the exercise's pinned template, else the "
            "active default for its exercise_type, else 200 with a null body "
            "(not 404) so the client falls back to holistic grading."
        ),
        responses={200: s.RubricTemplateSerializer},
    )
    @action(detail=True, methods=["get"], url_path="rubric")
    def rubric(self, request, pk=None):
        exercise = self.get_object()
        template = RubricTemplate.resolve_for(exercise)
        if template is None:
            return Response(None)
        return Response(s.RubricTemplateSerializer(template).data)


# ============================================================ Submissions
@extend_schema(tags=["submissions"])
class SubmissionViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Submission.objects.all().order_by("id")
    serializer_class = s.SubmissionSerializer

    def get_permissions(self):
        if self.action == "create":
            return _perms(IsStudent)
        if self.action == "ai_review_feedback":
            return _perms(IsTeacherOrAdmin)
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
        qs = (
            Feedback.objects.filter(submission=sub)
            .prefetch_related("criterion_scores__criterion__template")
            .order_by("id")
        )
        return Response(s.FeedbackSerializer(qs, many=True).data)

    @extend_schema(
        summary="List inline annotations on a writing submission",
        description=(
            "Same read audience as feedback (can_view_submission_feedback): "
            "owner student / class teacher / a teacher who reviewed it / admin. "
            "Ordered by start_offset."
        ),
        responses={
            200: s.WritingAnnotationSerializer(many=True),
            403: OpenApiResponse(description="Not permitted to view this submission"),
        },
    )
    @action(detail=True, methods=["get"], url_path="annotations")
    def annotations(self, request, pk=None):
        sub = self.get_object()
        if not can_view_submission_feedback(request.user, sub):
            raise PermissionDenied("Not permitted to view this submission")
        qs = sub.annotations.all().order_by("start_offset", "end_offset", "id")
        return Response(s.WritingAnnotationSerializer(qs, many=True).data)

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
        fb = _ai_call(evaluate_submission, submission)
        return Response(
            s.FeedbackSerializer(fb).data, status=status.HTTP_201_CREATED
        )

    # ---- AI coaching (core/ai/assist.py) ----
    _run_assist = staticmethod(_ai_call)

    @staticmethod
    def _can_coach(user, submission) -> bool:
        """Read audience for coaching: everyone who may read feedback, plus the
        teacher who owns the exercise (the inbox audience)."""
        if can_view_submission_feedback(user, submission):
            return True
        if user.role == UserRole.TEACHER:
            ex = submission.exercise
            if ex.created_by_id == user.id:
                return True
            if ex.module_id and ex.module.created_by_id == user.id:
                return True
        return False

    @extend_schema(
        tags=["ai"],
        summary="AI review of a teacher's (draft) feedback with recommendations",
        description=(
            "Teacher/Admin sends the feedback they are about to post (score, "
            "comments, optional rubric cells). The AI reviews the FEEDBACK — "
            "specificity, tone, actionability, accuracy, coverage, score "
            "alignment — and returns strengths, recommendations and a "
            "suggested rewrite. Nothing is persisted; the teacher decides."
        ),
        request=s.FeedbackReviewRequestSerializer,
        responses={
            200: s.FeedbackReviewSerializer,
            400: OpenApiResponse(description="Empty draft, or AI backend not configured"),
            502: OpenApiResponse(description="AI service unavailable"),
        },
    )
    @action(detail=True, methods=["post"], url_path="ai-review-feedback")
    def ai_review_feedback(self, request, pk=None):
        submission = self.get_object()
        ser = s.FeedbackReviewRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        result = self._run_assist(
            assist.review_feedback,
            submission,
            score=data.get("score"),
            comments=data.get("comments") or "",
            criterion_scores=data.get("criterion_scores"),
        )
        return Response(s.FeedbackReviewSerializer(result).data)

    @extend_schema(
        tags=["ai"],
        summary="AI explanation of the mistakes in a submission",
        description=(
            "GET returns the newest stored explanation (404 if none yet). POST "
            "generates a fresh one with the AI backend and stores it. Audience: "
            "the student who owns the submission, the class teacher, a "
            "reviewing teacher, the exercise owner, or an admin. Writing and "
            "receptive (reading/listening/quiz) submissions only."
        ),
        responses={
            200: s.AiInsightSerializer,
            201: s.AiInsightSerializer,
            400: OpenApiResponse(description="Unsupported submission or AI backend not configured"),
            403: OpenApiResponse(description="Not permitted"),
            404: OpenApiResponse(description="No explanation generated yet (GET)"),
            502: OpenApiResponse(description="AI service unavailable"),
        },
        request=None,
    )
    @action(detail=True, methods=["get", "post"], url_path="ai-explain")
    def ai_explain(self, request, pk=None):
        submission = self.get_object()
        if not self._can_coach(request.user, submission):
            raise PermissionDenied("Not permitted to view this submission")
        if request.method == "GET":
            row = submission.ai_insights.filter(
                kind=AiInsightKind.MISTAKE_EXPLANATION
            ).first()
            if row is None:
                raise NotFound("No explanation generated yet")
            return Response(s.AiInsightSerializer(row).data)
        result = self._run_assist(assist.explain_mistakes, submission)
        row = AiInsight.objects.create(
            submission=submission,
            kind=AiInsightKind.MISTAKE_EXPLANATION,
            payload=result,
            engine=result.get("engine", ""),
            requested_by=request.user,
        )
        return Response(s.AiInsightSerializer(row).data, status=status.HTTP_201_CREATED)

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
        fb = _ai_call(evaluate_submission, submission)
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

        # If this submission belongs to a mock test's Writing/Speaking section,
        # the score just became convertible into a band (no-op otherwise).
        mock_tests.on_feedback_created(feedback)

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
@extend_schema(tags=["admin"])
class HealthCheckView(APIView):
    """Unauthenticated liveness/readiness probe for load balancers and uptime
    monitors.

    Distinct from :class:`AdminHealthView`, which is admin-only and reports
    entity counts. This one is anonymous, so it deliberately exposes nothing
    beyond whether the process can reach its database — and it *does* reach the
    database, because a probe that only proves the web worker is up will report
    green through an entire database outage.

    200 when healthy, 503 when not, so an orchestrator can act on the status
    code alone. `SECURE_REDIRECT_EXEMPT` keeps it reachable over plain HTTP on
    the private interface.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Liveness probe (anonymous): process up + database reachable",
        responses={
            200: s.HealthCheckSerializer,
            503: OpenApiResponse(description="Database unreachable"),
        },
    )
    def get(self, request):
        import time

        from django.db import connection

        started = time.perf_counter()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            database, healthy = "ok", True
        except Exception:
            # Never surface the driver message: it carries host, port and user.
            logger.exception("Health check failed to reach the database")
            database, healthy = "error", False

        return Response(
            {
                "status": "ok" if healthy else "degraded",
                "database": database,
                "db_latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            },
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


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


# ============================================================ Mock tests
@extend_schema(tags=["mock-tests"])
class TestFormatViewSet(viewsets.ModelViewSet):
    """Exam-format registry (IELTS Academic, TOEIC L&R, ...). Readable by any
    authenticated user so the catalog can badge templates; writable by admins
    only — adding a format is data entry, never a migration."""

    serializer_class = s.TestFormatSerializer

    def get_queryset(self):
        """Retired formats stay in the table — templates PROTECT them and old
        reports still convert through their tables — but they leave the
        catalogue. Admins keep seeing everything so a format can be brought
        back without a shell."""
        qs = TestFormat.objects.all().order_by("slug")
        user = self.request.user
        if user.is_authenticated and user.role == UserRole.ADMIN:
            return qs
        return qs.filter(is_active=True)

    def get_permissions(self):
        if self.action in ("list", "retrieve", "conversions"):
            return _perms()
        return _perms(IsAdmin)

    @extend_schema(
        summary="Read or replace a format's raw -> band/scaled conversion tables",
        description=(
            "GET returns every per-skill table for the format. PUT replaces one "
            "table: body is {skill, mapping, source_note}. Official IELTS/TOEIC "
            "tables are unpublished, so seeded mappings are approximations and "
            "reports label scores 'estimated'."
        ),
        request=s.ScoreConversionTableSerializer,
        responses={
            200: s.ScoreConversionTableSerializer(many=True),
            403: OpenApiResponse(description="Admin only"),
        },
    )
    @action(detail=True, methods=["get", "put"], url_path="conversions")
    def conversions(self, request, pk=None):
        fmt = self.get_object()
        if request.method == "GET":
            qs = ScoreConversionTable.objects.filter(format=fmt).order_by("skill")
            return Response(s.ScoreConversionTableSerializer(qs, many=True).data)

        if request.user.role != UserRole.ADMIN:
            raise PermissionDenied("Insufficient permissions")
        ser = s.ScoreConversionTableSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        table, _ = ScoreConversionTable.objects.update_or_create(
            format=fmt,
            skill=ser.validated_data["skill"],
            defaults={
                "mapping": ser.validated_data["mapping"],
                "source_note": ser.validated_data.get("source_note"),
            },
        )
        return Response(s.ScoreConversionTableSerializer(table).data)


@extend_schema(tags=["mock-tests"])
class MockTestTemplateViewSet(viewsets.ModelViewSet):
    """The platform's mock-test library: an ordered set of sections, each
    wrapping existing Exercises.

    Mock tests are app content, not classroom content — every authenticated
    user reads the whole catalogue and any student can sit any test, with
    admins curating the shelf. This mirrors StudyMaterialViewSet (RBAC rows
    6-7): no publish gate, no per-teacher ownership scope.
    """

    serializer_class = s.MockTestTemplateSerializer

    def get_queryset(self):
        """A template whose format was retired drops out of the catalogue with
        it — otherwise deactivating TOEIC would leave its papers on the shelf
        with no way to sit them coherently. Admins still see everything."""
        qs = (
            MockTestTemplate.objects
            .select_related("format")
            .prefetch_related("sections__items__exercise")
            .order_by("-created_at", "id")
        )
        user = self.request.user
        if user.is_authenticated and user.role == UserRole.ADMIN:
            return qs
        return qs.filter(format__is_active=True)

    def get_permissions(self):
        if self.action in (
            "create", "update", "partial_update", "destroy",
            "duplicate", "import_template",
        ):
            return _perms(IsAdmin)      # curating the library is an admin job
        if self.action == "attempts":
            return _perms(IsStudent)
        return _perms()                 # any active user browses and reads

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        # Editing replaces the section list wholesale, which PROTECT refuses
        # once anyone has sat the test — surface that as a 400, not a 500.
        try:
            return super().update(request, *args, **kwargs)
        except ProtectedError:
            raise ValidationError({
                "detail": (
                    "This mock test has already been attempted; its sections "
                    "can no longer be changed. Add a new test instead."
                ),
                "code": "template_in_use",
            })

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            raise ValidationError({
                "detail": (
                    "This mock test has attempts and cannot be deleted; "
                    "removing it would destroy those students' score reports."
                ),
                "code": "template_in_use",
            })

    @extend_schema(
        summary="Start (or resume) an attempt at this mock test",
        description=(
            "Creates a TestAttempt plus one not_started SectionAttempt per "
            "section. Retakes are unlimited, but an unfinished attempt is "
            "returned as-is instead of being duplicated — including its mode, "
            "which resuming never changes.\n\n"
            "`mode` is `exam` (sections in the template's order, the real "
            "sitting) or `practice` (start with whichever section you like). "
            "Defaults to `exam` when the body is omitted."
        ),
        request=s.TestAttemptCreateSerializer,
        responses={
            201: s.TestAttemptSerializer,
            400: OpenApiResponse(description="Template has no sections"),
        },
    )
    @action(detail=True, methods=["post"], url_path="attempts")
    def attempts(self, request, pk=None):
        template = self.get_object()
        ser = s.TestAttemptCreateSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        try:
            attempt = mock_tests.start_attempt(
                template, request.user, mode=ser.validated_data["mode"],
            )
        except mock_tests.MockTestError as exc:
            raise ValidationError({"detail": str(exc), "code": exc.code})
        return Response(
            s.TestAttemptSerializer(attempt).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Copy a template into a new, unattempted one",
        description=(
            "Sections and their items are copied; attempts are not. This is the "
            "supported way to revise a test that students have already sat — "
            "editing one in place is refused so their score reports keep "
            "meaning what they said."
        ),
        request=None,
        responses={201: s.MockTestTemplateSerializer},
    )
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        source = self.get_object()
        copy = mock_tests.duplicate_template(source, request.user)
        return Response(
            s.MockTestTemplateSerializer(copy).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Export a template as a portable JSON document",
        description=(
            "Exercises are inlined by content rather than by id, so the document "
            "can be imported into another environment where those ids mean "
            "nothing. Answer keys are included — this is an admin export, not "
            "anything a student may read."
        ),
        responses={200: s.MockTestDocumentSerializer},
    )
    @action(detail=True, methods=["get"], url_path="export")
    def export(self, request, pk=None):
        return Response(mock_tests.export_template(self.get_object()))

    @extend_schema(
        summary="Import one or more exported templates",
        description=(
            "Body is a single export document or a list of them — the bulk path. "
            "Each document creates its own exercises, so an import never "
            "re-points at content another test already owns."
        ),
        request=s.MockTestDocumentSerializer,
        responses={201: s.MockTestTemplateSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="import", url_name="import")
    def import_template(self, request):
        documents = request.data if isinstance(request.data, list) else [request.data]
        ser = s.MockTestDocumentSerializer(data=documents, many=True)
        ser.is_valid(raise_exception=True)
        try:
            created = mock_tests.import_templates(ser.validated_data, request.user)
        except mock_tests.MockTestError as exc:
            raise ValidationError({"detail": str(exc), "code": exc.code})
        return Response(
            s.MockTestTemplateSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["mock-tests"])
class TestAttemptViewSet(
    mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """A student's sitting of a mock test. All timing is server-authoritative
    (core/mock_tests.py): the client only reads `expires_at` and `server_time`."""

    serializer_class = s.TestAttemptSerializer

    def get_queryset(self):
        return (
            TestAttempt.objects
            .select_related("template__format", "student")
            .prefetch_related(
                "sections__section__items__exercise__questions__options"
            )
            .order_by("-started_at", "-id")
        )

    def get_permissions(self):
        if self.action in ("start", "answers", "submit", "advance"):
            return _perms(IsStudent)
        return _perms()

    def get_object(self):
        attempt = super().get_object()
        if not can_view_attempt(self.request.user, attempt):
            raise PermissionDenied("Not permitted to view this attempt")
        return attempt

    def _own_attempt(self):
        """Write actions are the sitting student's alone — a class teacher may
        read an attempt but never touch its clock or answers."""
        attempt = super().get_object()
        if attempt.student_id != self.request.user.id:
            raise PermissionDenied("Not your attempt")
        return attempt

    @staticmethod
    def _handle(exc: "mock_tests.MockTestError"):
        """Map a domain error onto HTTP. `section_expired` is a 409 because the
        client treats it as a state transition (lock the UI), not a bad request."""
        if isinstance(exc, mock_tests.SectionExpired):
            return Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_409_CONFLICT,
            )
        if exc.code == "not_found":
            raise NotFound(str(exc))
        raise ValidationError({"detail": str(exc), "code": exc.code})

    @extend_schema(
        summary="List the current student's own mock-test attempts",
        responses={200: s.TestAttemptListSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = (
            TestAttempt.objects
            .filter(student=request.user)
            .select_related("template__format")
            # Prefetched, not annotated: the serializer counts in Python from
            # one extra query rather than adding two aggregate subqueries.
            .prefetch_related("sections")
            .order_by("-started_at", "-id")
        )
        return Response(s.TestAttemptListSerializer(qs, many=True).data)

    @extend_schema(
        summary="Start a section's server clock",
        description=(
            "Sets started_at and expires_at (duration + 30s grace). Sections "
            "must be taken in order, one at a time. Idempotent while the "
            "section is already running."
        ),
        request=None,
        responses={200: s.SectionAttemptSerializer},
    )
    @action(
        detail=True, methods=["post"],
        url_path=r"sections/(?P<section_attempt_id>[0-9]+)/start",
    )
    def start(self, request, pk=None, section_attempt_id=None):
        attempt = self._own_attempt()
        try:
            section_attempt = mock_tests.start_section(attempt, section_attempt_id)
        except mock_tests.MockTestError as exc:
            return self._handle(exc)
        return Response(s.SectionAttemptSerializer(section_attempt).data)

    @extend_schema(
        summary="Autosave the section's draft answers",
        description=(
            "Replaces the whole draft envelope in one row UPDATE. Returns 409 "
            "with code `section_expired` once the clock has run out — the "
            "client then locks the UI and offers only Submit."
        ),
        request=s.SectionDraftSerializer,
        responses={
            200: s.SectionAttemptSerializer,
            409: OpenApiResponse(description="Section expired (code: section_expired)"),
        },
    )
    @action(
        detail=True, methods=["patch"],
        url_path=r"sections/(?P<section_attempt_id>[0-9]+)/answers",
    )
    def answers(self, request, pk=None, section_attempt_id=None):
        attempt = self._own_attempt()
        ser = s.SectionDraftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            section_attempt = mock_tests.autosave(
                attempt, section_attempt_id, ser.validated_data
            )
        except mock_tests.MockTestError as exc:
            return self._handle(exc)
        return Response(s.SectionAttemptSerializer(section_attempt).data)

    @extend_schema(
        summary="Close the current part of a sequential section",
        description=(
            "Listening recordings and Speaking interview parts run one at a "
            "time with no going back. This closes the part in hand and opens "
            "the next; it is idempotent, so a double tap cannot skip one. "
            "Sections whose parts are all open (Reading, Writing) reject it."
        ),
        request=s.SectionAdvanceSerializer,
        responses={200: s.SectionAttemptSerializer},
    )
    @action(
        detail=True, methods=["post"],
        url_path=r"sections/(?P<section_attempt_id>[0-9]+)/advance",
    )
    def advance(self, request, pk=None, section_attempt_id=None):
        attempt = self._own_attempt()
        ser = s.SectionAdvanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            section_attempt = mock_tests.advance_item(
                attempt, section_attempt_id, ser.validated_data["exercise_id"]
            )
        except mock_tests.MockTestError as exc:
            return self._handle(exc)
        return Response(s.SectionAttemptSerializer(section_attempt).data)

    @extend_schema(
        summary="Submit a section",
        description=(
            "Creates one ordinary Submission per exercise in the section, "
            "auto-grades receptive ones, and converts the raw count into a "
            "band/scaled score. Accepted after expiry too, but then it grades "
            "the last draft the server accepted rather than the request body."
        ),
        request=s.SectionSubmitSerializer,
        responses={200: s.SectionAttemptSerializer},
    )
    @action(
        detail=True, methods=["post"],
        url_path=r"sections/(?P<section_attempt_id>[0-9]+)/submit",
    )
    def submit(self, request, pk=None, section_attempt_id=None):
        attempt = self._own_attempt()
        ser = s.SectionSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            section_attempt = mock_tests.submit_section(
                attempt,
                section_attempt_id,
                draft=ser.validated_data.get("draft"),
                recordings=ser.validated_data.get("recordings"),
            )
        except mock_tests.MockTestError as exc:
            return self._handle(exc)
        return Response(s.SectionAttemptSerializer(section_attempt).data)

    @extend_schema(
        summary="Score report for an attempt (owner student / their teacher / admin)",
        description=(
            "`partial` stays true while any section lacks a converted score — "
            "Listening/Reading land immediately, Writing/Speaking after "
            "teacher or AI grading. Scores are estimates: see `estimated`."
        ),
        responses={200: s.TestAttemptReportSerializer},
    )
    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, pk=None):
        attempt = self.get_object()
        return Response(
            s.TestAttemptReportSerializer(mock_tests.build_report(attempt)).data
        )

    @extend_schema(
        summary="Run automatic AI marking for the attempt's completed sections",
        description=(
            "Claims every completed section whose AI marking is pending, "
            "failed or stuck and grades it in the background: Writing/Speaking "
            "tasks get an AI Feedback row (which sets the section band); "
            "Listening/Reading tasks get a per-question explanation of each "
            "wrong answer, folded into the report's `submissions[].questions`. "
            "Sections are claimed automatically on submit, so this is the "
            "retry / catch-up path. Returns the report: 202 when something was "
            "claimed, 200 when there was nothing to do. Poll the report while "
            "any section's `ai_status` is `running`."
        ),
        request=None,
        responses={
            200: s.TestAttemptReportSerializer,
            202: s.TestAttemptReportSerializer,
        },
    )
    @action(detail=True, methods=["post"], url_path="ai-grade")
    def ai_grade(self, request, pk=None):
        attempt = self.get_object()
        claimed = mock_tests.claim_ai_grading(attempt)
        mock_tests.schedule_ai_grading(claimed)
        attempt.refresh_from_db()  # inline grading may have set overall_score
        report = s.TestAttemptReportSerializer(mock_tests.build_report(attempt)).data
        return Response(
            report,
            status=status.HTTP_202_ACCEPTED if claimed else status.HTTP_200_OK,
        )


# ============================================================ Rubrics
@extend_schema(tags=["rubrics"])
class RubricTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only rubric registry (docs/research/02-scoring-rubrics.md §4.4).
    List shows active templates only; retrieve includes inactive ones so
    historic grades still render against the exact rubric used (NFR2). Readable
    by any authenticated active user — students need the wording for their own
    breakdowns. MVP content is managed via seed_rubrics + Django admin."""

    serializer_class = s.RubricTemplateSerializer

    def get_queryset(self):
        qs = (
            RubricTemplate.objects
            .prefetch_related("criteria__band_descriptors")
            .order_by("id")
        )
        if self.action == "list":
            return qs.filter(is_active=True)
        return qs

    def get_permissions(self):
        return _perms()


# ============================================================ Writing annotations
@extend_schema(tags=["annotations"])
class WritingAnnotationViewSet(
    mixins.CreateModelMixin, mixins.UpdateModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Inline teacher annotations on a writing submission
    (docs/research/03-writing-annotations.md §4.4). Nested read lives on
    SubmissionViewSet.annotations; this viewset owns the writes."""

    serializer_class = s.WritingAnnotationSerializer
    # Class attribute only so the router/OpenAPI generator can resolve the model
    # (get_queryset below needs request.user and is the real runtime source).
    queryset = WritingAnnotation.objects.all()

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

    def perform_update(self, serializer):
        obj = serializer.instance
        user = self.request.user
        if user.role != UserRole.ADMIN and obj.author_id != user.id:
            raise PermissionDenied("Not your annotation")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if user.role != UserRole.ADMIN and instance.author_id != user.id:
            raise PermissionDenied("Not your annotation")
        instance.delete()

    @extend_schema(
        summary="Acknowledge an annotation (submission's student only) — Phase 2",
        request=None,
        responses={200: s.WritingAnnotationSerializer},
    )
    @action(detail=True, methods=["post"], url_path="acknowledge")
    def acknowledge(self, request, pk=None):
        annotation = self.get_object()
        if annotation.submission.student_id != request.user.id:
            raise PermissionDenied("Only the submission's student may acknowledge.")
        annotation.is_acknowledged = True
        annotation.save(update_fields=["is_acknowledged"])
        return Response(s.WritingAnnotationSerializer(annotation).data)


# ============================================================ Media uploads
@extend_schema(tags=["media"])
class AudioUploadView(APIView):
    """Store one audio file and return the URL to reference it by.

    Two callers, one endpoint:

    * an admin attaching a recording to a listening ``Exercise``
      (``audio_prompt_url``), and
    * a student's Speaking answer during a mock test.

    The second is why this exists. Speaking answers used to travel as base64
    data URLs inside the section-submit body; an IELTS Speaking section is three
    parts of up to five minutes each, which is tens of megabytes of JSON in one
    request and the same again in the row. Uploading each clip on its own and
    submitting URLs keeps both bounded.

    Passing ``attempt_id`` + ``exercise_id`` applies that part's
    ``max_record_seconds`` on top of the global cap, so an IELTS Part 2 long
    turn is held to its real two minutes rather than the endpoint's six.
    """

    permission_classes = _BASE
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [MediaUploadThrottle]

    @extend_schema(
        summary="Upload an audio file and get back its URL",
        request={"multipart/form-data": {
            "type": "object",
            "properties": {
                "audio": {"type": "string", "format": "binary"},
                "attempt_id": {"type": "integer"},
                "exercise_id": {"type": "integer"},
            },
            "required": ["audio"],
        }},
        responses={
            201: inline_serializer(
                name="AudioUploadResponse",
                fields={
                    # Signed, expiring — play this one.
                    "url": drf_serializers.CharField(),
                    # Unsigned MEDIA_URL path — persist this one.
                    "path": drf_serializers.CharField(),
                },
            ),
            400: OpenApiResponse(description="Missing, oversized, or too-long audio"),
        },
    )
    def post(self, request):
        upload = request.FILES.get("audio")
        if upload is None:
            raise ValidationError({"audio": "An audio file is required."})

        max_seconds = MOCK_TEST_MAX_DURATION_SECONDS
        item = self._section_item(request)
        if item is not None and item.max_record_seconds:
            max_seconds = item.max_record_seconds

        validate_upload(
            upload,
            max_bytes=MOCK_TEST_MAX_UPLOAD_BYTES,
            max_seconds=max_seconds,
        )
        stored = default_storage.save(
            f"mock-tests/{timezone.now():%Y/%m}/{upload.name}", upload
        )
        # Signed so the clip plays back in an <audio> tag without exposing every
        # recording to anyone who guesses the filename (core/media.py). The
        # unsigned path is returned too: that is what callers persist on
        # Submission.audio_recording_url, and a stored signature would expire.
        return Response(
            {
                "url": media.signed_url(stored, request),
                "path": default_storage.url(stored),
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _section_item(request):
        """The TestSectionExercise this clip answers, when the caller names one.

        Scoped to the requesting student's own live attempt: the per-part limit
        is only meaningful there, and looking it up any other way would let one
        student probe another's attempt for section structure.
        """
        attempt_id = request.data.get("attempt_id")
        exercise_id = request.data.get("exercise_id")
        if not attempt_id or not exercise_id:
            return None
        return TestSectionExercise.objects.filter(
            exercise_id=exercise_id,
            section__attempts__attempt_id=attempt_id,
            section__attempts__attempt__student=request.user,
        ).first()


# ============================================================ Pronunciation practice
@extend_schema(tags=["pronunciation"])
class PronunciationDrillViewSet(viewsets.ModelViewSet):
    """Pronunciation drills (docs/research/04-pronunciation-practice.md §4.2).
    Any authenticated user browses; teachers/admins author (creator-scoped edit).
    The nested `attempts` action is the record → score → retry loop."""

    serializer_class = s.PronunciationDrillSerializer
    queryset = PronunciationDrill.objects.all().order_by("difficulty_level", "id")

    def get_queryset(self):
        qs = PronunciationDrill.objects.all().order_by("difficulty_level", "id")
        if self.action != "list":
            return qs
        params = self.request.query_params
        drill_type = params.get("drill_type")
        difficulty = params.get("difficulty")
        module_id = params.get("module_id")
        if drill_type:
            qs = qs.filter(drill_type=drill_type)
        if difficulty:
            qs = qs.filter(difficulty_level=difficulty)
        if module_id:
            qs = qs.filter(module_id=module_id)
        return qs

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return _perms(IsTeacherOrAdmin)
        return _perms()

    def get_throttles(self):
        # Only the recording POST is rate-capped; browsing history is not.
        if self.action == "attempts" and self.request.method == "POST":
            return [PronunciationAttemptThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _require_owner(self, drill):
        user = self.request.user
        if user.role == UserRole.TEACHER and drill.created_by_id != user.id:
            raise PermissionDenied("Not your drill")

    def update(self, request, *args, **kwargs):
        self._require_owner(self.get_object())
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_owner(self.get_object())
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        summary="List own attempts (GET) or record + score a new one (POST)",
        description=(
            "GET returns the current student's attempt history for this drill "
            "(retry loop). POST is multipart/form-data with field `audio`: the "
            "server validates size/type, stores the file, runs pronunciation "
            "assessment synchronously, and returns the scored attempt (201)."
        ),
        request={
            "multipart/form-data": s.PronunciationAttemptCreateSerializer,
        },
        responses={
            200: s.PronunciationAttemptSerializer(many=True),
            201: s.PronunciationAttemptSerializer,
        },
    )
    @action(
        detail=True, methods=["get", "post"], url_path="attempts",
        parser_classes=[MultiPartParser, FormParser],
    )
    def attempts(self, request, pk=None):
        drill = self.get_object()
        if request.method == "GET":
            qs = PronunciationAttempt.objects.filter(
                drill=drill, student=request.user
            ).order_by("-created_at", "-id")
            return Response(
                s.PronunciationAttemptSerializer(
                    qs, many=True, context={"request": request}
                ).data
            )

        # POST — students only record attempts.
        if request.user.role != UserRole.STUDENT:
            raise PermissionDenied("Only students record attempts.")
        upload = request.FILES.get("audio")
        if upload is None:
            raise ValidationError({"audio": "An audio file is required."})
        validate_upload(upload)  # size / type / duration caps
        attempt = PronunciationAttempt.objects.create(
            drill=drill, student=request.user, audio_file=upload,
        )
        try:
            assess_attempt(attempt)
        except (RuntimeError, ValueError, ImproperlyConfigured) as exc:
            # Loud failure when the real engine is misconfigured; the attempt row
            # survives (nullable scores) so a fixed deploy can re-score it.
            raise ValidationError(str(exc))
        except GeminiError as exc:
            raise AiUnavailable(f"AI service unavailable: {exc}")
        return Response(
            s.PronunciationAttemptSerializer(
                attempt, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["pronunciation"])
class PronunciationAttemptViewSet(viewsets.GenericViewSet):
    """Cross-drill attempt history for the current student. Attempts are always
    scoped to the requesting student (N2: read only your own)."""

    serializer_class = s.PronunciationAttemptSerializer

    def get_queryset(self):
        return (
            PronunciationAttempt.objects
            .filter(student=self.request.user)
            .order_by("-created_at", "-id")
        )

    def get_permissions(self):
        return _perms(IsStudent)

    @extend_schema(
        summary="The current student's attempts across all drills",
        parameters=[
            OpenApiParameter(
                "drill_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                description="Filter to a single drill",
            ),
        ],
        responses={200: s.PronunciationAttemptSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = self.get_queryset()
        drill_id = request.query_params.get("drill_id")
        if drill_id:
            qs = qs.filter(drill_id=drill_id)
        return Response(
            s.PronunciationAttemptSerializer(
                qs, many=True, context={"request": request}
            ).data
        )


# ============================================================ Meetings
def _broadcast_meeting(meeting_id, payload):
    """Best-effort push to a meeting's signaling group (room peers).
    Same stance as _broadcast_class: never breaks the HTTP request."""
    layer = get_channel_layer()
    if layer is None or meeting_id is None:
        return
    try:
        async_to_sync(layer.group_send)(
            f"meeting_{meeting_id}", {"type": "signal", "payload": payload}
        )
    except Exception:
        pass


@extend_schema(tags=["meetings"])
class MeetingViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """1:1 WebRTC meeting rooms. Create/end is teacher-side; students of the
    class can list and retrieve (join happens over the signaling socket)."""

    # Class-level queryset lets drf-spectacular derive the pk path type;
    # real role scoping happens in get_queryset.
    queryset = Meeting.objects.all()
    serializer_class = s.MeetingSerializer

    def get_queryset(self):
        qs = Meeting.objects.select_related("klass", "created_by").order_by(
            "-created_at", "-id"
        )
        user = self.request.user
        if user.role == UserRole.TEACHER:
            return qs.filter(klass__teacher=user)
        if user.role == UserRole.STUDENT:
            return qs.filter(klass__students__student=user).distinct()
        return qs

    def get_permissions(self):
        if self.action in ("create", "end"):
            return _perms(IsTeacherOrAdmin)
        return _perms()

    def perform_create(self, serializer):
        user = self.request.user
        klass = serializer.validated_data["klass"]
        if user.role == UserRole.TEACHER and klass.teacher_id != user.id:
            raise PermissionDenied("Not your class")
        # A future booking opens as "scheduled" and flips to active only when
        # the first peer joins (MeetingSignalConsumer stamps started_at); a
        # start-now room is active and joinable immediately.
        scheduled_at = serializer.validated_data.get("scheduled_at")
        is_future = scheduled_at is not None and scheduled_at > timezone.now()
        obj = serializer.save(
            created_by=user,
            status=MeetingStatus.SCHEDULED if is_future else MeetingStatus.ACTIVE,
        )
        _broadcast_class(
            obj.klass_id,
            {
                "event": "meeting_scheduled" if is_future else "meeting_started",
                "meeting_id": obj.id,
                "title": obj.title,
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            },
        )

    @extend_schema(
        summary="End a meeting (teacher/admin)",
        request=None,
        responses={200: s.MeetingSerializer},
    )
    @action(detail=True, methods=["post"], url_path="end")
    def end(self, request, pk=None):
        obj = self.get_object()
        user = request.user
        if user.role == UserRole.TEACHER and obj.klass.teacher_id != user.id:
            raise PermissionDenied("Not your meeting")
        if obj.status != MeetingStatus.ENDED:
            obj.status = MeetingStatus.ENDED
            obj.ended_at = timezone.now()
            obj.save(update_fields=["status", "ended_at"])
            # Tell peers still in the room so they hang up client-side.
            _broadcast_meeting(obj.id, {"type": "meeting_ended"})
            # Tell the class so open /meetings lists drop the Join button.
            _broadcast_class(
                obj.klass_id, {"event": "meeting_ended", "meeting_id": obj.id}
            )
            # Free the 1:1 occupancy slot tracked by MeetingSignalConsumer.
            cache.delete(f"meeting_occupancy_{obj.id}")
        return Response(s.MeetingSerializer(obj).data)

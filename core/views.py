"""DRF views — port of the FastAPI routers.

URL scheme adopts DRF conventions under /api/ (see config/urls.py). Role and
ownership rules mirror the original `require_roles` dependencies.
"""

from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import mixins, status, viewsets
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
    User,
    UserRole,
)
from .permissions import IsActiveUser, IsAdmin, IsStudent, IsTeacherOrAdmin

# Standard permission stack shared by every authenticated endpoint.
_BASE = [IsAuthenticated, IsActiveUser]


def _perms(*extra):
    return [p() for p in (*_BASE, *extra)]


# ============================================================ Auth
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = s.RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if User.objects.filter(email=ser.validated_data["email"]).exists():
            raise ValidationError({"email": "Email already registered"})
        user = ser.save()
        return Response(s.UserSerializer(user).data, status=status.HTTP_201_CREATED)


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
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("id")
    serializer_class = s.UserSerializer

    def get_permissions(self):
        if self.action in ("list", "destroy", "update", "partial_update"):
            return _perms(IsAdmin)
        return _perms()

    @action(detail=False, methods=["get", "put", "patch"])
    def me(self, request):
        if request.method == "GET":
            return Response(s.UserSerializer(request.user).data)
        ser = s.UserUpdateSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        if "status" in data and request.user.role != UserRole.ADMIN:
            data.pop("status")
        for field, value in data.items():
            setattr(request.user, field, value)
        request.user.save()
        return Response(s.UserSerializer(request.user).data)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.user.role != UserRole.ADMIN and request.user.id != obj.id:
            raise PermissionDenied("Forbidden")
        return Response(s.UserSerializer(obj).data)

    def update(self, request, *args, **kwargs):
        # Admin-only edit of an arbitrary user (mirrors admin_update_user).
        obj = self.get_object()
        ser = s.UserUpdateSerializer(
            obj, data=request.data, partial=kwargs.get("partial", False)
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(s.UserSerializer(obj).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


# ============================================================ Classes
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
        return Response(
            s.AssignmentSerializer(obj).data, status=status.HTTP_201_CREATED
        )


# ============================================================ Modules
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
        obj = ser.save(module=module)
        return Response(
            s.ExerciseSerializer(obj).data, status=status.HTTP_201_CREATED
        )


# ============================================================ Exercises
class ExerciseViewSet(
    mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    queryset = Exercise.objects.all().order_by("id")
    serializer_class = s.ExerciseSerializer

    def get_permissions(self):
        if self.action == "destroy":
            return _perms(IsTeacherOrAdmin)
        return _perms()

    @action(detail=True, methods=["get"], url_path="submissions")
    def submissions(self, request, pk=None):
        if request.user.role not in (UserRole.TEACHER, UserRole.ADMIN):
            raise PermissionDenied("Insufficient permissions")
        ex = self.get_object()
        qs = Submission.objects.filter(exercise=ex).order_by("id")
        return Response(s.SubmissionSerializer(qs, many=True).data)


# ============================================================ Submissions
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

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = Submission.objects.filter(student=request.user).order_by("id")
        return Response(s.SubmissionSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="feedback")
    def feedback(self, request, pk=None):
        sub = self.get_object()
        qs = Feedback.objects.filter(submission=sub).order_by("id")
        return Response(s.FeedbackSerializer(qs, many=True).data)


# ============================================================ Feedback
class FeedbackViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = Feedback.objects.all().order_by("id")
    serializer_class = s.FeedbackSerializer

    def get_permissions(self):
        return _perms(IsTeacherOrAdmin)

    def perform_create(self, serializer):
        serializer.save(reviewer=self.request.user)


# ============================================================ Progress
class ProgressViewSet(viewsets.GenericViewSet):
    queryset = StudentModuleProgress.objects.all().order_by("id")
    serializer_class = s.ProgressSerializer

    def get_permissions(self):
        if self.action == "create":
            return _perms(IsStudent)
        return _perms()

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

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        qs = StudentModuleProgress.objects.filter(student=request.user).order_by("id")
        return Response(s.ProgressSerializer(qs, many=True).data)

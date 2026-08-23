"""Root URLconf.

/admin/  -> Django admin (the reason for this migration)
/api/    -> DRF browsable API + endpoints
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
# pyrefly: ignore [missing-import]
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter
# pyrefly: ignore [missing-import]
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core import views

router = DefaultRouter()
router.register("users", views.UserViewSet, basename="user")
router.register("classes", views.ClassViewSet, basename="class")
router.register("modules", views.ModuleViewSet, basename="module")
router.register("exercises", views.ExerciseViewSet, basename="exercise")
router.register("submissions", views.SubmissionViewSet, basename="submission")
router.register("feedback", views.FeedbackViewSet, basename="feedback")
router.register("progress", views.ProgressViewSet, basename="progress")
router.register("study-materials", views.StudyMaterialViewSet, basename="study-material")
router.register("ai-models", views.AiModelViewSet, basename="ai-model")
# Mock tests (docs/research/01-mock-tests.md §4.4). Registered under
# /api/mock-tests/... so formats, templates and attempts share one namespace.
router.register(
    "mock-tests/formats", views.TestFormatViewSet, basename="mock-test-format",
)
router.register(
    "mock-tests/templates", views.MockTestTemplateViewSet,
    basename="mock-test-template",
)
router.register(
    "mock-tests/attempts", views.TestAttemptViewSet, basename="mock-test-attempt",
)
# Scoring rubrics (docs/research/02-scoring-rubrics.md §4.2).
router.register("rubrics", views.RubricTemplateViewSet, basename="rubric")
# Inline writing annotations (docs/research/03-writing-annotations.md §4.2).
router.register(
    "annotations", views.WritingAnnotationViewSet, basename="annotation",
)
# Pronunciation practice (docs/research/04-pronunciation-practice.md §4.2).
router.register(
    "pronunciation/drills", views.PronunciationDrillViewSet,
    basename="pronunciation-drill",
)
router.register(
    "pronunciation/attempts", views.PronunciationAttemptViewSet,
    basename="pronunciation-attempt",
)


def root(_request):
    return JsonResponse({"status": "ok", "service": "english-learning-api"})


auth_patterns = [
    path("register", views.RegisterView.as_view(), name="register"),
    path("login", views.LoginView.as_view(), name="login"),
    # simplejwt standard pair/refresh (email + password)
    path("token", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
]

# OpenAPI schema + interactive docs. Front-end fetches the raw schema from
# /api/schema/ (append ?format=json or ?format=yaml) and renders it, or feeds
# it to a client generator (openapi-typescript, orval, swagger-codegen).
docs_patterns = [
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

urlpatterns = [
    path("", root),
    path("admin/", admin.site.urls),
    path("api/auth/", include(auth_patterns)),
    path("api/dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("api/reports/grades", views.GradeReportView.as_view(), name="report-grades"),
    path("api/admin/activity/", views.AdminActivityView.as_view(), name="admin-activity"),
    path("api/admin/health/", views.AdminHealthView.as_view(), name="admin-health"),
    path("api/media/audio", views.AudioUploadView.as_view(), name="media-audio"),
    path("api/", include(docs_patterns)),
    path("api/", include(router.urls)),
]

# Dev/MVP media serving for real uploads (pronunciation audio). In production the
# S3 phase replaces this with presigned GET URLs; local disk serving is fine for
# MVP (docs/research/04-pronunciation-practice.md §4.2 / risk table). Attempt
# reads are already scoped to the owning student, so URLs are not discoverable.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

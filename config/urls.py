"""Root URLconf.

/admin/  -> Django admin (the reason for this migration)
/api/    -> DRF browsable API + endpoints
"""

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
    path("api/", include(docs_patterns)),
    path("api/", include(router.urls)),
]

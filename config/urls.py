"""Root URLconf.

/admin/  -> Django admin (the reason for this migration)
/api/    -> DRF browsable API + endpoints
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.routers import DefaultRouter
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


def root(_request):
    return JsonResponse({"status": "ok", "service": "english-learning-api"})


auth_patterns = [
    path("register", views.RegisterView.as_view(), name="register"),
    path("login", views.LoginView.as_view(), name="login"),
    # simplejwt standard pair/refresh (email + password)
    path("token", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
]

urlpatterns = [
    path("", root),
    path("admin/", admin.site.urls),
    path("api/auth/", include(auth_patterns)),
    path("api/", include(router.urls)),
]

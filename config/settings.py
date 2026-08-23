"""
Django settings for the English Learning API.

Ported from a FastAPI app. Config is driven by the same .env file the
FastAPI version used (DATABASE_URL, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES).
"""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-secret-change-me")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")


# Application definition

INSTALLED_APPS = [
    "daphne",                 # Daphne must precede django.contrib.staticfiles
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "channels",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    # local
    "core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# Database — parse the FastAPI-style DATABASE_URL (strip any +driver suffix).
def _db_from_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or "5432"),
    }


_DATABASE_URL = os.getenv("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in .env")
DATABASES = {"default": _db_from_url(_DATABASE_URL)}


AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
]


# DRF + JWT
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        "core.permissions.IsActiveUser",
    ),
    # OpenAPI 3 schema generation (drf-spectacular).
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# OpenAPI / API docs. The front-end fetches the machine-readable schema from
# /api/schema/ (JSON or YAML) and renders docs / generates a typed client.
SPECTACULAR_SETTINGS = {
    "TITLE": "English Learning API",
    "DESCRIPTION": (
        "REST API for the English-learning platform: users, classes, learning "
        "modules, exercises, submissions, feedback and progress. JWT auth."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # ExerciseType and SubmissionType share identical member values
    # (writing/speaking/reading/listening/quiz), so they collapse to one enum
    # component. Name that shared set explicitly to silence the rename warning.
    "ENUM_NAME_OVERRIDES": {
        "SkillTypeEnum": "core.models.ExerciseType.choices",
        # Mock tests add two more `status` fields; name them explicitly so the
        # generated client gets AttemptStatusEnum/SectionStatusEnum instead of
        # another hash-suffixed StatusXxxEnum.
        "AttemptStatusEnum": "core.models.AttemptStatus.choices",
        "SectionStatusEnum": "core.models.SectionStatus.choices",
    },
    # Bearer JWT — show the Authorize button in Swagger UI.
    "SECURITY": [{"jwtAuth": []}],
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {"name": "auth", "description": "Registration, login, JWT tokens"},
        {"name": "users", "description": "User accounts and self-profile"},
        {"name": "classes", "description": "Classes, enrollment, lesson plans, assignments"},
        {"name": "modules", "description": "Learning modules and their exercises"},
        {"name": "exercises", "description": "Exercises and their submissions"},
        {"name": "submissions", "description": "Student submissions and auto-grading"},
        {"name": "feedback", "description": "Reviewer feedback on submissions"},
        {"name": "progress", "description": "Per-student module progress"},
        {"name": "study-materials", "description": "Official study documents: Admin-managed, read by all"},
        {"name": "dashboard", "description": "Role-aware dashboard aggregates (student/teacher/admin)"},
        {"name": "reports", "description": "Analytical grade-report export (CSV; PDF deferred)"},
        {"name": "ai", "description": "Admin AI ML configuration and automated grading actions"},
        {"name": "admin", "description": "Platform management, health status, activity feed"},
        {"name": "mock-tests", "description": "Timed IELTS/TOEIC-style mock tests: formats, templates, attempts, reports"},
        {"name": "rubrics", "description": "Criteria x bands scoring matrices for Writing/Speaking review"},
        {"name": "annotations", "description": "Inline span-anchored teacher notes on writing submissions"},
        {"name": "pronunciation", "description": "Self-serve pronunciation drills: record, score, retry"},
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    # NOTE: leave USER_ID_CLAIM at its default ("user_id"). Using "sub" breaks
    # under PyJWT >= 2.10, which rejects a non-string "sub" claim.
}

# CORS settings
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_CREDENTIALS = True


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Media (real file uploads) — docs/research/04-pronunciation-practice.md §5 phase 0.
# The platform's first FileField (PronunciationAttempt.audio_file) lands here.
# Local disk for MVP; flip STORAGES["default"] to S3 (django-storages) later with
# no model change. MEDIA_URL is served by an authenticated file view in dev/MVP
# (see config/urls.py) and by presigned S3 GETs in the S3 phase.
MEDIA_ROOT = os.getenv("MEDIA_ROOT") or (BASE_DIR / "media")
MEDIA_URL = "/media/"

# AI grading integration settings
AI_BACKEND = os.getenv("AI_BACKEND", "mock").lower()

# Pronunciation-assessment engine switch (sibling of AI_BACKEND). "mock"
# (default, deterministic/offline) or "azure" (real; requires ffmpeg + Azure
# env keys) — docs/research/04-pronunciation-practice.md §4.4.
PRONUNCIATION_BACKEND = os.getenv("PRONUNCIATION_BACKEND", "mock").lower()

# Reports — PDF export is deferred behind reportlab; CSV is always available.
REPORTS_PDF_ENABLED = os.getenv("REPORTS_PDF_ENABLED", "false").lower() == "true"

# Real-time channel layer. Dev = in-memory (single process, no broker).
# Prod = Redis (set REDIS_URL); requires `channels-redis` + a real ASGI server.
if os.getenv("REDIS_URL"):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [os.getenv("REDIS_URL")]},
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

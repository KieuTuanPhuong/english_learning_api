"""
Django settings for the English Learning API.

Ported from a FastAPI app. Config is driven by the same .env file the
FastAPI version used (DATABASE_URL, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES).
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------- env helpers
def env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var. Accepts 1/true/yes/on (case-insensitive)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list:
    """Read a comma-separated env var into a list, dropping blanks."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# `DEBUG=false` is the production switch: it turns on TLS redirects, HSTS,
# secure cookies (see "Production security" below) and makes the guards in this
# file fatal rather than advisory. Deploys must set it explicitly.
DEBUG = env_bool("DEBUG", True)
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

INSECURE_SECRET_KEY = "django-insecure-dev-secret-change-me"
SECRET_KEY = os.getenv("SECRET_KEY") or INSECURE_SECRET_KEY

# Deliberately NOT "*": a production box that forgets ALLOWED_HOSTS gets a loud
# DisallowedHost (400) on the first request instead of silently trusting any
# Host header (cache poisoning / password-reset link forgery).
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

if not DEBUG:
    if SECRET_KEY == INSECURE_SECRET_KEY:
        raise ImproperlyConfigured(
            "SECRET_KEY is unset (or still the shared dev value) while DEBUG is "
            "false. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    if "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS=* is not allowed with DEBUG=false. List the real "
            "hostnames this API answers on, e.g. ALLOWED_HOSTS=api.example.com"
        )


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
    # Serves everything collectstatic wrote, straight from the app process, so
    # the admin renders without an Nginx alias into the deploy user's home dir.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
    query = parse_qs(parsed.query)

    options = {}
    # Managed Postgres (RDS, Cloud SQL, Neon, Supabase) is reached with
    # ?sslmode=require; pass that and a connect timeout through to libpq.
    sslmode = (query.get("sslmode") or [os.getenv("DB_SSLMODE", "")])[0]
    if sslmode:
        options["sslmode"] = sslmode
    options["connect_timeout"] = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or "5432"),
        # Reuse connections between requests in production; every new one costs
        # a TCP handshake plus Postgres auth. 0 (dev default) closes each time.
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "0" if DEBUG else "60")),
        # Cheap liveness probe on a reused connection, so a restarted database
        # or a dropped idle connection surfaces as a retry, not a 500.
        "CONN_HEALTH_CHECKS": not DEBUG,
        "OPTIONS": options,
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
        "MeetingStatusEnum": "core.models.MeetingStatus.choices",
        "AiInsightKindEnum": "core.models.AiInsightKind.choices",
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
        {"name": "meetings", "description": "1:1 teacher-student WebRTC meeting rooms (P2P media, WS signaling)"},
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

# CORS — the browser origins the web client is served from. Production must
# set CORS_ALLOWED_ORIGINS explicitly; the defaults only cover local dev.
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000",
)
CORS_ALLOW_CREDENTIALS = True


def _csrf_origin(host: str) -> str:
    """ALLOWED_HOSTS entry -> CSRF origin. `.example.com` -> `https://*.example.com`."""
    scheme = "http" if DEBUG else "https"
    return f"{scheme}://*{host}" if host.startswith(".") else f"{scheme}://{host}"


# Django 4+ rejects cross-origin POSTs (the admin login behind an HTTPS proxy
# included) unless the origin is listed here. Default to the hosts we serve.
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS") or [
    _csrf_origin(host) for host in ALLOWED_HOSTS if host not in ("*", "localhost", "127.0.0.1", "[::1]")
]


# ----------------------------------------------------- Production security
# All of these follow DEBUG: they switch on together when DEBUG=false, and each
# one can still be overridden by env for edge cases (TLS terminated upstream,
# an internal-only deployment, a staging box on plain HTTP).

# Nginx/ALB terminates TLS and forwards over plain HTTP. Without this Django
# sees http://, so is_secure() is false: SECURE_SSL_REDIRECT would loop forever
# and "secure" cookies would never be set.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", not DEBUG)

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
# Load balancers and uptime probes hit /health/ over plain HTTP on the private
# interface; a 301 there reads as an outage.
SECURE_REDIRECT_EXEMPT = [r"^health/?$"]

SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)

# HSTS is a commitment: browsers refuse plain HTTP for this long. Start at one
# year once TLS is verified; set SECURE_HSTS_SECONDS=0 to back out.
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
# Preload is effectively irreversible (removal takes months). Opt in knowingly.
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Upload ceiling for JSON/form bodies. Audio arrives as multipart and is capped
# separately in core/audio.py (5 MB drills, 25 MB mock-test parts).
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# `manage.py collectstatic` writes here; WhiteNoise serves it. Required — the
# command errors out without STATIC_ROOT, which is step 5 of every deploy.
STATIC_ROOT = Path(os.getenv("STATIC_ROOT") or (BASE_DIR / "staticfiles"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Compressed but NOT hashed/manifested: the admin is the only consumer
        # and a manifest turns a forgotten collectstatic into a 500 on every
        # admin page. Swap to CompressedManifestStaticFilesStorage if you want
        # far-future cache headers and always run collectstatic on deploy.
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Media (real file uploads) — docs/research/04-pronunciation-practice.md §5 phase 0.
# The platform's first FileField (PronunciationAttempt.audio_file) lands here.
# Local disk for MVP; flip STORAGES["default"] to S3 (django-storages) later with
# no model change. MEDIA_URL is served by an authenticated file view in dev/MVP
# (see config/urls.py) and by presigned S3 GETs in the S3 phase.
MEDIA_ROOT = os.getenv("MEDIA_ROOT") or (BASE_DIR / "media")
MEDIA_URL = "/media/"

# Uploaded audio is student voice — never public. `core/media.py` hands out
# short-lived signed URLs and `ProtectedMediaView` refuses anything else, so a
# leaked link expires instead of staying downloadable forever. Keep this longer
# than a grading session but far short of "forever".
MEDIA_URL_TTL = int(os.getenv("MEDIA_URL_TTL", str(7 * 24 * 3600)))

# With Nginx in front, let it stream the file after Django authorises it: the
# view returns an empty response carrying X-Accel-Redirect and the worker is
# free immediately. Requires the `internal` location from docs/DEPLOYMENT.md.
MEDIA_X_ACCEL_REDIRECT = env_bool("MEDIA_X_ACCEL_REDIRECT", False)
MEDIA_X_ACCEL_PREFIX = os.getenv("MEDIA_X_ACCEL_PREFIX", "/protected-media/")

# AI grading integration settings: "mock" (deterministic), "llm" (alias
# "gemini": live — writing graded by AI_GRADING_MODEL, speaking transcribed +
# scored by Gemini), "real" (legacy stub, raises).
AI_BACKEND = os.getenv("AI_BACKEND", "mock").lower()

# AI coaching assistants (teacher-feedback review, student mistake explanation;
# core/ai/assist.py). "mock" = deterministic/offline, "gemini" = live Gemini
# call via GEMINI_API_KEY. Defaults to gemini only when a key is present.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# NVIDIA NIM (build.nvidia.com), OpenAI-compatible; hosts Kimi K3 / DeepSeek V4.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "moonshotai/kimi-k3")
NVIDIA_TIMEOUT = float(os.getenv("NVIDIA_TIMEOUT", "600"))  # per-read socket timeout (NIM queues 3-5 min)
NVIDIA_THINKING = os.getenv("NVIDIA_THINKING", "false").lower() == "true"  # keep reasoning pass on
# Text-task routing (core/ai/llm.py): "provider[:model]". Audio always -> Gemini.
AI_TEXT_PROVIDER = os.getenv("AI_TEXT_PROVIDER", "gemini" if GEMINI_API_KEY else "nvidia").lower()
AI_GRADING_MODEL = os.getenv("AI_GRADING_MODEL", "")   # e.g. nvidia:moonshotai/kimi-k3
AI_ASSIST_MODEL = os.getenv("AI_ASSIST_MODEL", "")     # e.g. nvidia:deepseek-ai/deepseek-v4-pro-0813
AI_FALLBACK_PROVIDER = os.getenv("AI_FALLBACK_PROVIDER", "gemini" if GEMINI_API_KEY else "").lower()
AI_ASSIST_BACKEND = (
    os.getenv("AI_ASSIST_BACKEND") or ("llm" if (GEMINI_API_KEY or NVIDIA_API_KEY) else "mock")
).lower()

# Pronunciation-assessment engine switch (sibling of AI_BACKEND). "mock"
# (default, deterministic/offline), "gemini" (live audio assessment via
# GEMINI_API_KEY, no ffmpeg) or "azure" (stub; requires ffmpeg + Azure keys)
# — docs/research/04-pronunciation-practice.md §4.4.
PRONUNCIATION_BACKEND = os.getenv("PRONUNCIATION_BACKEND", "mock").lower()

# The test suite must never spend Gemini quota or depend on the network: force
# every AI switch to the deterministic mock under `manage.py test`. Individual
# tests opt back in with override_settings(...="gemini") + a patched client.
if len(sys.argv) > 1 and sys.argv[1] == "test":
    AI_BACKEND = PRONUNCIATION_BACKEND = AI_ASSIST_BACKEND = "mock"
    AI_TEXT_PROVIDER = "gemini"
    AI_GRADING_MODEL = AI_ASSIST_MODEL = AI_FALLBACK_PROVIDER = ""

# Reports — PDF export is deferred behind reportlab; CSV is always available.
REPORTS_PDF_ENABLED = os.getenv("REPORTS_PDF_ENABLED", "false").lower() == "true"

# Logging — one console handler; systemd/journald or the container runtime owns
# the log stream from there. Django's default config swallows everything except
# 5xx emails, which nothing is configured to send.
# INFO in both environments: a DEBUG root logger drowns the app's own output in
# asyncio/urllib3 internals. Raise it per-logger via LOG_LEVEL when debugging.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        # Unhandled exceptions in a view. Without this they are invisible once
        # DEBUG is off.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Every AI provider call, fallback and failure (core/ai/llm.py).
        "core.ai": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        # Chatty at DEBUG: one line per query / one per event-loop selector.
        "django.db.backends": {"level": "INFO"},
        "asyncio": {"level": "INFO"},
        "daphne": {"level": "INFO"},
    },
}


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
    # In-memory groups do not cross process boundaries: with more than one
    # worker, a WebSocket broadcast reaches only the clients on the same
    # process. Fine for dev and a single Daphne process, wrong for a scaled
    # deployment — set REDIS_URL there.
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

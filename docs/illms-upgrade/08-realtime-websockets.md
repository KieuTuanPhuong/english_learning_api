# 08 — Real-time layer (Django Channels / ASGI consumers for live speaking & classroom notifications)

**Status:** Proposed · **Depends on:** [06 — AI evaluation suite](./06-ai-evaluation-suite.md) (the speaking scorer `transcribe_and_score_speaking`), [04 — Grading, evaluations & inbox](./04-grading-evaluations-inbox.md) (Feedback creation + `Submission.status`), [01 — Model foundations](./01-model-foundations.md) (fields) · **Spec refs:** docs.md §1 (Real-Time Interactivity — "Dedicated WebSocket Server … bi-directional, full-duplex … instant low-latency conversational speaking exercises and live classroom notifications"); §3.2 UC-09 (practice speaking); §6 directive #4 (WebSocket Isolation — ASGI consumers, Redis/broker, bypass WSGI)

**Estimated blast radius:** `requirements.txt` (+`channels`, +`channels-redis`, +`daphne` — all NEW), `config/settings.py` (+`daphne`/`channels` in `INSTALLED_APPS`, +`ASGI_APPLICATION`, +`CHANNEL_LAYERS`), `config/asgi.py` (**rewritten** from the plain 16-line WSGI-style callable to a `ProtocolTypeRouter`), `core/routing.py` (NEW), `core/consumers.py` (NEW), `core/ws_auth.py` (NEW JWT middleware), `core/views.py` (+`group_send` broadcast calls in `ClassViewSet.assignments` create and `FeedbackViewSet.perform_create` — additive). No model changes.

---

## Goal

Add the real-time layer docs.md §1 calls for: a dedicated WebSocket surface running on Django Channels over ASGI, isolated from the synchronous DRF/WSGI request path (docs.md §6 directive #4). Two channels: (a) a **live speaking session** consumer that accepts streamed audio frames during a speaking exercise and, on end-of-stream, hands the recording to the AI speaking scorer from [06](./06-ai-evaluation-suite.md) and pushes the result back over the socket (UC-09); and (b) a **notifications** consumer that broadcasts classroom events (new assignment, feedback posted) to enrolled students in real time. Per the repo's realism policy, audio frames are **mock** (base64/URL strings, no real binary streaming to Whisper) and dev runs on the in-memory channel layer — Redis + an ASGI server are flagged as deferred production infra.

## Why (gap)

The platform has **no real-time path** today — every interaction is a synchronous DRF HTTP request:

- `config/asgi.py` is the stock 16-line Django ASGI shim: `application = get_asgi_application()`. It serves HTTP over ASGI but has **no WebSocket protocol router** — a `ws://` connection is rejected.
- `config/settings.py` declares `WSGI_APPLICATION = "config.wsgi.application"` and has **no** `ASGI_APPLICATION` or `CHANNEL_LAYERS`. `INSTALLED_APPS` has no `channels`.
- `requirements.txt` has no `channels`, `channels-redis`, or ASGI server (`daphne`/`uvicorn`). The whole stack runs `manage.py runserver` (WSGI dev server).
- There is no mechanism to push anything to a client; students only learn about a new assignment or a grade by polling (e.g. re-fetching `GET /api/dashboard/` from [05](./05-dashboards-and-reports.md)).
- DRF authentication (`JWTAuthentication`) and permission classes (`IsActiveUser`) **do not apply to Channels consumers** — consumers see Channels `scope`, not a DRF `request`, so JWT must be re-validated in an ASGI middleware (docs.md §6 directive #3 still wants identity from the JWT, not a session).

This file closes the gap **additively**: the existing HTTP API is untouched (it keeps running on WSGI in dev, or HTTP-over-ASGI in prod); WebSockets are a parallel surface.

> **Dependency note:** the speaking consumer delegates scoring to `core.ai.service` from [06](./06-ai-evaluation-suite.md) and writes an AI `Feedback` row (`is_ai_generated=True`, `reviewer=None`) exactly as [04](./04-grading-evaluations-inbox.md)/[06](./06-ai-evaluation-suite.md) define. If 06 is not yet applied, the speaking consumer's end-of-stream handler should no-op with a "scorer unavailable" frame rather than fail. This file does **not** define any model field.

---

## Changes (file by file)

### `requirements.txt`

Append the Channels stack. `daphne` is the reference ASGI server (it also patches `runserver` to serve ASGI in dev when listed first in `INSTALLED_APPS`). `channels-redis` is only needed in production (the Redis-backed layer); dev uses the in-memory layer and does not import it, but pin it so the prod path is reproducible.

```text
channels==4.2.0
channels-redis==4.2.1
daphne==4.1.2
```

> Install into the existing venv: `./venv/bin/pip install -r requirements.txt`.

### `config/settings.py`

Three additions — no edits to existing keys.

1. **`INSTALLED_APPS`** — add `daphne` **first** (so its `runserver` override wins) and `channels`:

```python
INSTALLED_APPS = [
    "daphne",                 # NEW — must precede django.contrib.staticfiles / admin so runserver serves ASGI
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "channels",               # NEW
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    # local
    "core",
]
```

2. **ASGI application** — point Channels at the router (added next to the existing `WSGI_APPLICATION = "config.wsgi.application"` line; keep WSGI for the sync path):

```python
ASGI_APPLICATION = "config.asgi.application"
```

3. **Channel layer** — in-memory for dev (single process, zero infra), Redis for prod. Env-gate it so the prod path is one variable away and never imports Redis in dev:

```python
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
```

> ⚠️ `InMemoryChannelLayer` is **per-process** — `group_send` only reaches consumers in the same process. It is fine for `runserver`/demo but does not work across multiple workers. Production MUST set `REDIS_URL`. This is the deferred infra boundary (see below).

### `config/asgi.py` (rewrite)

The current file is the plain shim:

```python
# BEFORE (current — 16 lines)
import os
from django.core.asgi import get_asgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_asgi_application()
```

Replace with a `ProtocolTypeRouter` that keeps HTTP on the standard Django app and routes `websocket` through the JWT middleware + URL router. **Set `DJANGO_SETTINGS_MODULE` and call `get_asgi_application()` BEFORE importing anything that touches models/consumers** (the Django app registry must be populated first):

```python
# AFTER
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialise Django (populates the app registry) before importing consumers,
# which import models. Order matters.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from core.routing import websocket_urlpatterns  # noqa: E402
from core.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
```

### `core/ws_auth.py` (NEW — JWT auth for the socket)

DRF's `JWTAuthentication` never runs for consumers, so resolve the user from the token here. Accept the token from the `?token=` query string (browsers cannot set `Authorization` on a `WebSocket`). Reject suspended users to mirror `core.permissions.IsActiveUser`. Use `simplejwt`'s validator and the project's `AUTH_USER_MODEL`.

```python
"""ASGI middleware: authenticate the WebSocket from a ?token= JWT.

DRF auth/permission classes do not apply to Channels consumers — they see the
ASGI ``scope``, not a DRF ``request``. This mirrors JWTAuthentication +
IsActiveUser for the socket surface (docs.md §6 directives #3/#4)."""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _user_from_token(raw_token):
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    from core.models import UserStatus

    auth = JWTAuthentication()
    try:
        validated = auth.get_validated_token(raw_token)
        user = auth.get_user(validated)
    except (InvalidToken, TokenError):
        return AnonymousUser()
    # Mirror IsActiveUser: suspended/inactive users are rejected on the socket too.
    if getattr(user, "status", None) != UserStatus.ACTIVE:
        return AnonymousUser()
    return user


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        token = (query.get("token") or [None])[0]
        scope["user"] = await _user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
```

> A consumer then rejects the connection itself when `self.scope["user"].is_anonymous` (see below). Closing with code `4401` is the convention for "unauthenticated".

### `core/routing.py` (NEW)

```python
"""WebSocket URL routing (parallel to config/urls.py for HTTP)."""

from django.urls import path

from core import consumers

websocket_urlpatterns = [
    path("ws/speaking/<int:exercise_id>/", consumers.SpeakingConsumer.as_asgi()),
    path("ws/notifications/", consumers.NotificationConsumer.as_asgi()),
]
```

### `core/consumers.py` (NEW)

Two `AsyncJsonWebsocketConsumer`s.

**`SpeakingConsumer`** — a per-exercise live speaking session (UC-09). The client opens `ws/speaking/<exercise_id>/?token=<jwt>`, sends `{"type": "audio_chunk", "data": "<base64-or-url>"}` frames (mock — server just acks; no real binary/Whisper streaming), then `{"type": "end", "audio_recording_url": "<mock url>"}`. On `end`, it creates a `Submission` (speaking) and runs the AI scorer from [06](./06-ai-evaluation-suite.md), then pushes the score frame back.

```python
"""Channels consumers — live speaking session + classroom notifications.

Audio handling is MOCK (string frames), consistent with the repo's mock-URL
stance. Real scoring is delegated to core.ai.service (file 06)."""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class SpeakingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close(code=4401)
            return
        self.exercise_id = self.scope["url_route"]["kwargs"]["exercise_id"]
        exercise = await self._get_speaking_exercise(self.exercise_id)
        if exercise is None:
            await self.close(code=4404)
            return
        self.chunks = 0
        await self.accept()
        await self.send_json({"type": "ready", "exercise_id": self.exercise_id})

    async def receive_json(self, content):
        msg_type = content.get("type")
        if msg_type == "audio_chunk":
            # MOCK: do not buffer real audio; just count + ack for backpressure.
            self.chunks += 1
            await self.send_json({"type": "ack", "chunk": self.chunks})
        elif msg_type == "end":
            url = content.get("audio_recording_url") or "https://mock.cdn/recordings/live-session.m4a"
            result = await self._score(self.exercise_id, self.scope["user"].id, url)
            await self.send_json({"type": "result", **result})
        else:
            await self.send_json({"type": "error", "detail": f"unknown message type {msg_type!r}"})

    @database_sync_to_async
    def _get_speaking_exercise(self, exercise_id):
        from core.models import Exercise, ExerciseType
        return (
            Exercise.objects.filter(id=exercise_id, exercise_type=ExerciseType.SPEAKING)
            .first()
        )

    @database_sync_to_async
    def _score(self, exercise_id, student_id, audio_url):
        # Delegates to file 06. Creates the Submission + AI Feedback row
        # (is_ai_generated=True, reviewer=None) and returns {score, comments, transcript}.
        from core.models import Submission, SubmissionType
        try:
            from core.ai.service import transcribe_and_score_speaking  # file 06
        except ImportError:
            return {"error": "AI scorer unavailable"}

        sub = Submission.objects.create(
            exercise_id=exercise_id,
            student_id=student_id,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url=audio_url,
        )
        return transcribe_and_score_speaking(sub)  # writes AI Feedback, flips status to AI_Graded
```

> **Graceful degradation if 06 is not yet applied:** the `from core.ai.service import …` is wrapped in `try/except ImportError`, returning `{"error": "AI scorer unavailable"}` instead of raising. This lets 08 ship before 06.

**`NotificationConsumer`** — joins the connecting user to per-class groups for every class they belong to (teacher's `classes_taught` or student's enrollments via `ClassStudent`), so classroom events broadcast to them live.

```python
class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close(code=4401)
            return
        self.groups_joined = await self._class_groups(user)
        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        for group in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    # Handler invoked by group_send(type="notify"); see the view broadcasts below.
    async def notify(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _class_groups(self, user):
        from core.models import Class, ClassStudent, UserRole
        if user.role in (UserRole.TEACHER, UserRole.ADMIN):
            ids = Class.objects.filter(teacher=user).values_list("id", flat=True)
        else:
            ids = ClassStudent.objects.filter(student=user).values_list("klass_id", flat=True)
        return [f"class_{cid}" for cid in ids]
```

### `core/views.py` (additive broadcast calls)

Emit a notification when a teacher creates an assignment and when feedback is posted. `group_send` is async; call it from the synchronous DRF views via `async_to_sync`. Add a tiny helper and call it from the two existing write paths — **no behavior change** to the HTTP responses.

```python
# top of core/views.py
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


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
```

In `ClassViewSet.assignments` (the assignment-create path), after the assignment is saved:

```python
        _broadcast_class(
            assignment.klass_id,
            {"event": "assignment_created", "assignment_id": assignment.id,
             "exercise_id": assignment.exercise_id, "due_date": str(assignment.due_date)},
        )
```

In `FeedbackViewSet.perform_create` (see [04](./04-grading-evaluations-inbox.md), which already edits this method for status transitions), after the feedback + status save — push to the submission's class so the student sees the grade land:

```python
        klass_id = getattr(feedback.submission.assignment, "klass_id", None)
        _broadcast_class(
            klass_id,
            {"event": "feedback_posted", "submission_id": feedback.submission_id,
             "score": str(feedback.score) if feedback.score is not None else None},
        )
```

> Coordinate with [04](./04-grading-evaluations-inbox.md): that file owns the `FeedbackViewSet.perform_create` edit. Add the broadcast line **inside** the method 04 already defines — do not write a second competing version of `perform_create`. Self-practice submissions (`assignment is None`) have no class group; `_broadcast_class(None, …)` is a safe no-op.

---

## Migrations

**None.** This file adds no model fields. Channels needs no DB tables for the in-memory or Redis layer. (The Redis layer stores nothing in Postgres.)

To run the ASGI stack in dev after the settings/requirements changes:

```bash
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py runserver   # daphne (listed first in INSTALLED_APPS) now serves ASGI → WebSockets work
```

For production (multi-process), set `REDIS_URL` and run a real ASGI server:

```bash
REDIS_URL=redis://localhost:6379/0 ./venv/bin/daphne -b 0.0.0.0 -p 8000 config.asgi:application
```

## RBAC / permissions

- **Connection auth:** `core/ws_auth.py` resolves the JWT; anonymous or suspended/inactive users are set to `AnonymousUser` and every consumer closes with `4401`. This mirrors `IsActiveUser` (docs.md §6 directives #3/#4).
- **`SpeakingConsumer`:** any active authenticated user with a valid speaking `exercise_id` (students for practice — RBAC row 12; teachers may test their own exercises). No role gate beyond active-user.
- **`NotificationConsumer`:** scoped by membership — a user only joins groups for classes they teach or are enrolled in, so they cannot eavesdrop on other classrooms.
- **Broadcasts** originate from existing HTTP write paths already guarded by their DRF permissions ([04](./04-grading-evaluations-inbox.md) feedback create = `IsTeacherOrAdmin`; assignment create = teacher/admin), so no new authorization surface is introduced.

## Acceptance criteria & smoke test

- [ ] `./venv/bin/pip install -r requirements.txt` installs `channels`, `channels-redis`, `daphne` cleanly.
- [ ] `runserver` boots with `daphne` (log line shows ASGI/Daphne, not WSGIServer) and the existing HTTP API still works (`curl http://127.0.0.1:8000/` → `{"status":"ok",…}`).
- [ ] A `ws://` connection **without** a token closes immediately (code 4401).
- [ ] A speaking-session round-trip works with a seeded student token (`alice.student@english.app` / `password123`) against a seeded speaking exercise (e.g. "Ordering at a cafe", id 1).
- [ ] Posting feedback over HTTP (teacher) pushes a `feedback_posted` frame to a connected enrolled student on `ws/notifications/`.

Get a token, then drive the socket with a tiny client (the repo has no JS client; use `websockets` in the venv or `wscat`):

```bash
# 1. token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"alice.student@english.app","password":"password123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2. live speaking session (requires `pip install websockets` in the venv)
./venv/bin/python - "$TOKEN" <<'PY'
import asyncio, json, sys
import websockets  # pip install websockets

async def main(token):
    uri = f"ws://127.0.0.1:8000/ws/speaking/1/?token={token}"
    async with websockets.connect(uri) as ws:
        print("server:", await ws.recv())                       # {"type":"ready",...}
        await ws.send(json.dumps({"type": "audio_chunk", "data": "AAAA"}))
        print("server:", await ws.recv())                       # {"type":"ack","chunk":1}
        await ws.send(json.dumps({"type": "end",
                                  "audio_recording_url": "https://mock.cdn/recordings/live.m4a"}))
        print("server:", await ws.recv())                       # {"type":"result","score":...}

asyncio.run(main(sys.argv[1]))
PY
```

(If [06](./06-ai-evaluation-suite.md) is not yet applied, the `end` frame returns `{"type":"result","error":"AI scorer unavailable"}` — that is the documented degraded behavior, still a pass for 08 in isolation.)

## Deferred / out of scope

- **Real audio streaming & Whisper.** Frames are mock strings; the consumer never buffers binary audio or calls Whisper directly — scoring is delegated to [06](./06-ai-evaluation-suite.md)'s backend (mock by default). Real binary chunking + a streaming STT integration is future work.
- **Redis + production ASGI server.** Dev uses `InMemoryChannelLayer` (single process; `group_send` does not cross workers). Production requires `REDIS_URL` + `daphne`/`uvicorn` behind the load balancer — pinned in requirements but not wired into any deploy config (there is none in the repo yet).
- **Presence / typing indicators / chat.** The UI brief lists real-time chat as out of scope; only exercise scoring + classroom notifications are implemented.
- **Token refresh over the socket.** A long-lived socket will outlive a 60-min access token; reconnect-on-expiry is left to the client. A refresh handshake frame is a future add.
- **Per-user notification fan-out / unread store.** Notifications are ephemeral (fire-and-forget to connected sockets). Durable/unread notifications would need a model + the email/notification system that docs.md and the UI brief mark out of scope.

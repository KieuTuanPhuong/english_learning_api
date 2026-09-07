"""Channels consumers — live speaking session + classroom notifications.

Audio handling is MOCK (string frames), consistent with the repo's mock-URL
stance. Real scoring is delegated to core.ai.service (file 06)."""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache


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

    # Handler invoked by group_send(type="notify"); see the view broadcasts.
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


class MeetingSignalConsumer(AsyncJsonWebsocketConsumer):
    """WebRTC signaling relay for 1:1 meeting rooms (ws/meetings/<id>/).

    The server never inspects SDP/ICE payloads — it only forwards frames to
    the other peer in the room group. Close codes: 4401 unauthenticated,
    4403 not a participant of the meeting's class, 4404 no such live meeting,
    4409 room already has its two participants.

    Unlike the other consumers, the socket is ACCEPTED before the checks run:
    a pre-accept close reaches browsers as an opaque 1006, so the client could
    never distinguish "ended" from a network error otherwise.
    """

    RELAYED_TYPES = ("offer", "answer", "ice")
    OCCUPANCY_TTL = 24 * 3600  # backstop against leaked counts

    async def connect(self):
        await self.accept()
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close(code=4401)
            return
        self.meeting_id = self.scope["url_route"]["kwargs"]["meeting_id"]
        allowed = await self._can_join(user, self.meeting_id)
        if allowed is None:
            await self.close(code=4404)
            return
        if not allowed:
            await self.close(code=4403)
            return
        # 1:1 cap — refuse a third concurrent connection so a classmate can't
        # receive the peers' SDP/ICE or scramble an established call.
        occupancy_key = f"meeting_occupancy_{self.meeting_id}"
        cache.add(occupancy_key, 0, timeout=self.OCCUPANCY_TTL)
        try:
            count = cache.incr(occupancy_key)
        except ValueError:  # key evicted between add and incr
            cache.set(occupancy_key, 1, timeout=self.OCCUPANCY_TTL)
            count = 1
        self._counted = True
        if count > 2:
            await self.close(code=4409)
            return
        self.group_name = f"meeting_{self.meeting_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        # Subscribe-then-verify: end() commits the status flip BEFORE it
        # broadcasts, so a meeting_ended that fired before our group_add is
        # guaranteed visible to this re-read (closes the connect/end race).
        if not await self._is_active(self.meeting_id):
            await self.close(code=4404)
            return
        # First peer through the door starts the clock (and opens a room that
        # was merely scheduled). Both peers read started_at off their own
        # "ready" frame, so their timers agree without another round-trip.
        started_at = await self._mark_started(self.meeting_id)
        await self.send_json({
            "type": "ready",
            "meeting_id": self.meeting_id,
            "started_at": started_at,
        })
        # The peer already in the room answers this by sending an offer.
        await self._relay({
            "type": "peer_joined",
            "user_id": user.id,
            "full_name": user.full_name,
            "role": user.role,
        })

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self._relay({"type": "peer_left"})
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if getattr(self, "_counted", False):
            try:
                cache.decr(f"meeting_occupancy_{self.meeting_id}")
            except ValueError:
                pass  # key expired/cleared; nothing to release

    async def receive_json(self, content):
        # Guard non-dict valid JSON ("ping", 42, [...]): .get would raise and
        # kill the socket without running disconnect (no peer_left relayed).
        if not isinstance(content, dict):
            await self.send_json({"type": "error", "detail": "frames must be JSON objects"})
            return
        msg_type = content.get("type")
        if msg_type in self.RELAYED_TYPES:
            await self._relay(content)
        else:
            await self.send_json(
                {"type": "error", "detail": f"unknown message type {msg_type!r}"}
            )

    async def _relay(self, payload):
        # `sid` identifies the originating connection (channel_name is an
        # opaque random string) so clients can drop stale frames from an old
        # connection of the same peer, e.g. a late peer_left after a rejoin.
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "signal",
                "payload": {**payload, "sid": self.channel_name},
                "sender": self.channel_name,
            },
        )

    # Handler invoked by group_send(type="signal") — from peers (skip the
    # originator) or from views._broadcast_meeting (no sender, send to all).
    async def signal(self, event):
        if event.get("sender") == self.channel_name:
            return
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _is_active(self, meeting_id):
        from core.models import Meeting, MeetingStatus
        return Meeting.objects.filter(
            id=meeting_id,
            status__in=(MeetingStatus.ACTIVE, MeetingStatus.SCHEDULED),
        ).exists()

    @database_sync_to_async
    def _mark_started(self, meeting_id):
        """Stamp started_at on the first join and open a scheduled room.
        The conditional UPDATE makes concurrent joins idempotent — whoever
        loses the race still reads the winner's timestamp back."""
        from django.utils import timezone
        from core.models import Meeting, MeetingStatus
        Meeting.objects.filter(id=meeting_id, started_at__isnull=True).update(
            started_at=timezone.now(), status=MeetingStatus.ACTIVE
        )
        started_at = (
            Meeting.objects.filter(id=meeting_id)
            .values_list("started_at", flat=True)
            .first()
        )
        return started_at.isoformat() if started_at else None

    @database_sync_to_async
    def _can_join(self, user, meeting_id):
        """None → 4404 (missing/ended), False → 4403, True → join."""
        from core.models import ClassStudent, Meeting, MeetingStatus, UserRole
        meeting = (
            Meeting.objects.select_related("klass").filter(id=meeting_id).first()
        )
        if meeting is None or meeting.status == MeetingStatus.ENDED:
            return None
        if user.role == UserRole.ADMIN:
            return True
        if user.role == UserRole.TEACHER:
            return meeting.klass.teacher_id == user.id
        return ClassStudent.objects.filter(
            klass_id=meeting.klass_id, student=user
        ).exists()

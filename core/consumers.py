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

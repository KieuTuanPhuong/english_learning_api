import asyncio
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITransactionTestCase
# pyrefly: ignore [missing-import]
from rest_framework_simplejwt.tokens import RefreshToken

from channels.testing import WebsocketCommunicator
from config.asgi import application

from core.models import (
    AiModel,
    Assignment,
    Class,
    ClassStudent,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    Submission,
    SubmissionType,
    SubmissionStatus,
    StudyMaterial,
    SystemLog,
    User,
    UserRole,
    UserStatus,
)


class UpgradeTests(APITransactionTestCase):
    def setUp(self):
        # Create users
        self.admin = User.objects.create_user(
            email="admin@test.app", password="password123", full_name="Admin User",
            role=UserRole.ADMIN, is_staff=True, is_superuser=True
        )
        self.teacher1 = User.objects.create_user(
            email="t1@test.app", password="password123", full_name="Teacher One",
            role=UserRole.TEACHER
        )
        self.teacher2 = User.objects.create_user(
            email="t2@test.app", password="password123", full_name="Teacher Two",
            role=UserRole.TEACHER
        )
        self.student1 = User.objects.create_user(
            email="s1@test.app", password="password123", full_name="Student One",
            role=UserRole.STUDENT
        )
        self.student2 = User.objects.create_user(
            email="s2@test.app", password="password123", full_name="Student Two",
            role=UserRole.STUDENT
        )
        self.suspended_student = User.objects.create_user(
            email="suspended@test.app", password="password123", full_name="Suspended Student",
            role=UserRole.STUDENT, status=UserStatus.SUSPENDED
        )

        # Create modules and exercises
        self.module = LearningModule.objects.create(
            title="Module 1", created_by=self.teacher1
        )
        self.exercise_writing = Exercise.objects.create(
            module=self.module, title="Writing Ex",
            exercise_type=ExerciseType.WRITING, prompt_text="Write an essay.",
            created_by=self.teacher1
        )
        self.exercise_speaking = Exercise.objects.create(
            module=self.module, title="Speaking Ex",
            exercise_type=ExerciseType.SPEAKING, prompt_text="Speak aloud.",
            created_by=self.teacher1
        )

        # Create class and enroll
        self.klass = Class.objects.create(
            class_name="Class 1", teacher=self.teacher1, academic_year="2026"
        )
        ClassStudent.objects.create(klass=self.klass, student=self.student1)

        # Create assignment
        self.assignment = Assignment.objects.create(
            klass=self.klass, exercise=self.exercise_writing,
            assigned_by=self.teacher1, due_date=timezone.now() + timezone.timedelta(days=1)
        )

        # Create submissions
        self.submission1 = Submission.objects.create(
            assignment=self.assignment, exercise=self.exercise_writing,
            student=self.student1, submission_type=SubmissionType.WRITING,
            writing_text="This is my writing submission text."
        )
        self.submission2 = Submission.objects.create(
            assignment=self.assignment, exercise=self.exercise_writing,
            student=self.student2, submission_type=SubmissionType.WRITING,
            writing_text="This is another student's writing."
        )

        # Create AI Model configuration
        self.ai_model = AiModel.objects.create(
            model_name="GPT-4 Test Evaluator",
            endpoint_url="https://api.openai.com/v1/chat/completions",
            version_identifier="gpt-4-test",
            is_active=True,
            updated_by=self.admin
        )

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        token = resp.data["access_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # ==================== Task 01 & 02: Registration & Role Signup ====================
    def test_registration_restrictions(self):
        # Admin registration should fail
        resp = self.client.post(reverse("register"), {
            "email": "hacker@test.app", "password": "password123",
            "full_name": "Hacker", "role": UserRole.ADMIN
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Student registration should succeed
        resp = self.client.post(reverse("register"), {
            "email": "newstudent@test.app", "password": "password123",
            "full_name": "New Student", "role": UserRole.STUDENT
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["role"], UserRole.STUDENT)

    # ==================== Task 02: Class Teacher Lock ====================
    def test_class_teacher_lock(self):
        self._login(self.teacher1)
        url = reverse("class-detail", args=[self.klass.id])

        # Teacher tries to change teacher_id to teacher2
        resp = self.client.patch(url, {"teacher_id": self.teacher2.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.klass.refresh_from_db()
        # Should NOT change teacher
        self.assertEqual(self.klass.teacher_id, self.teacher1.id)

        # Admin changes teacher_id
        self._login(self.admin)
        resp = self.client.patch(url, {"teacher_id": self.teacher2.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.klass.refresh_from_db()
        # Should change teacher
        self.assertEqual(self.klass.teacher_id, self.teacher2.id)

    # ==================== Task 02: Feedback Leak Prevention ====================
    def test_feedback_read_permissions(self):
        feedback = Feedback.objects.create(
            submission=self.submission1, reviewer=self.teacher1,
            score=Decimal("85.00"), comments="Good job."
        )

        url = reverse("submission-feedback", args=[self.submission1.id])

        # Owner student reads own feedback -> OK
        self._login(self.student1)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

        # Other student reads -> 403
        self._login(self.student2)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Class teacher reads -> OK
        self._login(self.teacher1)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Unrelated teacher reads -> 403
        self._login(self.teacher2)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Admin reads -> OK
        self._login(self.admin)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ==================== Task 03: Study Materials Library ====================
    def test_study_materials_access(self):
        doc = StudyMaterial.objects.create(
            title="Grammar Guide", file_url="https://test.cdn/grammar.pdf",
            uploaded_by=self.admin
        )

        # Student lists and retrieves -> OK
        self._login(self.student1)
        resp = self.client.get(reverse("study-material-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

        resp = self.client.get(reverse("study-material-detail", args=[doc.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Student attempts to write -> 403
        resp = self.client.post(reverse("study-material-list"), {
            "title": "Hack Doc", "file_url": "http://hack"
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Admin creates study material -> OK
        self._login(self.admin)
        resp = self.client.post(reverse("study-material-list"), {
            "title": "Official Doc", "file_url": "https://test.cdn/official.pdf",
            "class_id": self.klass.id
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["class_id"], self.klass.id)

    # ==================== Task 04: Grading & Inbox Action ====================
    def test_grading_transitions_status(self):
        # Initial status should be pending
        self.assertEqual(self.submission1.status, SubmissionStatus.PENDING)

        self._login(self.teacher1)
        # Post feedback
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission1.id,
            "score": "95.00",
            "comments": "Brilliant writing style."
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.submission1.refresh_from_db()
        # Status should transition to GRADED
        self.assertEqual(self.submission1.status, SubmissionStatus.GRADED)

    def test_teacher_inbox(self):
        # Teacher inbox should include submissions of teacher's exercises
        self._login(self.teacher1)
        resp = self.client.get(reverse("submission-inbox"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Should find both submissions
        self.assertEqual(len(resp.data), 2)
        # Verify custom fields
        row = resp.data[0]
        self.assertIn("is_graded", row)
        self.assertIn("grading_source", row)
        self.assertIn("latest_score", row)
        self.assertIn("student_name", row)

        # Unrelated teacher inbox -> empty (no exercises created_by self.teacher2)
        self._login(self.teacher2)
        resp = self.client.get(reverse("submission-inbox"))
        self.assertEqual(len(resp.data), 0)

    # ==================== Task 05: Dashboards & Reports ====================
    def test_dashboards(self):
        # Student dashboard
        self._login(self.student1)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["role"], UserRole.STUDENT)
        self.assertIn("due_assignments", resp.data["data"])

        # Teacher dashboard
        self._login(self.teacher1)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["role"], UserRole.TEACHER)
        self.assertIn("class_count", resp.data["data"])

        # Admin dashboard
        self._login(self.admin)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["role"], UserRole.ADMIN)
        self.assertIn("user_counts_by_role", resp.data["data"])

        # Suspended user dashboard -> 403
        self._login(self.suspended_student)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_grade_report_export(self):
        self._login(self.teacher1)
        url = reverse("report-grades") + f"?class_id={self.klass.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn(f'attachment; filename="grades_class_{self.klass.id}.csv"', resp["Content-Disposition"])

    # ==================== Task 06: AI Evaluation Suite ====================
    def test_ai_practice_and_evaluate(self):
        # Autonomous AI Practice (Student)
        self._login(self.student1)
        resp = self.client.post(reverse("submission-ai-practice"), {
            "exercise_id": self.exercise_speaking.id,
            "audio_recording_url": "https://test.cdn/recordings/s1-speaking.m4a"
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("submission", resp.data)
        self.assertIn("feedback", resp.data)
        # Check generated feedback is marked as AI
        self.assertTrue(resp.data["feedback"]["is_ai_generated"])
        self.assertIsNone(resp.data["feedback"]["reviewer_id"])

        # AI Evaluate Action (Teacher)
        self._login(self.teacher1)
        resp = self.client.post(reverse("submission-ai-evaluate", args=[self.submission1.id]))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["is_ai_generated"])
        self.assertIsNone(resp.data["reviewer_id"])

    # ==================== Task 07: Admin Audit & Operations ====================
    def test_admin_operations_and_audit(self):
        # Admin sets user status to suspended -> audits
        self._login(self.admin)
        url = reverse("user-detail", args=[self.student2.id])
        resp = self.client.patch(url, {"status": UserStatus.SUSPENDED})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.student2.refresh_from_db()
        self.assertEqual(self.student2.status, UserStatus.SUSPENDED)

        # Check system log entry exists
        logs = SystemLog.objects.filter(target_status=UserStatus.SUSPENDED)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().admin, self.admin)

        # Admin activity feed -> OK
        resp = self.client.get(reverse("admin-activity"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(len(resp.data) > 0)

        # Admin health check -> OK
        resp = self.client.get(reverse("admin-health"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "ok")

    # ==================== Task 08: WebSockets integration ====================
    def test_speaking_websocket_flow(self):
        async def run():
            token = str(RefreshToken.for_user(self.student1).access_token)
            communicator = WebsocketCommunicator(
                application, f"ws/speaking/{self.exercise_speaking.id}/?token={token}"
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # 1. Server ready
            msg = await communicator.receive_json_from()
            self.assertEqual(msg["type"], "ready")

            # 2. Send chunk -> get ack
            await communicator.send_json_to({"type": "audio_chunk", "data": "BASE64"})
            msg = await communicator.receive_json_from()
            self.assertEqual(msg["type"], "ack")
            self.assertEqual(msg["chunk"], 1)

            # 3. Send end -> get result
            await communicator.send_json_to({"type": "end", "audio_recording_url": "https://test.cdn/speaking.mp3"})
            msg = await communicator.receive_json_from()
            self.assertEqual(msg["type"], "result")
            self.assertIn("score", msg)
            self.assertIn("comments", msg)

            await communicator.disconnect()

        asyncio.run(run())

    def test_websocket_auth_rejection(self):
        async def run():
            # Missing token -> rejects with 4401
            communicator = WebsocketCommunicator(
                application, f"ws/speaking/{self.exercise_speaking.id}/"
            )
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)

            # Suspended user -> rejects with 4401
            token = str(RefreshToken.for_user(self.suspended_student).access_token)
            communicator = WebsocketCommunicator(
                application, f"ws/speaking/{self.exercise_speaking.id}/?token={token}"
            )
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)

        asyncio.run(run())

    def test_notification_broadcast(self):
        from core.views import _broadcast_class

        async def run():
            token = str(RefreshToken.for_user(self.student1).access_token)
            communicator = WebsocketCommunicator(
                application, f"ws/notifications/?token={token}"
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Trigger a broadcast class notification
            payload = {"event": "test_broadcast", "data": 123}
            from channels.layers import get_channel_layer
            layer = get_channel_layer()
            await layer.group_send(
                f"class_{self.klass.id}", {"type": "notify", "payload": payload}
            )

            # Receive the notification
            msg = await communicator.receive_json_from()
            self.assertEqual(msg, payload)

            await communicator.disconnect()

        asyncio.run(run())

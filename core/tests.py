import asyncio
import json
import tempfile
from decimal import Decimal
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITransactionTestCase
# pyrefly: ignore [missing-import]
from rest_framework_simplejwt.tokens import RefreshToken

from channels.testing import WebsocketCommunicator
from config.asgi import application

from core import mock_tests
from core import serializers as s_mod
from core.models import (
    AiInsight,
    AiModel,
    AnnotationCategory,
    Assignment,
    AttemptMode,
    AttemptStatus,
    BandLevel,
    Class,
    ClassStudent,
    CriterionScore,
    DrillType,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    Meeting,
    MeetingStatus,
    MockTestTemplate,
    OverallStrategy,
    PronunciationAttempt,
    PronunciationDrill,
    Question,
    QuestionOption,
    RubricAggregation,
    RubricBandDescriptor,
    RubricCriterion,
    RubricTemplate,
    ScoreConversionTable,
    SectionSkill,
    SectionStatus,
    Submission,
    SubmissionType,
    SubmissionStatus,
    StudyMaterial,
    SystemLog,
    TestAttempt,
    TestFormat,
    TestSection,
    TestSectionExercise,
    Topic,
    User,
    UserRole,
    UserStatus,
    WritingAnnotation,
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


class MockTestTests(APITransactionTestCase):
    """Mock-test engine: state machine, server-authoritative timing, scoring.

    Timing edge cases first (expiry, double-start, out-of-order start, resume),
    per the research doc's Phase 2 checklist.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="mt-teacher@test.app", password="password123",
            full_name="Mock Teacher", role=UserRole.TEACHER,
        )
        self.student = User.objects.create_user(
            email="mt-student@test.app", password="password123",
            full_name="Mock Student", role=UserRole.STUDENT,
        )
        self.other_student = User.objects.create_user(
            email="mt-other@test.app", password="password123",
            full_name="Other Student", role=UserRole.STUDENT,
        )
        # Admins curate the mock-test library (the StudyMaterial pattern).
        self.admin = User.objects.create_user(
            email="mt-admin@test.app", password="password123",
            full_name="Mock Admin", role=UserRole.ADMIN,
        )

        self.format = TestFormat.objects.create(
            slug="ielts_academic", name="IELTS Academic", version="2026",
            overall_strategy=OverallStrategy.BAND_AVERAGE, score_precision="0.5",
        )
        # Sparse table: 2 correct -> band 5.0, 1 -> 4.0, 0 -> 0.0. The nearest
        # lower key fallback is what makes sparse tables usable.
        ScoreConversionTable.objects.create(
            format=self.format, skill=SectionSkill.READING,
            mapping={"0": "0.0", "1": "4.0", "2": "5.0"},
            source_note="test fixture",
        )
        ScoreConversionTable.objects.create(
            format=self.format, skill=SectionSkill.WRITING,
            mapping={"0": "0.0", "50": "6.0", "80": "8.0"},
            source_note="test fixture",
        )

        module = LearningModule.objects.create(title="Mock module", created_by=self.teacher)
        self.reading_ex = Exercise.objects.create(
            module=module, title="Passage 1", exercise_type=ExerciseType.READING,
            prompt_text="Read the passage.", content_text="A passage.",
            created_by=self.teacher,
        )
        # Q1 multiple choice (key = option "B"), Q2 fill-blank (key = "paris").
        self.q1 = Question.objects.create(exercise=self.reading_ex, text="Pick one", order=0)
        self.q1_wrong = QuestionOption.objects.create(question=self.q1, text="A", order=0)
        self.q1_right = QuestionOption.objects.create(
            question=self.q1, text="B", is_correct=True, order=1,
        )
        self.q2 = Question.objects.create(
            exercise=self.reading_ex, text="The capital is [[Paris]].", order=1,
        )
        self.writing_ex = Exercise.objects.create(
            module=module, title="Task 1", exercise_type=ExerciseType.WRITING,
            prompt_text="Write 150 words.", created_by=self.teacher,
        )

        self.template = MockTestTemplate.objects.create(
            format=self.format, title="IELTS Practice A",
            created_by=self.admin,
        )
        self.reading_section = TestSection.objects.create(
            template=self.template, skill=SectionSkill.READING, title="Reading",
            order=0, duration_minutes=60,
        )
        TestSectionExercise.objects.create(
            section=self.reading_section, exercise=self.reading_ex, order=0,
        )
        self.writing_section = TestSection.objects.create(
            template=self.template, skill=SectionSkill.WRITING, title="Writing",
            order=1, duration_minutes=60,
        )
        TestSectionExercise.objects.create(
            section=self.writing_section, exercise=self.writing_ex, order=0,
        )

    def _login(self, user):
        resp = self.client.post(
            reverse("login"), {"email": user.email, "password": "password123"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _attempt(self):
        return mock_tests.start_attempt(self.template, self.student)

    def _section(self, attempt, order):
        return attempt.sections.get(section__order=order)

    # A perfect answer set in the web client's envelope shape.
    def _full_marks_draft(self):
        return {
            "answers": {
                str(self.reading_ex.id): {
                    "version": 1,
                    "responses": [
                        {"question_id": self.q1.id, "type": "mcq",
                         "option_id": self.q1_right.id},
                        {"question_id": self.q2.id, "type": "fill_blank",
                         "text": '["paris"]'},
                    ],
                },
            },
            "writing": {},
            "meta": {"audio_played": []},
        }

    # ---------------- grading shapes ----------------
    def test_grade_detail_accepts_both_answer_shapes(self):
        envelope = Submission.objects.create(
            exercise=self.reading_ex, student=self.student,
            submission_type=SubmissionType.READING,
            answers=self._full_marks_draft()["answers"][str(self.reading_ex.id)],
        )
        self.assertEqual(envelope.grade_detail(), {"correct": 2, "total": 2})
        self.assertEqual(envelope.grade(), Decimal("100.00"))

        legacy = Submission.objects.create(
            exercise=self.reading_ex, student=self.student,
            submission_type=SubmissionType.READING,
            answers={str(self.q1.id): [self.q1_right.id]},
        )
        # Q1 right, Q2 (fill-blank) unanswered -> 1 of 2.
        self.assertEqual(legacy.grade_detail(), {"correct": 1, "total": 2})

    def test_short_answer_questions_are_not_auto_gradable(self):
        exercise = Exercise.objects.create(
            title="Open", exercise_type=ExerciseType.QUIZ,
            prompt_text="Explain.", created_by=self.teacher,
        )
        Question.objects.create(exercise=exercise, text="Why?", order=0)
        sub = Submission.objects.create(
            exercise=exercise, student=self.student,
            submission_type=SubmissionType.QUIZ, answers={},
        )
        self.assertEqual(sub.grade_detail(), {"correct": 0, "total": 0})
        self.assertIsNone(sub.grade())

    # ---------------- state machine ----------------
    def test_sections_must_be_taken_in_order(self):
        attempt = self._attempt()
        writing = self._section(attempt, 1)
        with self.assertRaises(mock_tests.MockTestError):
            mock_tests.start_section(attempt, writing.id)

    def test_practice_mode_starts_with_any_section(self):
        """A student with an hour for Writing should not have to sit Listening
        first. Practice mode drops the ordering rule and nothing else."""
        attempt = mock_tests.start_attempt(
            self.template, self.other_student, mode=AttemptMode.PRACTICE,
        )
        writing = attempt.sections.get(section=self.writing_section)
        started = mock_tests.start_section(attempt, writing.id)
        self.assertEqual(started.status, "in_progress")
        self.assertIsNotNone(started.expires_at)

    def test_practice_mode_still_runs_one_section_at_a_time(self):
        """Free order is about what to sit next, not sitting several at once —
        two live clocks would be two timers nobody can attend to."""
        attempt = mock_tests.start_attempt(
            self.template, self.other_student, mode=AttemptMode.PRACTICE,
        )
        mock_tests.start_section(
            attempt, attempt.sections.get(section=self.writing_section).id
        )
        with self.assertRaises(mock_tests.MockTestError):
            mock_tests.start_section(
                attempt, attempt.sections.get(section=self.reading_section).id
            )

    def test_resuming_never_changes_the_mode(self):
        """An unfinished exam sitting must not become free-order because the
        student re-entered through a different button."""
        first = mock_tests.start_attempt(
            self.template, self.other_student, mode=AttemptMode.EXAM,
        )
        second = mock_tests.start_attempt(
            self.template, self.other_student, mode=AttemptMode.PRACTICE,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.mode, AttemptMode.EXAM)
        with self.assertRaises(mock_tests.MockTestError):
            mock_tests.start_section(
                second, second.sections.get(section=self.writing_section).id
            )

    def test_attempt_create_endpoint_accepts_mode(self):
        self._login(self.other_student)
        resp = self.client.post(
            reverse("mock-test-template-attempts", args=[self.template.id]),
            {"mode": "practice"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["mode"], "practice")

    def test_attempt_create_defaults_to_exam_mode(self):
        """An empty body is the real sitting, not the relaxed one — the strict
        rule must be what you get by saying nothing."""
        self._login(self.other_student)
        resp = self.client.post(
            reverse("mock-test-template-attempts", args=[self.template.id]),
            {}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["mode"], "exam")

    def test_only_one_section_runs_at_a_time(self):
        attempt = self._attempt()
        mock_tests.start_section(attempt, self._section(attempt, 0).id)
        with self.assertRaises(mock_tests.MockTestError):
            mock_tests.start_section(attempt, self._section(attempt, 1).id)

    def test_double_start_is_idempotent(self):
        attempt = self._attempt()
        first = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        second = mock_tests.start_section(attempt, first.id)
        self.assertEqual(first.expires_at, second.expires_at)

    def test_start_attempt_resumes_instead_of_duplicating(self):
        first = self._attempt()
        second = mock_tests.start_attempt(self.template, self.student)
        self.assertEqual(first.id, second.id)
        self.assertEqual(TestAttempt.objects.filter(student=self.student).count(), 1)

    def test_expires_at_includes_duration_plus_grace(self):
        attempt = self._attempt()
        section = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        expected = section.started_at + timezone.timedelta(minutes=60) + mock_tests.GRACE
        self.assertEqual(section.expires_at, expected)

    # ---------------- timing ----------------
    def test_autosave_rejected_after_expiry(self):
        attempt = self._attempt()
        section = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        section.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        section.save(update_fields=["expires_at"])
        with self.assertRaises(mock_tests.SectionExpired):
            mock_tests.autosave(attempt, section.id, self._full_marks_draft())

    def test_late_submit_grades_the_last_accepted_draft(self):
        attempt = self._attempt()
        section = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        mock_tests.autosave(attempt, section.id, self._full_marks_draft())
        section.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        section.save(update_fields=["expires_at"])

        # A post-expiry submit body must not overwrite the honest draft.
        cheating = {"answers": {}, "writing": {}, "meta": {"audio_played": []}}
        section = mock_tests.submit_section(attempt, section.id, draft=cheating)
        self.assertEqual(section.raw_score, 2)

    def test_sweeper_finalizes_only_long_expired_sections(self):
        attempt = self._attempt()
        section = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        section.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        section.save(update_fields=["expires_at"])
        self.assertEqual(mock_tests.expire_stale_sections(), 0)

        section.expires_at = timezone.now() - mock_tests.SWEEP_AFTER - timezone.timedelta(minutes=1)
        section.save(update_fields=["expires_at"])
        self.assertEqual(mock_tests.expire_stale_sections(), 1)
        section.refresh_from_db()
        self.assertEqual(section.status, SectionStatus.COMPLETED)

    # ---------------- scoring ----------------
    def test_receptive_submit_scores_and_converts(self):
        attempt = self._attempt()
        section = self._section(attempt, 0)
        mock_tests.start_section(attempt, section.id)
        section = mock_tests.submit_section(
            attempt, section.id, draft=self._full_marks_draft()
        )
        self.assertEqual((section.raw_score, section.raw_max), (2, 2))
        self.assertEqual(section.converted_score, Decimal("5.00"))
        # One ordinary Submission per exercise, linked back to the section.
        self.assertEqual(section.submissions.count(), 1)

    def test_short_sections_convert_on_proportion_not_raw_count(self):
        """Tables are calibrated for a 40-question IELTS section. A 10-question
        practice section must score on proportion, or 8/10 would be read as
        "8 of 40" and come back near the bottom of the scale."""
        ScoreConversionTable.objects.update_or_create(
            format=self.format, skill=SectionSkill.LISTENING,
            defaults={"mapping": {"0": "0.0", "20": "5.0", "32": "7.0", "40": "9.0"},
                      "source_note": "test fixture"},
        )
        self.assertEqual(
            mock_tests.convert_score(
                self.format, SectionSkill.LISTENING, 8, raw_max=10,
            ),
            Decimal("7.0"),                     # 80% -> 32/40 -> band 7.0
        )
        # Full-length sections are untouched by the rescale.
        self.assertEqual(
            mock_tests.convert_score(
                self.format, SectionSkill.LISTENING, 32, raw_max=40,
            ),
            Decimal("7.0"),
        )

    def test_conversion_falls_back_to_nearest_lower_key(self):
        # Table has no "3"; the highest key <= 3 is "2" -> band 5.0.
        self.assertEqual(
            mock_tests.convert_score(self.format, SectionSkill.READING, 3),
            Decimal("5.0"),
        )
        self.assertIsNone(
            mock_tests.convert_score(self.format, SectionSkill.LISTENING, 3)
        )

    # Automatic AI marking normally grades Writing at submit; switch it off to
    # exercise the teacher-grading path that the report also has to survive.
    @override_settings(MOCK_TEST_AI_AUTOGRADE=False)
    def test_overall_waits_for_productive_grading(self):
        attempt = self._attempt()
        reading = self._section(attempt, 0)
        mock_tests.start_section(attempt, reading.id)
        mock_tests.submit_section(attempt, reading.id, draft=self._full_marks_draft())

        writing = self._section(attempt, 1)
        mock_tests.start_section(attempt, writing.id)
        writing = mock_tests.submit_section(attempt, writing.id, draft={
            "answers": {}, "writing": {str(self.writing_ex.id): "An essay."},
            "meta": {"audio_played": []},
        })

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, AttemptStatus.COMPLETED)
        self.assertIsNone(attempt.overall_score)      # writing not graded yet
        report = mock_tests.build_report(attempt)
        self.assertTrue(report["partial"])

        # Teacher grades the writing task -> band conversion + overall.
        submission = writing.submissions.first().submission
        feedback = Feedback.objects.create(
            submission=submission, reviewer=self.teacher, score=Decimal("80.00"),
        )
        mock_tests.on_feedback_created(feedback)

        writing.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(writing.converted_score, Decimal("8.00"))
        # mean(5.0, 8.0) = 6.5 -> already a half band.
        self.assertEqual(attempt.overall_score, Decimal("6.50"))
        self.assertFalse(mock_tests.build_report(attempt)["partial"])

    def test_band_average_rounds_to_nearest_half_band(self):
        attempt = self._attempt()
        sections = list(attempt.sections.all())
        for section_attempt, score in zip(sections, [Decimal("6.0"), Decimal("6.5")]):
            section_attempt.converted_score = score
            section_attempt.status = SectionStatus.COMPLETED
            section_attempt.save()
        # mean 6.25 -> IELTS rounds a quarter band up to 6.5.
        self.assertEqual(mock_tests.recompute_overall(attempt), Decimal("6.50"))

    def test_scaled_sum_strategy_adds_section_scores(self):
        self.format.overall_strategy = OverallStrategy.SCALED_SUM
        self.format.save(update_fields=["overall_strategy"])
        attempt = self._attempt()
        for section_attempt, score in zip(
            attempt.sections.all(), [Decimal("450"), Decimal("400")]
        ):
            section_attempt.converted_score = score
            section_attempt.save(update_fields=["converted_score"])
        self.assertEqual(mock_tests.recompute_overall(attempt), Decimal("850.00"))

    # ---------------- API surface ----------------
    def test_runner_payload_never_leaks_the_answer_key(self):
        self._login(self.student)
        attempt = self._attempt()
        mock_tests.start_section(attempt, self._section(attempt, 0).id)

        url = reverse("mock-test-attempt-detail", args=[attempt.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = str(resp.data)
        self.assertNotIn("is_correct", body)
        self.assertNotIn("Paris", body)          # fill-blank key masked
        self.assertIn("[[]]", body)              # ...but the marker survives

    def test_not_started_section_withholds_its_exercises(self):
        self._login(self.student)
        attempt = self._attempt()
        resp = self.client.get(reverse("mock-test-attempt-detail", args=[attempt.id]))
        self.assertEqual(resp.data["sections"][0]["exercises"], [])

    def test_autosave_endpoint_returns_409_when_expired(self):
        self._login(self.student)
        attempt = self._attempt()
        section = mock_tests.start_section(attempt, self._section(attempt, 0).id)
        section.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        section.save(update_fields=["expires_at"])

        resp = self.client.patch(
            f"/api/mock-tests/attempts/{attempt.id}/sections/{section.id}/answers/",
            self._full_marks_draft(), format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data["code"], "section_expired")

    def test_another_student_cannot_read_or_write_an_attempt(self):
        attempt = self._attempt()
        self._login(self.other_student)
        detail = reverse("mock-test-attempt-detail", args=[attempt.id])
        self.assertEqual(self.client.get(detail).status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.post(
            f"/api/mock-tests/attempts/{attempt.id}"
            f"/sections/{self._section(attempt, 0).id}/start/"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_every_user_sees_the_whole_library(self):
        """Mock tests are app content, not classroom content: there is no
        publish gate and no per-teacher scope to hide rows behind."""
        MockTestTemplate.objects.create(
            format=self.format, title="TOEIC Practice B", created_by=self.admin,
        )
        for user in (self.student, self.teacher, self.admin):
            self._login(user)
            resp = self.client.get(reverse("mock-test-template-list"))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            titles = [row["title"] for row in resp.data]
            self.assertIn("IELTS Practice A", titles)
            self.assertIn("TOEIC Practice B", titles)
            self.assertNotIn("is_published", resp.data[0])

    def test_only_admins_curate_the_library(self):
        payload = {
            "format_id": self.format.id, "title": "New test",
            "sections": [{
                "skill": SectionSkill.READING, "title": "Reading",
                "duration_minutes": 60,
            }],
        }
        url = reverse("mock-test-template-list")

        for user in (self.student, self.teacher):
            self._login(user)
            resp = self.client.post(url, payload, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self._login(self.admin)
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_any_student_can_start_any_library_test(self):
        self._login(self.other_student)
        resp = self.client.post(
            reverse("mock-test-template-attempts", args=[self.template.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_template_with_attempts_cannot_be_edited_or_deleted(self):
        """PROTECT must surface as a 400, not a 500: an admin editing a test
        someone has already sat should get a usable message."""
        self._attempt()
        self._login(self.admin)
        detail = reverse("mock-test-template-detail", args=[self.template.id])

        resp = self.client.patch(
            detail, {"sections": []}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["code"], "template_in_use")

        resp = self.client.delete(detail)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["code"], "template_in_use")

    @staticmethod
    def _correct_answers(section_attempt, only_exercise_id=None):
        """Every question in a section answered correctly, in the web client's
        envelope shape. Fill-blank keys may carry `|` alternates, so the first
        accepted spelling is submitted."""
        from core.models import blank_answer_key

        answers = {}
        for item in section_attempt.section.items.select_related("exercise").all():
            if only_exercise_id and item.exercise_id != only_exercise_id:
                continue
            responses = []
            for question in item.exercise.questions.prefetch_related("options"):
                key = [o for o in question.options.all() if o.is_correct]
                if key:
                    responses.append({
                        "question_id": question.id, "type": "mcq",
                        "option_id": key[0].id,
                    })
                else:  # fill-blank: the key lives in the unmasked question text
                    first_alternate = [
                        alternates[0] for alternates in blank_answer_key(question.text)
                    ]
                    responses.append({
                        "question_id": question.id, "type": "fill_blank",
                        "text": json.dumps(first_alternate),
                    })
            answers[str(item.exercise.id)] = {"version": 1, "responses": responses}
        return answers

    def test_seeded_library_is_takeable(self):
        """The shipped library must be sittable end to end: seed it, sit the
        full-length Reading section as a student, and get band 9 for 40/40."""
        from django.core.management import call_command

        call_command("seed_mock_tests", verbosity=0)
        template = MockTestTemplate.objects.get(
            title="IELTS Academic — Full Practice Test 1"
        )
        attempt = mock_tests.start_attempt(template, self.student)
        reading = attempt.sections.get(section__skill=SectionSkill.READING)

        # Reading is section 2; Listening must be finished first.
        listening = attempt.sections.get(section__skill=SectionSkill.LISTENING)
        mock_tests.start_section(attempt, listening.id)
        mock_tests.submit_section(attempt, listening.id)
        mock_tests.start_section(attempt, reading.id)

        reading = mock_tests.submit_section(attempt, reading.id, draft={
            "answers": self._correct_answers(reading),
            "writing": {}, "meta": {"audio_played": []},
        })

        # Three passages, 13 + 13 + 14 questions — the real paper's length.
        self.assertEqual(reading.raw_max, 40)
        self.assertEqual(reading.raw_score, 40)
        self.assertEqual(reading.converted_score, Decimal("9.00"))

    def test_seeded_listening_parts_are_gated(self):
        """The seeded Listening section plays in order: part 2 cannot be
        answered until part 1 has been closed."""
        from django.core.management import call_command

        call_command("seed_mock_tests", verbosity=0)
        template = MockTestTemplate.objects.get(
            title="IELTS Academic — Full Practice Test 1"
        )
        attempt = mock_tests.start_attempt(template, self.student)
        listening = attempt.sections.get(section__skill=SectionSkill.LISTENING)
        mock_tests.start_section(attempt, listening.id)

        items = list(listening.section.items.all())
        self.assertEqual(len(items), 4)
        first, second = items[0], items[1]

        # Only part 1 is open.
        self.assertEqual(
            mock_tests.open_item_ids(listening.section, listening.draft_answers),
            [first.exercise_id],
        )

        # Writing to part 2 is refused, not merely hidden.
        with self.assertRaises(mock_tests.MockTestError) as caught:
            mock_tests.autosave(attempt, listening.id, {
                "answers": self._correct_answers(
                    listening, only_exercise_id=second.exercise_id
                ),
                "writing": {}, "meta": {},
            })
        self.assertEqual(caught.exception.code, "item_locked")

        # Answer part 1, close it, and part 2 opens.
        mock_tests.autosave(attempt, listening.id, {
            "answers": self._correct_answers(
                listening, only_exercise_id=first.exercise_id
            ),
            "writing": {}, "meta": {},
        })
        listening = mock_tests.advance_item(attempt, listening.id, first.exercise_id)
        self.assertEqual(
            mock_tests.open_item_ids(listening.section, listening.draft_answers),
            [second.exercise_id],
        )
        # Advancing twice is a no-op, so a double tap cannot skip a recording.
        listening = mock_tests.advance_item(attempt, listening.id, first.exercise_id)
        self.assertEqual(
            listening.draft_answers["meta"]["completed_items"], [first.exercise_id],
        )
        # Part 1's answers survive being closed.
        self.assertIn(str(first.exercise_id), listening.draft_answers["answers"])

    def test_free_flow_sections_open_every_part(self):
        """Reading and Writing hand the student everything at once — that is
        the real paper, and gating them would be a rule the exam does not have."""
        attempt = self._attempt()
        section = attempt.sections.get(section=self.reading_section)
        mock_tests.start_section(attempt, section.id)
        self.assertEqual(
            mock_tests.open_item_ids(section.section, section.draft_answers),
            [self.reading_ex.id],
        )
        with self.assertRaises(mock_tests.MockTestError):
            mock_tests.advance_item(attempt, section.id, self.reading_ex.id)

    def test_writing_task_two_counts_double(self):
        """IELTS Writing is (Task 1 + 2 x Task 2) / 3, not a plain mean."""
        task_two = Exercise.objects.create(
            title="Task 2", exercise_type=ExerciseType.WRITING,
            prompt_text="Write 250 words.", created_by=self.teacher,
        )
        TestSectionExercise.objects.create(
            section=self.writing_section, exercise=task_two, order=1, weight=2,
        )
        attempt = self._attempt()
        reading = attempt.sections.get(section=self.reading_section)
        writing = attempt.sections.get(section=self.writing_section)
        mock_tests.start_section(attempt, reading.id)
        mock_tests.submit_section(attempt, reading.id)
        mock_tests.start_section(attempt, writing.id)
        mock_tests.submit_section(attempt, writing.id, draft={
            "answers": {},
            "writing": {
                str(self.writing_ex.id): "Task one response.",
                str(task_two.id): "Task two response.",
            },
            "meta": {"audio_played": []},
        })

        by_exercise = {
            link.submission.exercise_id: link.submission
            for link in writing.submissions.select_related("submission")
        }
        # Task 1 scores 50, Task 2 scores 80. The weighted mean is
        # (50 + 2*80) / 3 = 70; an unweighted mean would be 65.
        Feedback.objects.create(
            submission=by_exercise[self.writing_ex.id], score=50,
            is_ai_generated=True,
        )
        feedback_two = Feedback.objects.create(
            submission=by_exercise[task_two.id], score=80, is_ai_generated=True,
        )
        ScoreConversionTable.objects.filter(
            format=self.format, skill=SectionSkill.WRITING
        ).update(mapping={"0": "0.0", "65": "6.5", "70": "7.0", "80": "8.0"})
        mock_tests.on_feedback_created(feedback_two)

        writing.refresh_from_db()
        self.assertEqual(writing.converted_score, Decimal("7.00"))

    def test_custom_format_scores_as_a_percentage(self):
        """A custom test reports the percentage it actually is — no borrowed
        band scale."""
        from django.core.management import call_command

        call_command("seed_test_formats", verbosity=0)
        custom = TestFormat.objects.get(slug="custom")
        self.assertEqual(custom.overall_strategy, OverallStrategy.MEAN_PERCENT)
        # 1 of 2 correct is 50%, whatever the section's length.
        self.assertEqual(
            mock_tests.convert_score(custom, SectionSkill.READING, 1, raw_max=2),
            Decimal("50"),
        )

    def test_retired_format_leaves_the_catalogue(self):
        """Deactivating a format hides it and its templates from students, but
        an admin still sees both — retiring is not deleting."""
        from django.core.management import call_command

        call_command("seed_test_formats", verbosity=0)
        toeic = TestFormat.objects.get(slug="toeic_lr")
        self.assertFalse(toeic.is_active)

        self._login(self.student)
        resp = self.client.get(reverse("mock-test-format-list"))
        slugs = {row["slug"] for row in resp.data}
        self.assertIn("ielts_academic", slugs)
        self.assertIn("custom", slugs)
        self.assertNotIn("toeic_lr", slugs)

        self._login(self.admin)
        resp = self.client.get(reverse("mock-test-format-list"))
        self.assertIn("toeic_lr", {row["slug"] for row in resp.data})

    def test_answer_key_tolerates_variants_and_enforces_word_limit(self):
        """A 40-question section must not lose marks to a trailing full stop, a
        British spelling, or a leading article — but the exam's word limit must
        still bite."""
        from core.models import _blanks_match, blank_answer_key

        keys = blank_answer_key("It was [[colour|color]].")
        self.assertTrue(_blanks_match(keys, json.dumps(["Color"])))
        self.assertTrue(_blanks_match(keys, json.dumps([" colour. "])))
        self.assertFalse(_blanks_match(keys, json.dumps(["colours"])))

        article = blank_answer_key("Go to [[the museum]].")
        self.assertTrue(_blanks_match(article, json.dumps(["museum"])))

        limited = blank_answer_key("Storage for a [[bicycle]].")
        self.assertTrue(_blanks_match(limited, json.dumps(["bicycle"]), max_words=1))
        self.assertFalse(
            _blanks_match(limited, json.dumps(["a bicycle shed"]), max_words=1)
        )

    def test_admin_can_duplicate_a_template_that_has_attempts(self):
        """Editing an attempted test is refused; duplicating it is the way
        forward, and the copy carries the section structure."""
        self._attempt()
        self._login(self.admin)
        resp = self.client.post(
            reverse("mock-test-template-duplicate", args=[self.template.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["title"], "IELTS Practice A (copy)")
        self.assertEqual(len(resp.data["sections"]), 2)
        # The copy has no attempts, so it is editable.
        patch = self.client.patch(
            reverse("mock-test-template-detail", args=[resp.data["id"]]),
            {"title": "Revised"}, format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)

    def test_export_import_round_trip(self):
        """An exported document imports into a working test — the bulk path."""
        self._login(self.admin)
        resp = self.client.get(
            reverse("mock-test-template-export", args=[self.template.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        document = dict(resp.data)
        self.assertEqual(document["format_slug"], "ielts_academic")

        document["title"] = "Imported paper"
        created = self.client.post(
            reverse("mock-test-template-import"), [document], format="json"
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(created.data), 1)

        imported = MockTestTemplate.objects.get(title="Imported paper")
        self.assertEqual(imported.sections.count(), 2)
        # Exercises are copied, not shared, so editing the import cannot alter
        # the paper it came from.
        imported_reading = imported.sections.get(skill=SectionSkill.READING)
        self.assertNotEqual(
            imported_reading.items.first().exercise_id, self.reading_ex.id
        )
        self.assertEqual(
            imported_reading.items.first().exercise.questions.count(), 2
        )

    def test_import_rejects_an_unknown_format(self):
        self._login(self.admin)
        resp = self.client.post(reverse("mock-test-template-import"), [{
            "format_slug": "not_a_format", "title": "Broken",
            "sections": [{
                "skill": SectionSkill.READING, "title": "R", "duration_minutes": 10,
                "items": [{"exercise": {
                    "title": "X", "exercise_type": ExerciseType.READING,
                    "prompt_text": "p",
                }}],
            }],
        }], format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["code"], "unknown_format")
        self.assertFalse(MockTestTemplate.objects.filter(title="Broken").exists())

    def test_students_cannot_curate_the_library(self):
        self._login(self.student)
        for url in (
            reverse("mock-test-template-duplicate", args=[self.template.id]),
            reverse("mock-test-template-import"),
        ):
            resp = self.client.post(url, [], format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_report_visible_to_the_students_teacher(self):
        attempt = self._attempt()
        ClassStudent.objects.create(
            klass=Class.objects.create(
                class_name="MT class", teacher=self.teacher, academic_year="2026",
            ),
            student=self.student,
        )
        self._login(self.teacher)
        resp = self.client.get(reverse("mock-test-attempt-report", args=[attempt.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["estimated"])


# ==================== Doc 02: Scoring rubrics ====================
class RubricTests(APITransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="ra@t.app", password="password123", full_name="Admin",
            role=UserRole.ADMIN, is_staff=True, is_superuser=True)
        self.teacher = User.objects.create_user(
            email="rt@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="rs@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self.module = LearningModule.objects.create(title="M", created_by=self.teacher)
        self.writing_ex = Exercise.objects.create(
            module=self.module, title="W", exercise_type=ExerciseType.WRITING,
            prompt_text="Write.", created_by=self.teacher)
        self.template = RubricTemplate.objects.create(
            name="IELTS Writing", slug="ielts-w", exercise_type=ExerciseType.WRITING,
            scale_min=0, scale_max=9, score_step=1,
            aggregation=RubricAggregation.MEAN_DOWN_HALF)
        self.criteria = []
        for order, code in enumerate(
            ["task_response", "coherence_cohesion", "lexical_resource", "grammatical_range"],
            start=1,
        ):
            c = RubricCriterion.objects.create(
                template=self.template, name=code, code=code, order=order)
            RubricBandDescriptor.objects.create(
                criterion=c, band_value=Decimal("7.0"), label="Band 7", descriptor="d")
            self.criteria.append(c)
        self.writing_ex.rubric_template = self.template
        self.writing_ex.save(update_fields=["rubric_template"])
        self.klass = Class.objects.create(
            class_name="C", teacher=self.teacher, academic_year="2026")
        ClassStudent.objects.create(klass=self.klass, student=self.student)
        self.assignment = Assignment.objects.create(
            klass=self.klass, exercise=self.writing_ex, assigned_by=self.teacher)
        self.submission = Submission.objects.create(
            assignment=self.assignment, exercise=self.writing_ex, student=self.student,
            submission_type=SubmissionType.WRITING, writing_text="Essay text.")

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _scores(self, values):
        return [{"criterion_id": c.id, "score": v} for c, v in zip(self.criteria, values)]

    # ---- model-level aggregation / normalization ----
    def test_aggregate_mean_down_half(self):
        self.assertEqual(
            self.template.aggregate([Decimal(6), Decimal(6), Decimal(7), Decimal(7)]),
            Decimal("6.5"))
        self.assertEqual(
            self.template.aggregate([Decimal(5), Decimal(6), Decimal(6), Decimal(6)]),
            Decimal("5.5"))

    def test_aggregate_variants(self):
        self.template.aggregation = RubricAggregation.MEAN_NEAREST_HALF
        self.assertEqual(
            self.template.aggregate([Decimal(6), Decimal(7), Decimal(7), Decimal(7)]),
            Decimal("7.0"))  # mean 6.75 -> nearest 0.5 -> 7.0
        self.template.aggregation = RubricAggregation.SUM
        self.assertEqual(self.template.aggregate([Decimal(2), Decimal(3)]), Decimal(5))
        self.template.aggregation = RubricAggregation.MEAN
        self.assertEqual(self.template.aggregate([Decimal(6), Decimal(7)]), Decimal("6.50"))

    def test_normalize_bounds(self):
        self.assertEqual(self.template.normalize(Decimal("0")), Decimal("0.00"))
        self.assertEqual(self.template.normalize(Decimal("9")), Decimal("100.00"))
        self.assertEqual(self.template.normalize(Decimal("6.5")), Decimal("72.22"))

    def test_resolve_for_fallback_chain(self):
        self.assertEqual(RubricTemplate.resolve_for(self.writing_ex), self.template)
        # unpin -> no default configured -> None
        self.writing_ex.rubric_template = None
        self.writing_ex.save(update_fields=["rubric_template"])
        self.assertIsNone(RubricTemplate.resolve_for(self.writing_ex))
        # mark default -> resolves by type
        self.template.is_default_for_type = True
        self.template.save(update_fields=["is_default_for_type"])
        self.assertEqual(RubricTemplate.resolve_for(self.writing_ex), self.template)
        # retire -> None
        self.template.is_active = False
        self.template.save(update_fields=["is_active"])
        self.assertIsNone(RubricTemplate.resolve_for(self.writing_ex))

    def test_template_retirement_protects_historic_scores(self):
        from django.db.models import ProtectedError
        fb = Feedback.objects.create(submission=self.submission, reviewer=self.teacher)
        CriterionScore.objects.create(
            feedback=fb, criterion=self.criteria[0], score=Decimal("7.0"))
        with self.assertRaises(ProtectedError):
            self.criteria[0].delete()

    # ---- API ----
    def test_teacher_grades_via_matrix_normalizes_score(self):
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id, "comments": "ok",
            "criterion_scores": self._scores([6, 6, 7, 7]),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["rubric_overall"], "6.5")
        self.assertEqual(Decimal(resp.data["score"]), Decimal("72.22"))
        self.assertEqual(resp.data["rubric_template_id"], self.template.id)
        self.assertEqual(len(resp.data["criterion_scores"]), 4)

    def test_scores_posted_as_strings_are_accepted(self):
        """The web client sends decimals as strings, because that is how DRF
        renders them back (COERCE_DECIMAL_TO_STRING) and what the generated
        types declare. Posting `"6"` must grade identically to posting `6` —
        otherwise the rubric form 400s on every submission."""
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id, "comments": "ok",
            "criterion_scores": [
                {"criterion_id": criterion.id, "score": str(value)}
                for criterion, value in zip(self.criteria, [6, 6, 7, 7])
            ],
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["rubric_overall"], "6.5")
        self.assertEqual(Decimal(resp.data["score"]), Decimal("72.22"))

    def test_incomplete_set_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id,
            "criterion_scores": self._scores([6, 6, 7]),  # only 3 of 4
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_off_step_score_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id,
            "criterion_scores": self._scores([Decimal("6.3"), 6, 7, 7]),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_template_criteria_rejected(self):
        other = RubricTemplate.objects.create(
            name="Other", slug="other", exercise_type=ExerciseType.WRITING)
        alien = RubricCriterion.objects.create(template=other, name="x", code="x")
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id,
            "criterion_scores": [{"criterion_id": alien.id, "score": 5}],
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_receptive_submission_rejected(self):
        reading_ex = Exercise.objects.create(
            module=self.module, title="R", exercise_type=ExerciseType.READING,
            prompt_text="Read.", created_by=self.teacher)
        reading_sub = Submission.objects.create(
            exercise=reading_ex, student=self.student,
            submission_type=SubmissionType.READING, answers={})
        self._login(self.teacher)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": reading_sub.id,
            "criterion_scores": self._scores([6, 6, 7, 7]),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_post_scores_but_reads_own_breakdown(self):
        # teacher grades first
        self._login(self.teacher)
        self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id,
            "criterion_scores": self._scores([6, 6, 7, 7]),
        }, format="json")
        # student cannot post
        self._login(self.student)
        resp = self.client.post(reverse("feedback-list"), {
            "submission_id": self.submission.id,
            "criterion_scores": self._scores([9, 9, 9, 9]),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # student reads own breakdown
        resp = self.client.get(reverse("submission-feedback", args=[self.submission.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data[0]["rubric_overall"], "6.5")
        self.assertEqual(len(resp.data[0]["criterion_scores"]), 4)

    def test_exercise_rubric_endpoint(self):
        self._login(self.student)
        resp = self.client.get(reverse("exercise-rubric", args=[self.writing_ex.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["slug"], self.template.slug)
        # exercise with no rubric -> 200 + null
        quiz = Exercise.objects.create(
            module=self.module, title="Q", exercise_type=ExerciseType.QUIZ,
            prompt_text="q", created_by=self.teacher)
        resp = self.client.get(reverse("exercise-rubric", args=[quiz.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data)

    def test_rubric_list_hides_inactive_retrieve_shows(self):
        inactive = RubricTemplate.objects.create(
            name="Old", slug="old", exercise_type=ExerciseType.WRITING, is_active=False)
        self._login(self.student)
        resp = self.client.get(reverse("rubric-list"))
        slugs = [t["slug"] for t in resp.data]
        self.assertIn(self.template.slug, slugs)
        self.assertNotIn("old", slugs)
        resp = self.client.get(reverse("rubric-detail", args=[inactive.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


# ==================== Doc 03: Inline writing annotations ====================
class AnnotationTests(APITransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="aa@t.app", password="password123", full_name="Admin",
            role=UserRole.ADMIN, is_staff=True, is_superuser=True)
        self.teacher = User.objects.create_user(
            email="at@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.teacher2 = User.objects.create_user(
            email="at2@t.app", password="password123", full_name="Foreign Teacher",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="as@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self.student2 = User.objects.create_user(
            email="as2@t.app", password="password123", full_name="Other Student",
            role=UserRole.STUDENT)
        self.module = LearningModule.objects.create(title="M", created_by=self.teacher)
        self.writing_ex = Exercise.objects.create(
            module=self.module, title="W", exercise_type=ExerciseType.WRITING,
            prompt_text="Write.", created_by=self.teacher)
        self.speaking_ex = Exercise.objects.create(
            module=self.module, title="S", exercise_type=ExerciseType.SPEAKING,
            prompt_text="Speak.", created_by=self.teacher)
        self.klass = Class.objects.create(
            class_name="C", teacher=self.teacher, academic_year="2026")
        ClassStudent.objects.create(klass=self.klass, student=self.student)
        self.assignment = Assignment.objects.create(
            klass=self.klass, exercise=self.writing_ex, assigned_by=self.teacher)
        self.text = "The quick brown fox."  # len 20
        self.submission = Submission.objects.create(
            assignment=self.assignment, exercise=self.writing_ex, student=self.student,
            submission_type=SubmissionType.WRITING, writing_text=self.text)

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _body(self, **over):
        body = {
            "submission_id": self.submission.id,
            "start_offset": 4, "end_offset": 9, "quoted_text": "quick",
            "category": AnnotationCategory.VOCABULARY, "comment": "word choice",
        }
        body.update(over)
        return body

    def test_teacher_creates_annotation(self):
        self._login(self.teacher)
        resp = self.client.post(reverse("annotation-list"), self._body(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["quoted_text"], "quick")
        self.assertEqual(resp.data["author_id"], self.teacher.id)

    def test_offset_out_of_range_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("annotation-list"),
            self._body(start_offset=15, end_offset=99, quoted_text="fox."),
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_range_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("annotation-list"),
            self._body(start_offset=4, end_offset=4, quoted_text=""),
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_quote_mismatch_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("annotation-list"),
            self._body(quoted_text="wrong"),
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_writing_submission_rejected(self):
        speaking_sub = Submission.objects.create(
            exercise=self.speaking_ex, student=self.student,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url="https://mock/x.m4a")
        self._login(self.teacher)
        resp = self.client.post(
            reverse("annotation-list"),
            self._body(submission_id=speaking_sub.id),
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_create(self):
        self._login(self.student)
        resp = self.client.post(reverse("annotation-list"), self._body(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_foreign_teacher_cannot_create(self):
        self._login(self.teacher2)
        resp = self.client.post(reverse("annotation-list"), self._body(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_read_audience(self):
        WritingAnnotation.objects.create(
            submission=self.submission, author=self.teacher,
            start_offset=4, end_offset=9, quoted_text="quick",
            category=AnnotationCategory.VOCABULARY, comment="c")
        url = reverse("submission-annotations", args=[self.submission.id])
        # owner student -> OK
        self._login(self.student)
        self.assertEqual(len(self.client.get(url).data), 1)
        # foreign student -> 403
        self._login(self.student2)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)
        # class teacher + admin -> OK
        self._login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        self._login(self.admin)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_acknowledge(self):
        ann = WritingAnnotation.objects.create(
            submission=self.submission, author=self.teacher,
            start_offset=4, end_offset=9, quoted_text="quick", comment="c")
        self._login(self.student)
        resp = self.client.post(reverse("annotation-acknowledge", args=[ann.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ann.refresh_from_db()
        self.assertTrue(ann.is_acknowledged)

    def test_no_api_path_updates_writing_text(self):
        # Submissions are create-only: there is no detail CRUD route, so the
        # immutability anchoring assumption holds (NFR1 guard).
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse("submission-detail", args=[self.submission.id])


# ==================== Doc 04: Pronunciation practice ====================
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PronunciationTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()  # reset the attempt-create throttle between tests
        self.teacher = User.objects.create_user(
            email="pt@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.teacher2 = User.objects.create_user(
            email="pt2@t.app", password="password123", full_name="Teacher Two",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="ps@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self.student2 = User.objects.create_user(
            email="ps2@t.app", password="password123", full_name="Other Student",
            role=UserRole.STUDENT)
        self.drill = PronunciationDrill.objects.create(
            target_text="ship sheep", contrast_text="sheep",
            drill_type=DrillType.MINIMAL_PAIR, created_by=self.teacher)

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _audio(self, name="a.webm"):
        return SimpleUploadedFile(name, b"\x00\x01\x02fakeaudiodata", content_type="audio/webm")

    def test_drill_crud_permissions(self):
        self._login(self.student)
        resp = self.client.post(reverse("pronunciation-drill-list"),
                                 {"target_text": "thorough", "drill_type": DrillType.WORD},
                                 format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self._login(self.teacher)
        resp = self.client.post(reverse("pronunciation-drill-list"),
                                 {"target_text": "thorough", "drill_type": DrillType.WORD},
                                 format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["created_by_id"], self.teacher.id)
        drill_id = resp.data["id"]
        # foreign teacher cannot edit someone else's drill
        self._login(self.teacher2)
        resp = self.client.patch(
            reverse("pronunciation-drill-detail", args=[drill_id]),
            {"target_text": "hacked"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_attempt_upload_scores(self):
        self._login(self.student)
        url = reverse("pronunciation-drill-attempts", args=[self.drill.id])
        resp = self.client.post(url, {"audio": self._audio()}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNotNone(resp.data["overall_score"])
        self.assertEqual(resp.data["engine"], "mock")
        words = resp.data["word_results"]
        self.assertEqual([w["word"] for w in words], ["ship", "sheep"])
        self.assertIn("phonemes", words[0])
        self.assertIn("error_type", words[0])

    def test_mock_determinism(self):
        self._login(self.student)
        url = reverse("pronunciation-drill-attempts", args=[self.drill.id])
        r1 = self.client.post(url, {"audio": self._audio("a.webm")}, format="multipart")
        r2 = self.client.post(url, {"audio": self._audio("b.webm")}, format="multipart")
        self.assertEqual(r1.data["overall_score"], r2.data["overall_score"])
        self.assertEqual(r1.data["word_results"], r2.data["word_results"])

    def test_attempt_history_and_isolation(self):
        self._login(self.student)
        url = reverse("pronunciation-drill-attempts", args=[self.drill.id])
        self.client.post(url, {"audio": self._audio()}, format="multipart")
        self.assertEqual(len(self.client.get(url).data), 1)
        self.assertEqual(len(self.client.get(reverse("pronunciation-attempt-me")).data), 1)
        # another student sees nothing
        self._login(self.student2)
        self.assertEqual(len(self.client.get(url).data), 0)
        self.assertEqual(len(self.client.get(reverse("pronunciation-attempt-me")).data), 0)

    def test_oversize_upload_rejected(self):
        self._login(self.student)
        big = SimpleUploadedFile(
            "big.webm", b"0" * (5 * 1024 * 1024 + 1), content_type="audio/webm")
        resp = self.client.post(
            reverse("pronunciation-drill-attempts", args=[self.drill.id]),
            {"audio": big}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CatalogFilterTests(APITransactionTestCase):
    """Band/topic grading filters on modules and their exercises."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="cf@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.m_low = LearningModule.objects.create(
            title="Everyday life", band=BandLevel.BAND_5_6, topic=Topic.LIFE,
            created_by=self.teacher)
        self.m_high = LearningModule.objects.create(
            title="Sports science", band=BandLevel.BAND_7_8, topic=Topic.SPORTS,
            created_by=self.teacher)
        self.m_untagged = LearningModule.objects.create(
            title="Untagged", created_by=self.teacher)
        self.ex_low = Exercise.objects.create(
            module=self.m_low, title="Daily routine essay",
            exercise_type=ExerciseType.WRITING, prompt_text="p",
            band=BandLevel.BAND_5_6, topic=Topic.LIFE)
        self.ex_high = Exercise.objects.create(
            module=self.m_low, title="Stretch task",
            exercise_type=ExerciseType.WRITING, prompt_text="p",
            band=BandLevel.BAND_7_8, topic=Topic.SPORTS)

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def test_module_list_filters(self):
        self._login(self.teacher)
        url = reverse("module-list")
        self.assertEqual(len(self.client.get(url).data), 3)  # unfiltered
        by_band = self.client.get(url, {"band": BandLevel.BAND_5_6}).data
        self.assertEqual([m["id"] for m in by_band], [self.m_low.id])
        by_topic = self.client.get(url, {"topic": Topic.SPORTS}).data
        self.assertEqual([m["id"] for m in by_topic], [self.m_high.id])
        both = self.client.get(
            url, {"band": BandLevel.BAND_7_8, "topic": Topic.LIFE}).data
        self.assertEqual(both, [])
        # fields ride on the serializer
        self.assertEqual(by_band[0]["band"], BandLevel.BAND_5_6)
        self.assertEqual(by_band[0]["topic"], Topic.LIFE)

    def test_module_exercises_filters(self):
        self._login(self.teacher)
        url = reverse("module-exercises", args=[self.m_low.id])
        self.assertEqual(len(self.client.get(url).data), 2)
        by_band = self.client.get(url, {"band": BandLevel.BAND_7_8}).data
        self.assertEqual([e["id"] for e in by_band], [self.ex_high.id])
        by_topic = self.client.get(url, {"topic": Topic.LIFE}).data
        self.assertEqual([e["id"] for e in by_topic], [self.ex_low.id])

    def test_catalog_list_is_open_to_students_and_filterable(self):
        # A standalone catalog item: no module, like seed_exercise_catalog makes.
        loose = Exercise.objects.create(
            title="Standalone speaking task", exercise_type=ExerciseType.SPEAKING,
            prompt_text="Talk about your week.",
            band=BandLevel.BAND_4_5, topic=Topic.LIFE)
        student = User.objects.create_user(
            email="cfs@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self._login(student)
        url = reverse("exercise-list")

        self.assertEqual(len(self.client.get(url).data), 3)  # 2 from setUp + loose
        by_band = self.client.get(url, {"band": BandLevel.BAND_5_6}).data
        self.assertEqual([e["id"] for e in by_band], [self.ex_low.id])
        by_topic = self.client.get(url, {"topic": Topic.LIFE}).data
        self.assertEqual(
            sorted(e["id"] for e in by_topic), sorted([self.ex_low.id, loose.id]))
        by_type = self.client.get(url, {"type": ExerciseType.SPEAKING}).data
        self.assertEqual([e["id"] for e in by_type], [loose.id])
        by_search = self.client.get(url, {"q": "stretch"}).data
        self.assertEqual([e["id"] for e in by_search], [self.ex_high.id])
        combined = self.client.get(
            url, {"band": BandLevel.BAND_4_5, "topic": Topic.SPORTS}).data
        self.assertEqual(combined, [])

    def test_catalog_list_never_leaks_the_answer_key(self):
        question = Question.objects.create(
            exercise=self.ex_low, text="Which option is right?", order=1)
        QuestionOption.objects.create(
            question=question, text="Right", is_correct=True, order=1)
        QuestionOption.objects.create(
            question=question, text="Wrong", is_correct=False, order=2)
        student = User.objects.create_user(
            email="cfs2@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self._login(student)

        row = next(
            e for e in self.client.get(reverse("exercise-list")).data
            if e["id"] == self.ex_low.id
        )
        # Counted, but neither the questions nor their options travel.
        self.assertEqual(row["question_count"], 1)
        self.assertNotIn("questions", row)
        self.assertNotIn("is_correct", json.dumps(row))

    def test_catalog_facets_count_each_dimension_independently(self):
        student = User.objects.create_user(
            email="cfs3@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self._login(student)
        url = reverse("exercise-facets")

        facets = self.client.get(url).data
        self.assertEqual(facets["total"], 2)
        self.assertEqual(facets["bands"][BandLevel.BAND_5_6], 1)
        self.assertEqual(facets["topics"][Topic.SPORTS], 1)
        self.assertEqual(facets["types"][ExerciseType.WRITING], 2)

        # With a band selected, `bands` still shows every alternative (so the
        # student can see what switching would give) while the rest narrow.
        narrowed = self.client.get(url, {"band": BandLevel.BAND_5_6}).data
        self.assertEqual(narrowed["total"], 1)
        self.assertEqual(narrowed["bands"][BandLevel.BAND_7_8], 1)
        self.assertEqual(narrowed["topics"], {Topic.LIFE: 1})

    def test_exercise_create_accepts_band_topic(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("module-exercises", args=[self.m_low.id]),
            {"title": "New", "exercise_type": ExerciseType.WRITING,
             "prompt_text": "p", "band": BandLevel.BAND_6_7, "topic": Topic.TRAVEL},
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["band"], BandLevel.BAND_6_7)
        self.assertEqual(resp.data["topic"], Topic.TRAVEL)
        # invalid choice rejected
        resp = self.client.post(
            reverse("module-exercises", args=[self.m_low.id]),
            {"title": "Bad", "exercise_type": ExerciseType.WRITING,
             "prompt_text": "p", "band": "band_1_2"},
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class MeetingTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()  # reset meeting occupancy counters between tests
        self.admin = User.objects.create_user(
            email="ma@t.app", password="password123", full_name="Admin",
            role=UserRole.ADMIN)
        self.teacher = User.objects.create_user(
            email="mt@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.teacher2 = User.objects.create_user(
            email="mt2@t.app", password="password123", full_name="Teacher Two",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="ms@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self.outsider = User.objects.create_user(
            email="ms2@t.app", password="password123", full_name="Outsider",
            role=UserRole.STUDENT)
        self.klass = Class.objects.create(class_name="M1", teacher=self.teacher)
        ClassStudent.objects.create(klass=self.klass, student=self.student)

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _create(self):
        return Meeting.objects.create(
            klass=self.klass, created_by=self.teacher, title="Office hours")

    def test_create_permissions(self):
        # student cannot create
        self._login(self.student)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Nope"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # foreign teacher cannot create for someone else's class
        self._login(self.teacher2)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Nope"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # owning teacher can; meeting starts active
        self._login(self.teacher)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Office hours"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], MeetingStatus.ACTIVE)
        self.assertEqual(resp.data["created_by"], self.teacher.id)
        self.assertEqual(resp.data["class_name"], "M1")

    def test_list_scoped_by_enrollment(self):
        meeting = self._create()
        self._login(self.student)
        data = self.client.get(reverse("meeting-list")).data
        self.assertEqual([m["id"] for m in data], [meeting.id])
        # a student outside the class sees nothing and cannot retrieve
        self._login(self.outsider)
        self.assertEqual(self.client.get(reverse("meeting-list")).data, [])
        resp = self.client.get(reverse("meeting-detail", args=[meeting.id]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_schedule_meeting(self):
        self._login(self.teacher)
        slot = timezone.now() + timezone.timedelta(days=1)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Tomorrow's lesson",
             "scheduled_at": slot.isoformat()},
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], MeetingStatus.SCHEDULED)
        self.assertIsNotNone(resp.data["scheduled_at"])
        # Not started yet: no clock.
        self.assertIsNone(resp.data["started_at"])
        self.assertIsNone(resp.data["duration_seconds"])
        # The enrolled student sees the booking in their list.
        self._login(self.student)
        listed = self.client.get(reverse("meeting-list")).data
        self.assertIn(resp.data["id"], [m["id"] for m in listed])

    def test_schedule_in_the_past_rejected(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Yesterday",
             "scheduled_at": (timezone.now() - timezone.timedelta(hours=2)).isoformat()},
            format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scheduled_at", resp.data)

    def test_start_now_meeting_has_no_schedule(self):
        self._login(self.teacher)
        resp = self.client.post(
            reverse("meeting-list"),
            {"class_id": self.klass.id, "title": "Right now"}, format="json")
        self.assertEqual(resp.data["status"], MeetingStatus.ACTIVE)
        self.assertIsNone(resp.data["scheduled_at"])

    def test_joining_starts_the_clock_and_opens_a_scheduled_room(self):
        meeting = Meeting.objects.create(
            klass=self.klass, created_by=self.teacher, title="Booked",
            status=MeetingStatus.SCHEDULED,
            scheduled_at=timezone.now() + timezone.timedelta(minutes=30))

        async def run():
            token = str(RefreshToken.for_user(self.student).access_token)
            comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={token}")
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            ready = await comm.receive_json_from()
            self.assertEqual(ready["type"], "ready")
            # The clock origin rides the ready frame so both peers agree.
            self.assertIsNotNone(ready["started_at"])
            await comm.disconnect()

        asyncio.run(run())
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, MeetingStatus.ACTIVE)
        self.assertIsNotNone(meeting.started_at)
        first_started = meeting.started_at

        # A later join must not restart the clock.
        async def rejoin():
            token = str(RefreshToken.for_user(self.teacher).access_token)
            comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={token}")
            await comm.connect()
            await comm.receive_json_from()
            await comm.disconnect()

        asyncio.run(rejoin())
        meeting.refresh_from_db()
        self.assertEqual(meeting.started_at, first_started)

    def test_duration_reported_after_end(self):
        meeting = Meeting.objects.create(
            klass=self.klass, created_by=self.teacher, title="Timed",
            started_at=timezone.now() - timezone.timedelta(minutes=5))
        self._login(self.teacher)
        resp = self.client.post(reverse("meeting-end", args=[meeting.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # ~5 minutes, frozen at end time rather than still counting.
        self.assertAlmostEqual(resp.data["duration_seconds"], 300, delta=10)
        again = self.client.get(reverse("meeting-detail", args=[meeting.id]))
        self.assertEqual(again.data["duration_seconds"], resp.data["duration_seconds"])

    def test_end_meeting(self):
        meeting = self._create()
        url = reverse("meeting-end", args=[meeting.id])
        # student may not end
        self._login(self.student)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)
        # foreign teacher: meeting outside their queryset -> 404
        self._login(self.teacher2)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_404_NOT_FOUND)
        # owner ends it; second call is idempotent and keeps ended_at
        self._login(self.teacher)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["status"], MeetingStatus.ENDED)
        self.assertIsNotNone(resp.data["ended_at"])
        again = self.client.post(url)
        self.assertEqual(again.data["ended_at"], resp.data["ended_at"])

    async def _expect_close(self, comm, code):
        # The signaling consumer accepts BEFORE its checks (so browsers can
        # read custom close codes); rejections arrive as a post-accept close.
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        event = await comm.receive_output()
        self.assertEqual(event["type"], "websocket.close")
        self.assertEqual(event["code"], code)

    def test_signaling_requires_participant_and_live_meeting(self):
        meeting = self._create()

        async def run():
            # enrolled student connects fine
            token = str(RefreshToken.for_user(self.student).access_token)
            comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={token}")
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            ready = await comm.receive_json_from()
            self.assertEqual(ready["type"], "ready")

            # teacher joins second; student gets peer_joined, then relayed
            # offer — every relayed frame is stamped with the sender's sid
            t_token = str(RefreshToken.for_user(self.teacher).access_token)
            t_comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={t_token}")
            t_connected, _ = await t_comm.connect()
            self.assertTrue(t_connected)
            await t_comm.receive_json_from()  # teacher's own "ready"
            joined = await comm.receive_json_from()
            self.assertEqual(joined["type"], "peer_joined")
            self.assertEqual(joined["role"], UserRole.TEACHER)
            self.assertIn("sid", joined)
            await comm.send_json_to({"type": "offer", "sdp": "fake-sdp"})
            relayed = await t_comm.receive_json_from()
            self.assertEqual(relayed["type"], "offer")
            self.assertEqual(relayed["sdp"], "fake-sdp")
            self.assertIn("sid", relayed)

            # a non-dict JSON frame is answered, not a crash
            await comm.send_json_to("ping")
            err = await comm.receive_json_from()
            self.assertEqual(err["type"], "error")

            # outsider student is rejected with 4403
            o_token = str(RefreshToken.for_user(self.outsider).access_token)
            o_comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={o_token}")
            await self._expect_close(o_comm, 4403)

            # third authorized participant is refused: the room is 1:1 (4409)
            a_token = str(RefreshToken.for_user(self.admin).access_token)
            a_comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={a_token}")
            await self._expect_close(a_comm, 4409)

            await comm.disconnect()
            await t_comm.disconnect()

        asyncio.run(run())

        # ended meeting refuses connections with 4404
        meeting.status = MeetingStatus.ENDED
        meeting.save(update_fields=["status"])

        async def run_ended():
            token = str(RefreshToken.for_user(self.student).access_token)
            comm = WebsocketCommunicator(
                application, f"ws/meetings/{meeting.id}/?token={token}")
            await self._expect_close(comm, 4404)

        asyncio.run(run_ended())


@override_settings(AI_ASSIST_BACKEND="mock")
class AiCoachingTests(APITransactionTestCase):
    """Teacher-feedback review + student mistake explanation (core/ai/assist.py)
    against the deterministic mock backend. Gemini is never called here."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="ct@t.app", password="password123", full_name="Teacher",
            role=UserRole.TEACHER)
        self.other_teacher = User.objects.create_user(
            email="co@t.app", password="password123", full_name="Other",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="cs@t.app", password="password123", full_name="Student",
            role=UserRole.STUDENT)
        self.stranger = User.objects.create_user(
            email="cx@t.app", password="password123", full_name="Stranger",
            role=UserRole.STUDENT)
        self.module = LearningModule.objects.create(title="M", created_by=self.teacher)
        self.klass = Class.objects.create(
            class_name="C", teacher=self.teacher, academic_year="2026")
        ClassStudent.objects.create(klass=self.klass, student=self.student)

        self.writing_ex = Exercise.objects.create(
            module=self.module, title="W", exercise_type=ExerciseType.WRITING,
            prompt_text="Describe your weekend.", created_by=self.teacher)
        self.writing_sub = Submission.objects.create(
            assignment=Assignment.objects.create(
                klass=self.klass, exercise=self.writing_ex, assigned_by=self.teacher),
            exercise=self.writing_ex, student=self.student,
            submission_type=SubmissionType.WRITING,
            writing_text="Yesterday I go to the park with my friend.")

        self.quiz_ex = Exercise.objects.create(
            module=self.module, title="Q", exercise_type=ExerciseType.QUIZ,
            prompt_text="Pick the right word.", created_by=self.teacher)
        q = Question.objects.create(exercise=self.quiz_ex, text="She ___ tea.", order=1)
        self.q_right = QuestionOption.objects.create(question=q, text="drinks", is_correct=True, order=1)
        self.q_wrong = QuestionOption.objects.create(question=q, text="drink", is_correct=False, order=2)
        q2 = Question.objects.create(
            exercise=self.quiz_ex, text="The capital of France is [[Paris]].", order=2)
        self.quiz_sub = Submission.objects.create(
            exercise=self.quiz_ex, student=self.student,
            submission_type=SubmissionType.QUIZ,
            answers={"version": 1, "responses": [
                {"question_id": q.id, "type": "mcq", "option_id": self.q_wrong.id},
                {"question_id": q2.id, "type": "fill_blank", "text": '["Paris"]'},
            ]})
        self.quiz_sub.grade()
        self.quiz_sub.save(update_fields=["auto_score"])


    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    # ---- context builder ----
    def test_context_receptive_breakdown_marks_wrong_and_right(self):
        from core.ai.assist import build_context
        ctx = build_context(self.quiz_sub)
        self.assertEqual(ctx["submission_type"], "quiz")
        rows = ctx["questions"]
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["is_correct"])
        self.assertEqual(rows[0]["student_answer"], ["drink"])
        self.assertEqual(rows[0]["correct_options"], ["drinks"])
        self.assertTrue(rows[1]["is_correct"])
        self.assertEqual(rows[1]["correct_answers"], ["Paris"])
        # The key must be masked in the question text sent to the model.
        self.assertNotIn("[[Paris]]", rows[1]["question"])

    # ---- feedback review ----
    def test_teacher_reviews_draft_feedback(self):
        self._login(self.teacher)
        url = f"/api/submissions/{self.writing_sub.id}/ai-review-feedback/"
        resp = self.client.post(url, {"score": "70", "comments": "Good job."}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.data
        self.assertEqual(body["engine"], "mock")
        self.assertIn(body["rating"], range(1, 6))
        areas = {r["area"] for r in body["recommendations"]}
        self.assertIn("specificity", areas)
        self.assertIn("actionability", areas)
        self.assertTrue(body["suggested_comment"].startswith("Good job."))
        # Nothing persisted.
        self.assertEqual(Feedback.objects.count(), 0)
        self.assertEqual(AiInsight.objects.count(), 0)

    def test_review_rejects_empty_draft_and_students(self):
        self._login(self.teacher)
        url = f"/api/submissions/{self.writing_sub.id}/ai-review-feedback/"
        resp = self.client.post(url, {"comments": "  "}, format="json")
        self.assertEqual(resp.status_code, 400)
        self._login(self.student)
        resp = self.client.post(url, {"comments": "hi"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_review_accepts_rubric_cells(self):
        template = RubricTemplate.objects.create(
            name="R", slug="r-w", exercise_type=ExerciseType.WRITING,
            is_default_for_type=True)
        crit = RubricCriterion.objects.create(template=template, name="Task", code="task", order=1)
        self._login(self.teacher)
        url = f"/api/submissions/{self.writing_sub.id}/ai-review-feedback/"
        resp = self.client.post(url, {
            "comments": "Nice ideas, try to add more detail next time and practice tenses.",
            "criterion_scores": [{"criterion_id": crit.id, "score": "6", "note": "ok"}],
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertNotIn(
            "score_alignment", {r["area"] for r in resp.data["recommendations"]})

    # ---- mistake explanation ----
    def test_student_generates_and_reads_explanation(self):
        self._login(self.student)
        url = f"/api/submissions/{self.quiz_sub.id}/ai-explain/"
        self.assertEqual(self.client.get(url).status_code, 404)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 201, resp.data)
        payload = resp.data["payload"]
        self.assertEqual(resp.data["kind"], "mistake_explanation")
        self.assertEqual(payload["engine"], "mock")
        self.assertEqual(len(payload["mistakes"]), 1)
        self.assertEqual(payload["mistakes"][0]["correction"], "drinks")
        self.assertEqual(payload["mistakes"][0]["category"], "comprehension")
        # GET serves the newest stored row; regenerate inserts another.
        got = self.client.get(url)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.data["id"], resp.data["id"])
        again = self.client.post(url)
        self.assertEqual(again.status_code, 201)
        self.assertNotEqual(again.data["id"], resp.data["id"])
        self.assertEqual(self.client.get(url).data["id"], again.data["id"])
        self.assertEqual(AiInsight.objects.filter(submission=self.quiz_sub).count(), 2)

    def test_writing_explanation_and_access_control(self):
        url = f"/api/submissions/{self.writing_sub.id}/ai-explain/"
        self._login(self.stranger)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 403)
        # A teacher who neither teaches the class nor owns the exercise.
        self._login(self.other_teacher)
        self.assertEqual(self.client.post(url).status_code, 403)
        # Class teacher / exercise owner may generate for the student.
        self._login(self.teacher)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["payload"]["mistakes"][0]["category"], "grammar")
        # Owner reads it.
        self._login(self.student)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_speaking_needs_live_backend_and_empty_is_explained_instantly(self):
        speaking_ex = Exercise.objects.create(
            module=self.module, title="S", exercise_type=ExerciseType.SPEAKING,
            prompt_text="Talk.", created_by=self.teacher)
        speaking = Submission.objects.create(
            exercise=speaking_ex, student=self.student,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url="https://example.com/a.webm")
        empty = Submission.objects.create(
            exercise=self.writing_ex, student=self.student,
            submission_type=SubmissionType.WRITING, writing_text="   ")
        self._login(self.student)
        self.assertEqual(
            self.client.post(f"/api/submissions/{speaking.id}/ai-explain/").status_code, 400)
        # Nothing to analyse -> an instant, stored explanation rather than an error.
        resp = self.client.post(f"/api/submissions/{empty.id}/ai-explain/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["engine"], "auto")
        self.assertEqual(resp.data["payload"]["mistakes"], [])
        self.assertIn("No response", resp.data["payload"]["summary"])
        self.assertEqual(AiInsight.objects.filter(submission=empty).count(), 1)

    def test_gemini_failure_maps_to_502(self):
        from unittest import mock
        from core.ai.gemini import GeminiError
        self._login(self.student)
        with override_settings(AI_ASSIST_BACKEND="gemini"), mock.patch(
            "core.ai.assist.gemini.generate_json", side_effect=GeminiError("quota")
        ):
            resp = self.client.post(f"/api/submissions/{self.writing_sub.id}/ai-explain/")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("quota", str(resp.data["detail"]))
        self.assertEqual(AiInsight.objects.count(), 0)

    def test_gemini_backend_parses_structured_reply(self):
        from unittest import mock
        from core.ai import gemini
        fake = {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "summary": "s", "mistakes": [], "strengths": ["a"], "practice_suggestions": ["b"],
        })}]}}]}
        self.assertEqual(gemini.parse_response(json.dumps(fake))["summary"], "s")
        with self.assertRaises(gemini.GeminiError):
            gemini.parse_response(json.dumps({"error": {"message": "nope"}}))
        with self.assertRaises(gemini.GeminiError):
            gemini.parse_response(json.dumps({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}))
        self._login(self.student)
        with override_settings(AI_ASSIST_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.assist.gemini.generate_json", return_value=json.loads(fake["candidates"][0]["content"]["parts"][0]["text"])
        ) as call:
            resp = self.client.post(f"/api/submissions/{self.writing_sub.id}/ai-explain/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["engine"].startswith("gemini:"))
        self.assertEqual(call.call_args.kwargs["schema"]["required"][0], "summary")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class GeminiBackendTests(APITransactionTestCase):
    """Gemini-backed grading / speaking / pronunciation with the HTTP client
    patched — verifies prompt assembly, audio resolution and result mapping."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="gt@t.app", password="password123", full_name="T", role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="gs@t.app", password="password123", full_name="S", role=UserRole.STUDENT)
        self.module = LearningModule.objects.create(title="M", created_by=self.teacher)
        self.writing_ex = Exercise.objects.create(
            module=self.module, title="W", exercise_type=ExerciseType.WRITING,
            prompt_text="Write about your city.", created_by=self.teacher)
        self.speaking_ex = Exercise.objects.create(
            module=self.module, title="S", exercise_type=ExerciseType.SPEAKING,
            prompt_text="Describe your hometown.", created_by=self.teacher)
        from django.conf import settings as dj
        import os
        os.makedirs(os.path.join(dj.MEDIA_ROOT, "mock-tests"), exist_ok=True)
        self.audio_path = os.path.join(dj.MEDIA_ROOT, "mock-tests", "a.webm")
        with open(self.audio_path, "wb") as fh:
            fh.write(b"\x1aE\xdf\xa3fake-webm")

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def test_resolve_audio_media_url_local_path_and_errors(self):
        from core.ai.gemini import resolve_audio
        mime, data = resolve_audio("/media/mock-tests/a.webm")
        self.assertEqual(mime, "audio/webm")
        self.assertTrue(data.startswith(b"\x1aE"))
        mime, _ = resolve_audio(self.audio_path)
        self.assertEqual(mime, "audio/webm")
        mime, _ = resolve_audio("http://localhost:8000/media/mock-tests/a.webm")
        self.assertEqual(mime, "audio/webm")
        with self.assertRaises(ValueError):
            resolve_audio("/media/mock-tests/missing.webm")
        with self.assertRaises(ValueError):
            resolve_audio("")
        with self.assertRaises(ValueError):
            resolve_audio("ftp://x/y.mp3")

    def test_generate_json_attaches_inline_audio(self):
        from unittest import mock
        from core.ai import gemini
        captured = {}

        class FakeResp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self.body

        def fake_open(req, timeout=None):
            captured["body"] = json.loads(req.data)
            captured["key"] = req.get_header("X-goog-api-key")
            return FakeResp(json.dumps({"candidates": [{"content": {"parts": [
                {"text": json.dumps({"transcript": "hello"})}]}}]}).encode())

        with override_settings(GEMINI_API_KEY="k"), mock.patch(
            "core.ai.gemini.urllib.request.urlopen", side_effect=fake_open):
            out = gemini.transcribe("/media/mock-tests/a.webm")
        self.assertEqual(out, "hello")
        self.assertEqual(captured["key"], "k")
        parts = captured["body"]["contents"][0]["parts"]
        self.assertEqual(parts[0]["inlineData"]["mimeType"], "audio/webm")
        self.assertEqual(parts[1]["text"], "Transcribe the attached audio.")
        self.assertEqual(captured["body"]["generationConfig"]["responseMimeType"], "application/json")

    def test_gemini_backend_grades_writing_and_records_feedback(self):
        from unittest import mock
        sub = Submission.objects.create(
            exercise=self.writing_ex, student=self.student,
            submission_type=SubmissionType.WRITING, writing_text="My city is big.")
        self._login(self.teacher)
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.backends.gemini.generate_json",
            return_value={"score": 72.4, "comments": "Nice.", "criteria": [
                {"code": "task_response", "band": 6, "note": "ok"}]},
        ) as call:
            resp = self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["score"], "72.40")
        self.assertTrue(resp.data["is_ai_generated"])
        self.assertIn("task_response: 6", resp.data["comments"])
        self.assertIn("My city is big.", call.call_args.kwargs["user"])
        self.assertIsNone(call.call_args.kwargs.get("audio"))
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubmissionStatus.AI_GRADED)

    def test_gemini_backend_scores_speaking_from_local_audio(self):
        from unittest import mock
        sub = Submission.objects.create(
            exercise=self.speaking_ex, student=self.student,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url="/media/mock-tests/a.webm")
        self._login(self.teacher)
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.backends.gemini.generate_json",
            return_value={"transcript": "um my town", "score": 55, "comments": "Work on tense.", "criteria": []},
        ) as call:
            resp = self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["score"], "55.00")
        self.assertEqual(call.call_args.kwargs["audio"][0], "audio/webm")
        # Missing recording -> 400, unreachable Gemini -> 502.
        sub.audio_recording_url = "/media/mock-tests/nope.webm"
        sub.save()
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"):
            self.assertEqual(
                self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/").status_code, 400)
        sub.audio_recording_url = "/media/mock-tests/a.webm"
        sub.save()
        from core.ai.gemini import GeminiError
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.backends.gemini.generate_json", side_effect=GeminiError("503")):
            self.assertEqual(
                self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/").status_code, 502)

    def test_gemini_pronunciation_backend_maps_word_results(self):
        from unittest import mock
        drill = PronunciationDrill.objects.create(
            target_text="ship sheep", drill_type=DrillType.MINIMAL_PAIR,
            created_by=self.teacher)
        self._login(self.student)
        payload = {
            "transcript": "ship sheep", "accuracy": 81.5, "fluency": 90,
            "completeness": 100, "prosody": 70, "feedback": "Lengthen /iː/.",
            "words": [
                {"word": "ship", "accuracy": 88, "error_type": "None",
                 "phonemes": [{"phoneme": "ʃ", "accuracy": 95}, {"phoneme": "ɪ", "accuracy": 80}]},
                {"word": "sheep", "accuracy": 55, "error_type": "Mispronunciation",
                 "phonemes": [{"phoneme": "iː", "accuracy": 40}]},
            ],
        }
        audio = SimpleUploadedFile("try.webm", b"\x1aE\xdf\xa3x", content_type="audio/webm")
        with override_settings(PRONUNCIATION_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.pronunciation.gemini.generate_json", return_value=payload) as call:
            resp = self.client.post(
                f"/api/pronunciation/drills/{drill.id}/attempts/", {"audio": audio}, format="multipart")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(call.call_args.kwargs["audio"][0], "audio/webm")
        self.assertIn('"ship sheep"', call.call_args.kwargs["user"])
        attempt = PronunciationAttempt.objects.get(pk=resp.data["id"])
        self.assertEqual(attempt.accuracy_score, Decimal("81.50"))
        self.assertEqual(attempt.overall_score, Decimal("85.38"))  # (81.5+90+100+70)/4
        self.assertTrue(attempt.engine.startswith("gemini:"))
        self.assertIsNone(attempt.word_results[0]["error_type"])
        self.assertEqual(attempt.word_results[1]["error_type"], "Mispronunciation")
        self.assertEqual(attempt.engine_metadata["transcript"], "ship sheep")

    def test_speaking_explanation_uses_transcript(self):
        from unittest import mock
        sub = Submission.objects.create(
            exercise=self.speaking_ex, student=self.student,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url="/media/mock-tests/a.webm")
        self._login(self.student)
        url = f"/api/submissions/{sub.id}/ai-explain/"
        # Mock assist backend cannot transcribe -> 400.
        self.assertEqual(self.client.post(url).status_code, 400)
        explain = {"summary": "s", "mistakes": [], "strengths": [], "practice_suggestions": []}
        with override_settings(AI_ASSIST_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.assist.gemini.transcribe", return_value="I goes home") as tr, mock.patch(
            "core.ai.assist.gemini.generate_json", return_value=dict(explain)) as gen:
            resp = self.client.post(url)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn('"transcript": "I goes home"', gen.call_args.kwargs["user"])
        self.assertEqual(AiInsight.objects.get().payload["transcript"], "I goes home")
        # Second run reuses the stored transcript instead of transcribing again.
        with override_settings(AI_ASSIST_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
            "core.ai.assist.gemini.transcribe") as tr2, mock.patch(
            "core.ai.assist.gemini.generate_json", return_value=dict(explain)):
            self.assertEqual(self.client.post(url).status_code, 201)
        tr.assert_called_once()
        tr2.assert_not_called()


class NvidiaProviderTests(APITransactionTestCase):
    """NVIDIA NIM client (OpenAI-compatible) + provider dispatcher, offline."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="nt@t.app", password="password123", full_name="T", role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="ns@t.app", password="password123", full_name="S", role=UserRole.STUDENT)
        ex = Exercise.objects.create(
            title="W", exercise_type=ExerciseType.WRITING, prompt_text="Write.",
            created_by=self.teacher)
        self.sub = Submission.objects.create(
            exercise=ex, student=self.student, submission_type=SubmissionType.WRITING,
            writing_text="I goes home.")

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def test_extract_json_tolerates_fences_prose_and_think_blocks(self):
        from core.ai.nvidia import extract_json
        self.assertEqual(extract_json('```json\n{"a": 1}\n```')["a"], 1)
        self.assertEqual(extract_json('<think>hmm {not json}</think>\nSure: {"a": 2} done')["a"], 2)
        self.assertEqual(extract_json('{"a": {"b": [1, 2]}}')["a"]["b"], [1, 2])
        from core.ai.gemini import GeminiError
        with self.assertRaises(GeminiError):
            extract_json("no json here")
        with self.assertRaises(GeminiError):
            extract_json("[1, 2]")

    def test_parse_response_shapes(self):
        from core.ai import nvidia
        from core.ai.gemini import GeminiError
        ok = {"choices": [{"message": {"content": '{"x": true}'}, "finish_reason": "stop"}]}
        self.assertTrue(nvidia.parse_response(json.dumps(ok))["x"])
        with self.assertRaises(GeminiError):
            nvidia.parse_response(json.dumps({"error": {"message": "bad key"}}))
        with self.assertRaises(GeminiError):
            nvidia.parse_response(json.dumps({"detail": "Not found", "status": 404}))
        with self.assertRaises(GeminiError):
            nvidia.parse_response(json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}))

    def test_schema_hint_renders_shape(self):
        from core.ai.nvidia import _schema_hint
        from core.ai.assist import MISTAKES_SCHEMA
        hint = _schema_hint(MISTAKES_SCHEMA)
        self.assertIn('"mistakes": [', hint)
        self.assertIn("grammar | vocabulary", hint)
        self.assertIn("required: summary, mistakes", hint)

    def test_resolve_routes_by_task_and_forces_gemini_for_audio(self):
        from core.ai import llm
        with override_settings(
            AI_GRADING_MODEL="nvidia:moonshotai/kimi-k3",
            AI_ASSIST_MODEL="nvidia", NVIDIA_MODEL="deepseek-ai/deepseek-v4-flash-0731",
            AI_TEXT_PROVIDER="gemini", GEMINI_MODEL="gemini-3.6-flash",
        ):
            self.assertEqual(llm.resolve("grading"), ("nvidia", "moonshotai/kimi-k3"))
            self.assertEqual(llm.resolve("assist"), ("nvidia", "deepseek-ai/deepseek-v4-flash-0731"))
            self.assertEqual(llm.resolve("grading", audio=True), ("gemini", "gemini-3.6-flash"))
            self.assertEqual(llm.label("assist"), "nvidia:deepseek-ai/deepseek-v4-flash-0731")
        with override_settings(AI_GRADING_MODEL="", AI_TEXT_PROVIDER="gemini"):
            self.assertEqual(llm.resolve("grading")[0], "gemini")
        with override_settings(AI_GRADING_MODEL="openai:gpt"):
            with self.assertRaises(ValueError):
                llm.resolve("grading")

    def test_nvidia_generate_json_builds_openai_request(self):
        from unittest import mock
        from core.ai import nvidia
        captured = {}

        class FakeResp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __iter__(self): return iter(self.body.split(b"\n"))

        def sse(*chunks):
            lines = [("data: " + json.dumps(c)).encode() for c in chunks] + [b"data: [DONE]"]
            return b"\n\n".join(lines)

        def fake_open(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data)
            return FakeResp(sse(
                {"choices": [{"delta": {"role": "assistant", "reasoning_content": "thinking..."}}]},
                {"choices": [{"delta": {"content": "```json\n{\"summary\": \"s\", "}}]},
                {"choices": [{"delta": {"content": "\"mistakes\": [], \"strengths\": [], \"practice_suggestions\": []}\n```"}, "finish_reason": "stop"}]},
            ))

        with override_settings(NVIDIA_API_KEY="nv-k"), mock.patch(
            "core.ai.nvidia.urllib.request.urlopen", side_effect=fake_open):
            out = nvidia.generate_json(
                system="SYS", user="USER", schema={"type": "OBJECT", "properties": {
                    "summary": {"type": "STRING"}}, "required": ["summary"]},
                model="moonshotai/kimi-k3")
        self.assertEqual(out["summary"], "s")
        self.assertTrue(captured["url"].endswith("/v1/chat/completions"))
        self.assertEqual(captured["auth"], "Bearer nv-k")
        self.assertEqual(captured["body"]["model"], "moonshotai/kimi-k3")
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual(captured["body"]["chat_template_kwargs"], {"thinking": False})
        self.assertEqual(captured["body"]["messages"][1], {"role": "user", "content": "USER"})
        self.assertTrue(captured["body"]["messages"][0]["content"].startswith("SYS"))
        self.assertIn("Respond with ONE JSON object", captured["body"]["messages"][0]["content"])
        with self.assertRaises(ValueError):
            nvidia.generate_json(system="s", user="u", schema={}, audio=("audio/webm", b"x"))
        from django.core.exceptions import ImproperlyConfigured
        with override_settings(NVIDIA_API_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                nvidia.generate_json(system="s", user="u", schema={})

    def test_grading_and_assist_route_to_nvidia_models(self):
        from unittest import mock
        self._login(self.teacher)
        grade = {"score": 61, "comments": "Fix verbs.", "criteria": []}
        with override_settings(
            AI_BACKEND="llm", AI_GRADING_MODEL="nvidia:moonshotai/kimi-k3", NVIDIA_API_KEY="k",
        ), mock.patch("core.ai.llm.nvidia.generate_json", return_value=grade) as nv, mock.patch(
            "core.ai.llm.gemini.generate_json") as gm:
            resp = self.client.post(f"/api/submissions/{self.sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["score"], "61.00")
        self.assertIn("[AI · nvidia:moonshotai/kimi-k3]", resp.data["comments"])
        self.assertEqual(nv.call_args.kwargs["model"], "moonshotai/kimi-k3")
        gm.assert_not_called()

        self._login(self.student)
        explain = {"summary": "s", "mistakes": [], "strengths": [], "practice_suggestions": []}
        with override_settings(
            AI_ASSIST_BACKEND="llm", AI_ASSIST_MODEL="nvidia:deepseek-ai/deepseek-v4-flash-0731",
            NVIDIA_API_KEY="k",
        ), mock.patch("core.ai.llm.nvidia.generate_json", return_value=explain) as nv:
            resp = self.client.post(f"/api/submissions/{self.sub.id}/ai-explain/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["engine"], "nvidia:deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(nv.call_args.kwargs["model"], "deepseek-ai/deepseek-v4-flash-0731")

    def test_speaking_still_uses_gemini_when_text_routed_to_nvidia(self):
        from unittest import mock
        import os, tempfile
        from django.conf import settings as dj
        media = tempfile.mkdtemp()
        os.makedirs(os.path.join(media, "mock-tests"))
        with open(os.path.join(media, "mock-tests", "a.webm"), "wb") as fh:
            fh.write(b"\x1aE\xdf\xa3x")
        ex = Exercise.objects.create(
            title="S", exercise_type=ExerciseType.SPEAKING, prompt_text="Talk.",
            created_by=self.teacher)
        sub = Submission.objects.create(
            exercise=ex, student=self.student, submission_type=SubmissionType.SPEAKING,
            audio_recording_url="/media/mock-tests/a.webm")
        self._login(self.teacher)
        spoken = {"transcript": "hi", "score": 40, "comments": "c", "criteria": []}
        with override_settings(
            MEDIA_ROOT=media, AI_BACKEND="llm", AI_GRADING_MODEL="nvidia:moonshotai/kimi-k3",
            GEMINI_API_KEY="g", NVIDIA_API_KEY="k",
        ), mock.patch("core.ai.llm.gemini.generate_json", return_value=spoken) as gm, mock.patch(
            "core.ai.llm.nvidia.generate_json") as nv:
            resp = self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn("[AI · gemini:", resp.data["comments"])
        self.assertEqual(gm.call_args.kwargs["audio"][0], "audio/webm")
        nv.assert_not_called()


    def test_fallback_provider_when_nvidia_fails(self):
        from unittest import mock
        from core.ai.gemini import GeminiError
        self._login(self.teacher)
        grade = {"score": 58, "comments": "ok", "criteria": []}
        with override_settings(
            AI_BACKEND="llm", AI_GRADING_MODEL="nvidia:moonshotai/kimi-k3",
            AI_FALLBACK_PROVIDER="gemini", NVIDIA_API_KEY="k", GEMINI_API_KEY="g",
            GEMINI_MODEL="gemini-3.6-flash",
        ), mock.patch("core.ai.llm.nvidia.generate_json", side_effect=GeminiError("504")) as nv, mock.patch(
            "core.ai.llm.gemini.generate_json", return_value=dict(grade)) as gm:
            resp = self.client.post(f"/api/submissions/{self.sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIn("[AI · gemini:gemini-3.6-flash]", resp.data["comments"])
        nv.assert_called_once()
        gm.assert_called_once()
        # No fallback configured -> the NIM failure surfaces as 502.
        with override_settings(
            AI_BACKEND="llm", AI_GRADING_MODEL="nvidia:moonshotai/kimi-k3",
            AI_FALLBACK_PROVIDER="", NVIDIA_API_KEY="k",
        ), mock.patch("core.ai.llm.nvidia.generate_json", side_effect=GeminiError("504")):
            self.assertEqual(
                self.client.post(f"/api/submissions/{self.sub.id}/ai-evaluate/").status_code, 502)
        # Assist reports the engine that actually answered.
        self._login(self.student)
        explain = {"summary": "s", "mistakes": [], "strengths": [], "practice_suggestions": []}
        with override_settings(
            AI_ASSIST_BACKEND="llm", AI_ASSIST_MODEL="nvidia:deepseek-ai/deepseek-v4-pro-0813",
            AI_FALLBACK_PROVIDER="gemini", NVIDIA_API_KEY="k", GEMINI_API_KEY="g",
            GEMINI_MODEL="gemini-3.6-flash",
        ), mock.patch("core.ai.llm.nvidia.generate_json", side_effect=GeminiError("504")), mock.patch(
            "core.ai.llm.gemini.generate_json", return_value=dict(explain)):
            resp = self.client.post(f"/api/submissions/{self.sub.id}/ai-explain/")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["engine"], "gemini:gemini-3.6-flash")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProductionReadinessTests(APITransactionTestCase):
    """The production surface added for deployment: the anonymous health probe
    and signed, expiring media links (core/media.py)."""

    def setUp(self):
        import os

        from django.conf import settings as dj

        self.student = User.objects.create_user(
            email="pr_s@t.app", password="password123", full_name="S",
            role=UserRole.STUDENT)
        self.teacher = User.objects.create_user(
            email="pr_t@t.app", password="password123", full_name="T",
            role=UserRole.TEACHER)
        self.rel = "pronunciation/2026/09/clip.webm"
        target = os.path.join(dj.MEDIA_ROOT, self.rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(b"\x1aE\xdf\xa3audio-bytes")

    def _login(self, user):
        resp = self.client.post(
            reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    @staticmethod
    def _token(url):
        """The `t` parameter, percent-decoded the way Django decodes it."""
        from urllib.parse import parse_qs, urlparse

        return parse_qs(urlparse(url).query)["t"][0]

    # ---- health probe ----
    def test_health_is_anonymous_and_checks_the_database(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["database"], "ok")
        self.assertGreaterEqual(resp.data["db_latency_ms"], 0)
        # No auth header was sent: the probe must not be behind IsAuthenticated,
        # or a load balancer would mark a healthy box as down.
        self.assertNotIn("counts", resp.data)

    def test_health_reports_503_when_the_database_is_unreachable(self):
        from unittest import mock

        with mock.patch("django.db.connection.cursor", side_effect=RuntimeError("down")):
            # Also asserts the failure is logged: with DEBUG off this traceback
            # is the only record that the probe went red.
            with self.assertLogs("core.views", level="ERROR"):
                resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.data["status"], "degraded")
        self.assertEqual(resp.data["database"], "error")
        # The driver message names host/port/user — it must not reach an
        # anonymous caller.
        self.assertNotIn("down", json.dumps(resp.data))

    # ---- media signing ----
    def test_sign_unsign_roundtrip_and_url_shape(self):
        from core import media

        for stored in (
            self.rel,
            f"/media/{self.rel}",
            f"https://api.example.com/media/{self.rel}",
            f"/media/{self.rel}?t=stale-token",
        ):
            self.assertEqual(media.relative_path(stored), self.rel, stored)
        self.assertEqual(media.relative_path(""), "")
        self.assertEqual(media.signed_url(""), "")
        # Absolute URLs pointing somewhere else are not ours to sign.
        self.assertEqual(media.relative_path("https://cdn.example.com/a.m4a"), "")
        self.assertEqual(media.signed_url("https://cdn.example.com/a.m4a"), "")

        url = media.signed_url(self.rel)
        self.assertTrue(url.startswith(f"/media/{self.rel}?t="))
        self.assertEqual(media.unsign(self._token(url)), self.rel)

    def test_bad_expired_and_foreign_tokens_are_refused(self):
        from core import media

        token = media.sign(self.rel)
        with self.assertRaises(media.InvalidMediaToken):
            media.unsign(token, max_age=-1)          # expired
        with self.assertRaises(media.InvalidMediaToken):
            media.unsign(token[:-3] + "aaa")         # tampered
        with self.assertRaises(media.InvalidMediaToken):
            media.unsign("")                         # absent
        # A token minted for another purpose must not open media.
        from django.core import signing
        with self.assertRaises(media.InvalidMediaToken):
            media.unsign(signing.dumps(self.rel, salt="somewhere.else"))

    def test_signed_link_serves_the_file_and_nothing_else_does(self):
        from core import media

        url = media.signed_url(self.rel)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"\x1aE\xdf\xa3audio-bytes")
        self.assertIn("private", resp["Cache-Control"])

        # Anonymous, no token.
        self.assertEqual(self.client.get(f"/media/{self.rel}").status_code, 403)
        # Token that signs a different file than the path being fetched.
        other = media.sign("pronunciation/2026/09/someone-else.webm")
        self.assertEqual(
            self.client.get(f"/media/{self.rel}?t={other}").status_code, 403)
        # A JWT is not enough on its own: <audio> cannot send one, so the only
        # header-based caller is a script, and it should mint a signed link.
        self._login(self.student)
        self.assertEqual(self.client.get(f"/media/{self.rel}").status_code, 403)

    def test_session_user_may_browse_media_and_traversal_is_blocked(self):
        # Django admin links are plain hrefs from a logged-in session.
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(f"/media/{self.rel}").status_code, 200)
        for attack in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd"):
            resp = self.client.get(f"/media/{attack}")
            self.assertIn(resp.status_code, (403, 404), attack)

    def test_x_accel_redirect_hands_streaming_to_nginx(self):
        from core import media

        url = media.signed_url(self.rel)
        with override_settings(MEDIA_X_ACCEL_REDIRECT=True,
                               MEDIA_X_ACCEL_PREFIX="/protected-media/"):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Accel-Redirect"], f"/protected-media/{self.rel}")
        self.assertEqual(resp.content, b"")

    # ---- serializers hand out signed links ----
    def test_serializers_emit_signed_media_urls(self):
        from core import media

        drill = PronunciationDrill.objects.create(
            target_text="ship", drill_type=DrillType.WORD, created_by=self.teacher)
        attempt = PronunciationAttempt.objects.create(
            drill=drill, student=self.student,
            audio_file=SimpleUploadedFile("a.webm", b"x", content_type="audio/webm"))
        data = s_mod.PronunciationAttemptSerializer(attempt).data
        self.assertIn("?t=", data["audio_url"])
        self.assertEqual(
            media.unsign(self._token(data["audio_url"])), attempt.audio_file.name)

        exercise = Exercise.objects.create(
            title="S", exercise_type=ExerciseType.SPEAKING, prompt_text="Talk.",
            created_by=self.teacher)
        submission = Submission.objects.create(
            exercise=exercise, student=self.student,
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url=f"/media/{self.rel}")
        data = s_mod.SubmissionSerializer(submission).data
        # The raw stored value is untouched; the playable one is signed.
        self.assertEqual(data["audio_recording_url"], f"/media/{self.rel}")
        self.assertIn("?t=", data["audio_url"])
        # An external URL we never stored passes straight through.
        submission.audio_recording_url = "https://cdn.example.com/a.m4a"
        self.assertEqual(
            s_mod.SubmissionSerializer(submission).data["audio_url"],
            "https://cdn.example.com/a.m4a")

    def test_ai_layer_still_resolves_a_signed_url_to_bytes(self):
        from core.ai.gemini import resolve_audio

        mime, data = resolve_audio(f"/media/{self.rel}?t=whatever")
        self.assertEqual(mime, "audio/webm")
        self.assertEqual(data, b"\x1aE\xdf\xa3audio-bytes")

    # ---- settings helpers ----
    def test_env_helpers_parse_config(self):
        import os
        from unittest import mock

        from config.settings import env_bool, env_list

        with mock.patch.dict(os.environ, {"X": "TRUE"}, clear=False):
            self.assertTrue(env_bool("X", False))
        for value in ("false", "0", "no", "off", "  "):
            with mock.patch.dict(os.environ, {"X": value}, clear=False):
                self.assertEqual(env_bool("X", value.strip() == ""), value.strip() == "")
        with mock.patch.dict(os.environ, {"H": " a.com , ,b.com "}, clear=False):
            self.assertEqual(env_list("H"), ["a.com", "b.com"])
        self.assertEqual(env_list("DEFINITELY_UNSET_VAR_NAME"), [])


# ==================== Mock tests: automatic AI marking ====================
from datetime import timedelta  # noqa: E402
from django.test import override_settings as _override_settings  # noqa: E402
from core.models import (  # noqa: E402
    AiGradingStatus as _AiGradingStatus,
    AiInsightKind,
)


@_override_settings(
    AI_BACKEND="mock", AI_ASSIST_BACKEND="mock",
    MOCK_TEST_AI_AUTOGRADE=True, MOCK_TEST_AI_ASYNC=False,
)
class MockTestAiGradingTests(APITransactionTestCase):
    """Every submitted section is marked by the AI layer (core/mock_tests.py):
    Writing/Speaking through the grading backend, Listening/Reading through a
    stored per-question mistake explanation. Deterministic mock backends —
    no network."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="ai-teacher@test.app", password="password123",
            full_name="AI Teacher", role=UserRole.TEACHER,
        )
        self.student = User.objects.create_user(
            email="ai-student@test.app", password="password123",
            full_name="AI Student", role=UserRole.STUDENT,
        )
        self.other_student = User.objects.create_user(
            email="ai-other@test.app", password="password123",
            full_name="Other", role=UserRole.STUDENT,
        )
        self.admin = User.objects.create_user(
            email="ai-admin@test.app", password="password123",
            full_name="AI Admin", role=UserRole.ADMIN,
        )
        self.format = TestFormat.objects.create(
            slug="ielts_academic", name="IELTS Academic", version="2026",
            overall_strategy=OverallStrategy.BAND_AVERAGE, score_precision="0.5",
        )
        ScoreConversionTable.objects.create(
            format=self.format, skill=SectionSkill.READING,
            mapping={"0": "0.0", "1": "4.0", "2": "5.0"}, source_note="t",
        )
        ScoreConversionTable.objects.create(
            format=self.format, skill=SectionSkill.WRITING,
            mapping={"0": "0.0", "50": "6.0", "80": "8.0"}, source_note="t",
        )
        module = LearningModule.objects.create(title="AI module", created_by=self.teacher)
        self.reading_ex = Exercise.objects.create(
            module=module, title="Passage 1", exercise_type=ExerciseType.READING,
            prompt_text="Read.", content_text="Paris is the capital of France.",
            created_by=self.teacher,
        )
        self.q1 = Question.objects.create(exercise=self.reading_ex, text="Pick one", order=0)
        self.q1_wrong = QuestionOption.objects.create(question=self.q1, text="A", order=0)
        self.q1_right = QuestionOption.objects.create(
            question=self.q1, text="B", is_correct=True, order=1,
        )
        self.q2 = Question.objects.create(
            exercise=self.reading_ex, text="The capital is [[Paris]].", order=1,
        )
        self.writing_ex = Exercise.objects.create(
            module=module, title="Task 1", exercise_type=ExerciseType.WRITING,
            prompt_text="Write 150 words.", created_by=self.teacher,
        )
        self.template = MockTestTemplate.objects.create(
            format=self.format, title="AI-marked test", created_by=self.admin,
        )
        self.reading_section = TestSection.objects.create(
            template=self.template, skill=SectionSkill.READING, title="Reading",
            order=0, duration_minutes=60,
        )
        TestSectionExercise.objects.create(
            section=self.reading_section, exercise=self.reading_ex, order=0,
        )
        self.writing_section = TestSection.objects.create(
            template=self.template, skill=SectionSkill.WRITING, title="Writing",
            order=1, duration_minutes=60,
        )
        TestSectionExercise.objects.create(
            section=self.writing_section, exercise=self.writing_ex, order=0,
        )

    # ---- helpers ----
    def _login(self, user):
        resp = self.client.post(
            reverse("login"), {"email": user.email, "password": "password123"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _reading_draft(self, first_option):
        return {
            "answers": {
                str(self.reading_ex.id): {
                    "version": 1,
                    "responses": [
                        {"question_id": self.q1.id, "type": "mcq",
                         "option_id": first_option.id},
                        {"question_id": self.q2.id, "type": "fill_blank",
                         "text": '["paris"]'},
                    ],
                },
            },
            "writing": {},
            "meta": {"audio_played": []},
        }

    def _writing_draft(self, text):
        return {"answers": {}, "writing": {str(self.writing_ex.id): text}, "meta": {}}

    def _sit(self, reading_option=None, writing_text=None):
        """Start an attempt and submit its sections in order. ``None`` leaves a
        section untouched."""
        attempt = mock_tests.start_attempt(self.template, self.student)
        reading = attempt.sections.get(section__order=0)
        writing = attempt.sections.get(section__order=1)
        if reading_option is not None:
            mock_tests.start_section(attempt, reading.id)
            mock_tests.submit_section(
                attempt, reading.id, draft=self._reading_draft(reading_option),
            )
        if writing_text is not None:
            mock_tests.start_section(attempt, writing.id)
            mock_tests.submit_section(
                attempt, writing.id, draft=self._writing_draft(writing_text),
            )
        return attempt

    def _report(self, attempt):
        resp = self.client.get(reverse("mock-test-attempt-report", args=[attempt.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data

    # ---- receptive sections ----
    def test_reading_section_gets_per_question_explanations(self):
        attempt = self._sit(reading_option=self.q1_wrong)
        reading = attempt.sections.get(section__order=0)
        self.assertEqual(reading.ai_status, _AiGradingStatus.DONE, reading.ai_error)
        submission = reading.submissions.get().submission
        insight = submission.ai_insights.get(kind=AiInsightKind.MISTAKE_EXPLANATION)
        self.assertEqual(insight.engine, "mock")
        self.assertIsNone(insight.requested_by)

        self._login(self.student)
        section = self._report(attempt)["sections"][0]
        self.assertEqual(section["ai_status"], "done")
        self.assertEqual(section["raw_score"], 1)
        task = section["submissions"][0]
        self.assertEqual(task["ai_status"], "done")
        self.assertEqual(task["exercise_title"], "Passage 1")
        self.assertIn("mistake", task["explanation"]["summary"])
        wrong, right = task["questions"]
        self.assertEqual((wrong["number"], right["number"]), (1, 2))
        self.assertFalse(wrong["is_correct"])
        self.assertEqual(wrong["student_answer"], "A")
        self.assertEqual(wrong["correct"], ["B"])
        self.assertTrue(wrong["explanation"])
        self.assertTrue(wrong["tip"])
        self.assertTrue(right["is_correct"])
        self.assertEqual(right["student_answer"], "paris")
        self.assertEqual(right["correct"], ["Paris"])
        self.assertIsNone(right["explanation"])
        # The key stays masked in the question text, as in the student card.
        self.assertNotIn("[[Paris]]", right["question"])

    def test_perfect_reading_section_needs_no_model_call(self):
        attempt = self._sit(reading_option=self.q1_right)
        submission = attempt.sections.get(section__order=0).submissions.get().submission
        insight = submission.ai_insights.get(kind=AiInsightKind.MISTAKE_EXPLANATION)
        self.assertEqual(insight.engine, "auto")
        self.assertEqual(insight.payload["mistakes"], [])
        self.assertIn("All 2 answers are correct", insight.payload["summary"])

    # ---- productive sections ----
    def test_writing_is_graded_by_ai_and_the_report_completes(self):
        attempt = self._sit(
            reading_option=self.q1_right,
            writing_text="Yesterday I go to the park. However, it was closed.",
        )
        attempt.refresh_from_db()
        writing = attempt.sections.get(section__order=1)
        self.assertEqual(writing.ai_status, _AiGradingStatus.DONE, writing.ai_error)
        submission = writing.submissions.get().submission
        feedback = submission.feedback.get()
        self.assertTrue(feedback.is_ai_generated)
        self.assertIsNone(feedback.reviewer)
        self.assertEqual(submission.status, SubmissionStatus.AI_GRADED)
        self.assertIsNotNone(writing.converted_score)
        self.assertEqual(attempt.status, AttemptStatus.COMPLETED)
        self.assertIsNotNone(attempt.overall_score)

        self._login(self.student)
        report = self._report(attempt)
        self.assertFalse(report["partial"])
        task = report["sections"][1]["submissions"][0]
        self.assertEqual(task["ai_status"], "done")
        self.assertTrue(task["feedback"]["is_ai_generated"])
        self.assertEqual(task["feedback"]["score"], str(feedback.score))
        self.assertEqual(task["writing_text"], submission.writing_text)
        self.assertIsNone(task["audio_url"])
        self.assertEqual(task["questions"], [])

    def test_speaking_recording_is_served_through_a_signed_link(self):
        speaking_ex = Exercise.objects.create(
            module=self.writing_ex.module, title="Part 1",
            exercise_type=ExerciseType.SPEAKING, prompt_text="Talk.",
            created_by=self.teacher,
        )
        section = TestSection.objects.create(
            template=self.template, skill=SectionSkill.SPEAKING, title="Speaking",
            order=2, duration_minutes=14,
        )
        TestSectionExercise.objects.create(section=section, exercise=speaking_ex, order=0)
        attempt = mock_tests.start_attempt(self.template, self.student, mode="practice")
        speaking = attempt.sections.get(section__order=2)
        mock_tests.start_section(attempt, speaking.id)
        mock_tests.submit_section(
            attempt, speaking.id, draft={},
            recordings={str(speaking_ex.id): "/media/mock-tests/2026/09/answer.webm"},
        )
        self._login(self.student)
        task = self._report(attempt)["sections"][2]["submissions"][0]
        self.assertEqual(task["audio_recording_url"], "/media/mock-tests/2026/09/answer.webm")
        self.assertTrue(task["audio_url"].startswith("http://testserver/media/mock-tests/2026/09/answer.webm?t="))
        self.assertTrue(task["feedback"]["is_ai_generated"])  # mock engine graded it

    def test_empty_writing_scores_zero_without_a_model_call(self):
        attempt = self._sit(reading_option=self.q1_right, writing_text="   ")
        submission = attempt.sections.get(section__order=1).submissions.get().submission
        feedback = submission.feedback.get()
        self.assertEqual(feedback.score, Decimal("0"))
        self.assertIn("No response", feedback.comments)
        self.assertEqual(
            attempt.sections.get(section__order=1).converted_score, Decimal("0.0"),
        )

    def test_a_teachers_mark_is_never_overridden(self):
        with _override_settings(MOCK_TEST_AI_AUTOGRADE=False):
            attempt = self._sit(reading_option=self.q1_right, writing_text="Some text.")
        writing = attempt.sections.get(section__order=1)
        self.assertEqual(writing.ai_status, _AiGradingStatus.PENDING)
        submission = writing.submissions.get().submission
        Feedback.objects.create(
            submission=submission, reviewer=self.teacher, score=Decimal("70"),
            comments="Teacher mark",
        )
        self._login(self.student)
        resp = self.client.post(reverse("mock-test-attempt-ai-grade", args=[attempt.id]))
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(submission.feedback.count(), 1)
        self.assertFalse(submission.feedback.filter(is_ai_generated=True).exists())
        self.assertEqual(resp.data["sections"][1]["ai_status"], "done")

    # ---- the explicit endpoint ----
    def test_ai_grade_is_idempotent_and_retries_failed_sections(self):
        attempt = self._sit(reading_option=self.q1_wrong, writing_text="Some text.")
        self._login(self.student)
        url = reverse("mock-test-attempt-ai-grade", args=[attempt.id])
        insights = AiInsight.objects.count()
        feedback = Feedback.objects.count()

        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # nothing to claim
        self.assertEqual((AiInsight.objects.count(), Feedback.objects.count()),
                         (insights, feedback))

        reading = attempt.sections.get(section__order=0)
        reading.submissions.get().submission.ai_insights.all().delete()
        reading.ai_status = _AiGradingStatus.FAILED
        reading.ai_error = "boom"
        reading.save(update_fields=["ai_status", "ai_error"])

        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        section = resp.data["sections"][0]
        self.assertEqual(section["ai_status"], "done")
        self.assertEqual(section["ai_error"], "")
        self.assertEqual(AiInsight.objects.count(), insights)  # regenerated once
        self.assertEqual(Feedback.objects.count(), feedback)   # writing untouched

    def test_stale_running_section_is_reported_failed_and_reclaimable(self):
        attempt = self._sit(reading_option=self.q1_wrong)
        reading = attempt.sections.get(section__order=0)
        reading.submissions.get().submission.ai_insights.all().delete()
        reading.ai_status = _AiGradingStatus.RUNNING
        reading.ai_started_at = timezone.now() - timedelta(hours=1)
        reading.save(update_fields=["ai_status", "ai_started_at"])

        self._login(self.student)
        section = self._report(attempt)["sections"][0]
        self.assertEqual(section["ai_status"], "failed")
        self.assertIn("interrupted", section["ai_error"])

        resp = self.client.post(reverse("mock-test-attempt-ai-grade", args=[attempt.id]))
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.data["sections"][0]["ai_status"], "done")

    def test_ai_grade_follows_report_visibility(self):
        attempt = self._sit(reading_option=self.q1_wrong)
        url = reverse("mock-test-attempt-ai-grade", args=[attempt.id])
        self._login(self.other_student)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)
        self._login(self.teacher)  # not this student's teacher
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)
        ClassStudent.objects.create(
            klass=Class.objects.create(
                class_name="AI class", teacher=self.teacher, academic_year="2026",
            ),
            student=self.student,
        )
        self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)
        self._login(self.admin)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)

    def test_unfinished_sections_are_neither_claimed_nor_detailed(self):
        attempt = self._sit(reading_option=self.q1_wrong)  # writing not started
        self._login(self.student)
        report = self._report(attempt)
        writing = report["sections"][1]
        self.assertEqual(writing["status"], "not_started")
        self.assertEqual(writing["ai_status"], "pending")
        self.assertEqual(writing["submissions"], [])
        resp = self.client.post(reverse("mock-test-attempt-ai-grade", args=[attempt.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            attempt.sections.get(section__order=1).ai_status, _AiGradingStatus.PENDING,
        )

    def test_autograde_switch_leaves_sections_pending(self):
        with _override_settings(MOCK_TEST_AI_AUTOGRADE=False):
            attempt = self._sit(reading_option=self.q1_wrong)
        reading = attempt.sections.get(section__order=0)
        self.assertEqual(reading.ai_status, _AiGradingStatus.PENDING)
        self.assertFalse(AiInsight.objects.exists())


# ==================== Instant results: no model call for empty / key-marked work ====================
class InstantGradingTests(APITransactionTestCase):
    """An empty submission never reaches a model — every AI path answers it on
    the spot — and a receptive submission with an answer key is marked by the
    key. Proven against the *live* backend selection with the provider client
    patched, so a regression that starts calling the model fails loudly."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="ig-t@test.app", password="password123", full_name="T",
            role=UserRole.TEACHER)
        self.student = User.objects.create_user(
            email="ig-s@test.app", password="password123", full_name="S",
            role=UserRole.STUDENT)
        module = LearningModule.objects.create(title="IG", created_by=self.teacher)
        self.writing_ex = Exercise.objects.create(
            module=module, title="Essay", exercise_type=ExerciseType.WRITING,
            prompt_text="Write.", created_by=self.teacher)
        self.speaking_ex = Exercise.objects.create(
            module=module, title="Talk", exercise_type=ExerciseType.SPEAKING,
            prompt_text="Talk.", created_by=self.teacher)
        self.quiz_ex = Exercise.objects.create(
            module=module, title="Quiz", exercise_type=ExerciseType.QUIZ,
            prompt_text="Pick.", created_by=self.teacher)
        self.q1 = Question.objects.create(exercise=self.quiz_ex, text="One?", order=0)
        self.q1_right = QuestionOption.objects.create(question=self.q1, text="yes", is_correct=True, order=0)
        self.q1_wrong = QuestionOption.objects.create(question=self.q1, text="no", order=1)
        self.q2 = Question.objects.create(exercise=self.quiz_ex, text="Capital: [[Paris]].", order=1)

    def _login(self, user):
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "password123"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")

    def _submission(self, exercise, **fields):
        return Submission.objects.create(
            exercise=exercise, student=self.student,
            submission_type=exercise.exercise_type, **fields)

    def _evaluate_live(self, submission):
        """POST ai-evaluate with the live backend selected and the provider
        client patched: returns (response, provider mock)."""
        from unittest import mock
        self._login(self.teacher)
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
                "core.ai.backends.llm.generate_json") as call:
            resp = self.client.post(f"/api/submissions/{submission.id}/ai-evaluate/")
        return resp, call

    # ---- the rule itself ----
    def test_has_response_rules(self):
        self.assertFalse(self._submission(self.writing_ex, writing_text="  \n ").has_response())
        self.assertTrue(self._submission(self.writing_ex, writing_text="An essay.").has_response())
        self.assertFalse(self._submission(self.speaking_ex, audio_recording_url="").has_response())
        self.assertTrue(self._submission(self.speaking_ex, audio_recording_url="/media/a.webm").has_response())
        blank = {"version": 1, "responses": [
            {"question_id": self.q1.id, "type": "mcq"},
            {"question_id": self.q2.id, "type": "fill_blank", "text": '["", " "]'},
        ]}
        self.assertFalse(self._submission(self.quiz_ex, answers=blank).has_response())
        self.assertFalse(self._submission(self.quiz_ex, answers=None).has_response())
        answered = {"version": 1, "responses": [
            {"question_id": self.q2.id, "type": "fill_blank", "text": '["", "paris"]'},
        ]}
        self.assertTrue(self._submission(self.quiz_ex, answers=answered).has_response())

    # ---- grading ----
    def test_empty_writing_is_scored_zero_without_the_model(self):
        sub = self._submission(self.writing_ex, writing_text="")
        resp, call = self._evaluate_live(sub)
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_not_called()
        self.assertEqual(resp.data["score"], "0.00")
        self.assertTrue(resp.data["is_ai_generated"])
        self.assertIn("No response was submitted", resp.data["comments"])
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubmissionStatus.AI_GRADED)

    def test_missing_recording_is_scored_zero_without_the_model(self):
        sub = self._submission(self.speaking_ex, audio_recording_url="")
        resp, call = self._evaluate_live(sub)
        self.assertEqual(resp.status_code, 201, resp.data)  # used to be a 400
        call.assert_not_called()
        self.assertEqual(resp.data["score"], "0.00")

    def test_keyed_quiz_is_marked_by_the_key_without_the_model(self):
        sub = self._submission(self.quiz_ex, answers={"version": 1, "responses": [
            {"question_id": self.q1.id, "type": "mcq", "option_id": self.q1_wrong.id},
            {"question_id": self.q2.id, "type": "fill_blank", "text": '["Paris"]'},
        ]})
        resp, call = self._evaluate_live(sub)
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_not_called()
        self.assertEqual(resp.data["score"], "50.00")
        self.assertIn("1 of 2 correct", resp.data["comments"])
        sub.refresh_from_db()
        self.assertEqual(sub.auto_score, Decimal("50.00"))

    def test_blank_quiz_answers_score_zero_instantly(self):
        sub = self._submission(self.quiz_ex, answers={"version": 1, "responses": [
            {"question_id": self.q1.id, "type": "mcq"},
            {"question_id": self.q2.id, "type": "fill_blank", "text": '[""]'},
        ]})
        resp, call = self._evaluate_live(sub)
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_not_called()
        self.assertEqual(resp.data["score"], "0.00")

    def test_student_practice_with_nothing_is_instant(self):
        from unittest import mock
        self._login(self.student)
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
                "core.ai.backends.llm.generate_json") as call:
            resp = self.client.post(reverse("submission-ai-practice"), {
                "exercise_id": self.writing_ex.id, "writing_text": "",
            }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_not_called()
        self.assertEqual(resp.data["feedback"]["score"], "0.00")

    def test_speaking_service_shortcut_returns_the_same_shape(self):
        from core.ai.service import transcribe_and_score_speaking
        sub = self._submission(self.speaking_ex, audio_recording_url="")
        result = transcribe_and_score_speaking(sub)
        self.assertEqual(result["score"], "0.00")
        self.assertEqual(result["transcript"], "")
        self.assertIn("No response", result["comments"])
        self.assertEqual(sub.feedback.count(), 1)

    def test_real_content_still_reaches_the_model(self):
        from unittest import mock
        sub = self._submission(self.writing_ex, writing_text="Yesterday I go to school.")
        self._login(self.teacher)
        with override_settings(AI_BACKEND="gemini", GEMINI_API_KEY="k"), mock.patch(
                "core.ai.backends.llm.generate_json",
                return_value={"score": 61, "comments": "ok", "criteria": [], "engine": "gemini:x"}) as call:
            resp = self.client.post(f"/api/submissions/{sub.id}/ai-evaluate/")
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_called_once()
        self.assertEqual(resp.data["score"], "61.00")

    # ---- coaching + mock tests ----
    def test_explain_mistakes_on_blank_answers_is_instant(self):
        from unittest import mock
        sub = self._submission(self.quiz_ex, answers={"version": 1, "responses": [
            {"question_id": self.q1.id, "type": "mcq"},
        ]})
        self._login(self.student)
        with override_settings(AI_ASSIST_BACKEND="llm", GEMINI_API_KEY="k"), mock.patch(
                "core.ai.assist.llm.generate_json") as call:
            resp = self.client.post(f"/api/submissions/{sub.id}/ai-explain/")
        self.assertEqual(resp.status_code, 201, resp.data)
        call.assert_not_called()
        self.assertEqual(resp.data["engine"], "auto")
        self.assertEqual(resp.data["payload"]["mistakes"], [])

    @override_settings(AI_BACKEND="mock", AI_ASSIST_BACKEND="mock",
                       MOCK_TEST_AI_AUTOGRADE=True, MOCK_TEST_AI_ASYNC=False)
    def test_blank_mock_test_reading_part_needs_no_model(self):
        from unittest import mock
        fmt = TestFormat.objects.create(
            slug="custom_ig", name="Custom", version="1",
            overall_strategy=OverallStrategy.MEAN_PERCENT, score_precision="0.01")
        template = MockTestTemplate.objects.create(format=fmt, title="IG test", created_by=self.teacher)
        section = TestSection.objects.create(
            template=template, skill=SectionSkill.READING, title="R", order=0, duration_minutes=10)
        TestSectionExercise.objects.create(section=section, exercise=self.quiz_ex, order=0)
        attempt = mock_tests.start_attempt(template, self.student)
        reading = attempt.sections.get()
        mock_tests.start_section(attempt, reading.id)
        with mock.patch("core.ai.assist.MockAssistBackend.explain_mistakes") as call:
            mock_tests.submit_section(attempt, reading.id, draft={
                "answers": {str(self.quiz_ex.id): {"version": 1, "responses": [
                    {"question_id": self.q1.id, "type": "mcq"}]}},
                "writing": {}, "meta": {}})
        call.assert_not_called()
        reading.refresh_from_db()
        self.assertEqual(reading.ai_status, "done")
        insight = reading.submissions.get().submission.ai_insights.get()
        self.assertEqual(insight.engine, "auto")
        self.assertIn("No answers were submitted", insight.payload["summary"])

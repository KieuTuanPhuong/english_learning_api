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
from core.models import (
    AiModel,
    AnnotationCategory,
    Assignment,
    AttemptMode,
    AttemptStatus,
    Class,
    ClassStudent,
    CriterionScore,
    DrillType,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
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

"""Seed the demo database — Django port of the old app/seed.py.

Usage:
    ./venv/bin/python manage.py seed_demo

Wipes all rows then inserts a realistic demo dataset. Password for everyone
is "password123". The admin user is also a Django superuser (is_staff +
is_superuser) so it can log into /admin/.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Assignment,
    Class,
    ClassStudent,
    DifficultyLevel,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    LessonPlan,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    User,
    UserRole,
    UserStatus,
)

PASSWORD = "password123"


class Command(BaseCommand):
    help = "Wipe and reseed the demo dataset."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Wiping existing data…")
        # Children first; cascades cover the rest, but be explicit.
        Feedback.objects.all().delete()
        Submission.objects.all().delete()
        Assignment.objects.all().delete()
        StudentModuleProgress.objects.all().delete()
        Exercise.objects.all().delete()
        LearningModule.objects.all().delete()
        LessonPlan.objects.all().delete()
        ClassStudent.objects.all().delete()
        Class.objects.all().delete()
        User.objects.all().delete()

        # ---------- Users ----------
        self.stdout.write("Seeding users…")
        admin = User.objects.create_user(
            email="admin@english.app", password=PASSWORD, full_name="Site Admin",
            role=UserRole.ADMIN, status=UserStatus.ACTIVE,
            is_staff=True, is_superuser=True,
        )

        teachers_data = [
            ("emma.teacher@english.app", "Emma Carter"),
            ("david.teacher@english.app", "David Nguyen"),
            ("sophie.teacher@english.app", "Sophie Tran"),
        ]
        teachers = [
            User.objects.create_user(
                email=e, password=PASSWORD, full_name=n,
                role=UserRole.TEACHER, status=UserStatus.ACTIVE,
            )
            for e, n in teachers_data
        ]

        students_data = [
            ("alice.student@english.app", "Alice Pham"),
            ("bob.student@english.app", "Bob Le"),
            ("charlie.student@english.app", "Charlie Vu"),
            ("diana.student@english.app", "Diana Ho"),
            ("ethan.student@english.app", "Ethan Bui"),
            ("fiona.student@english.app", "Fiona Dang"),
            ("george.student@english.app", "George Doan"),
            ("hana.student@english.app", "Hana Truong"),
            ("ivy.student@english.app", "Ivy Mai"),
            ("jack.student@english.app", "Jack Lam"),
            ("kim.student@english.app", "Kim Phan"),
            ("luna.student@english.app", "Luna Vo"),
        ]
        students = []
        for i, (e, n) in enumerate(students_data):
            st = UserStatus.SUSPENDED if i == 11 else UserStatus.ACTIVE
            students.append(User.objects.create_user(
                email=e, password=PASSWORD, full_name=n,
                role=UserRole.STUDENT, status=st,
            ))

        # ---------- Classes ----------
        self.stdout.write("Seeding classes…")
        classes = [
            Class.objects.create(class_name="English 101 — Beginners",
                                 teacher=teachers[0], academic_year="2025-2026"),
            Class.objects.create(class_name="Intermediate Writing",
                                 teacher=teachers[0], academic_year="2025-2026"),
            Class.objects.create(class_name="Conversational English",
                                 teacher=teachers[1], academic_year="2025-2026"),
            Class.objects.create(class_name="IELTS Prep — Advanced",
                                 teacher=teachers[2], academic_year="2025-2026"),
        ]

        # ---------- Enrollments ----------
        self.stdout.write("Seeding enrollments…")
        enroll_plan = {
            0: students[0:6],
            1: students[2:8],
            2: students[4:10],
            3: students[6:11],
        }
        for class_idx, roster in enroll_plan.items():
            for s in roster:
                ClassStudent.objects.create(klass=classes[class_idx], student=s)

        # ---------- Lesson Plans ----------
        self.stdout.write("Seeding lesson plans…")
        today = date.today()
        LessonPlan.objects.bulk_create([
            LessonPlan(klass=classes[0], title="Week 1 — Greetings & Self-Intro",
                       objectives="Learn basic greetings, introduce yourself, ask about others.",
                       start_date=today, end_date=today + timedelta(days=7)),
            LessonPlan(klass=classes[1], title="Unit 3 — Persuasive Essays",
                       objectives="Structure an argument, use evidence, write a 5-paragraph essay.",
                       start_date=today + timedelta(days=3), end_date=today + timedelta(days=17)),
            LessonPlan(klass=classes[3], title="IELTS Writing Task 2 — Opinion Essays",
                       objectives="Master opinion essay structure for Band 7+.",
                       start_date=today, end_date=today + timedelta(days=14)),
        ])

        # ---------- Learning Modules ----------
        self.stdout.write("Seeding learning modules…")
        modules = [
            LearningModule.objects.create(
                title="Everyday Conversations",
                description="Real-life dialogues for ordering food, asking directions, small talk.",
                difficulty_level=DifficultyLevel.BEGINNER, created_by=teachers[1]),
            LearningModule.objects.create(
                title="Grammar Foundations",
                description="Tenses, articles, prepositions explained with practice.",
                difficulty_level=DifficultyLevel.BEGINNER, created_by=teachers[0]),
            LearningModule.objects.create(
                title="Academic Writing",
                description="Essay structure, citations, formal register, transitions.",
                difficulty_level=DifficultyLevel.INTERMEDIATE, created_by=teachers[0]),
            LearningModule.objects.create(
                title="Pronunciation Workshop",
                description="Vowel sounds, intonation, common mispronunciations.",
                difficulty_level=DifficultyLevel.INTERMEDIATE, created_by=teachers[1]),
            LearningModule.objects.create(
                title="IELTS Mastery",
                description="Full IELTS prep: Reading, Writing, Listening, Speaking strategies.",
                difficulty_level=DifficultyLevel.ADVANCED, created_by=teachers[2]),
        ]

        # ---------- Exercises ----------
        self.stdout.write("Seeding exercises…")
        exercises = [
            Exercise.objects.create(module=modules[0], title="Ordering at a cafe",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Record yourself ordering a coffee and a pastry.",
                audio_prompt_url="https://mock.cdn/audio/cafe-order.mp3"),
            Exercise.objects.create(module=modules[0], title="Asking for directions",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a short dialogue asking a stranger for directions to the train station."),
            Exercise.objects.create(module=modules[0], title="Greetings quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Which greeting is most appropriate in a business meeting? Explain why."),
            Exercise.objects.create(module=modules[1], title="Past simple vs present perfect",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write 5 sentences contrasting past simple and present perfect tense."),
            Exercise.objects.create(module=modules[1], title="Articles practice",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Fill in the blanks: ___ apple, ___ honest person, ___ university."),
            Exercise.objects.create(module=modules[1], title="Preposition story",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a short story (100 words) using 'in', 'on', 'at' correctly."),
            Exercise.objects.create(module=modules[2], title="Thesis statement workshop",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write three different thesis statements arguing about social media's impact on teens."),
            Exercise.objects.create(module=modules[2], title="Persuasive essay",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a 5-paragraph persuasive essay (~400 words) on: 'Should schools require uniforms?'"),
            Exercise.objects.create(module=modules[2], title="Transitions quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Name 5 transition words for contrast and 5 for cause/effect."),
            Exercise.objects.create(module=modules[3], title="Minimal pairs: ship vs sheep",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Record yourself saying these word pairs: ship/sheep, bit/beat, fit/feet.",
                audio_prompt_url="https://mock.cdn/audio/minimal-pairs.mp3"),
            Exercise.objects.create(module=modules[3], title="Intonation practice",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Read the provided paragraph aloud, paying attention to falling intonation on statements.",
                audio_prompt_url="https://mock.cdn/audio/intonation.mp3"),
            Exercise.objects.create(module=modules[4], title="IELTS Writing Task 2 — Opinion",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Some people believe technology has made us less social. To what extent do you agree? Write 250+ words."),
            Exercise.objects.create(module=modules[4], title="IELTS Speaking Part 2",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Describe a place you have visited recently. You should say where it is, when you went, what you did, and why you enjoyed it. Speak for 1-2 minutes.",
                audio_prompt_url="https://mock.cdn/audio/ielts-part2.mp3"),
            Exercise.objects.create(module=modules[4], title="IELTS vocabulary quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Provide synonyms for: ubiquitous, ameliorate, paradigm, scrutinize."),
            Exercise.objects.create(module=modules[4], title="IELTS Writing Task 1 — Chart",
                exercise_type=ExerciseType.WRITING,
                prompt_text="The chart shows energy consumption by source from 2000-2020. Summarise the key trends in 150+ words."),
        ]

        # ---------- Assignments ----------
        self.stdout.write("Seeding assignments…")
        now = timezone.now()
        assignments = [
            Assignment.objects.create(klass=classes[0], exercise=exercises[0],
                assigned_by=teachers[0], due_date=now + timedelta(days=3)),
            Assignment.objects.create(klass=classes[0], exercise=exercises[2],
                assigned_by=teachers[0], due_date=now + timedelta(days=7)),
            Assignment.objects.create(klass=classes[1], exercise=exercises[7],
                assigned_by=teachers[0], due_date=now + timedelta(days=10)),
            Assignment.objects.create(klass=classes[2], exercise=exercises[1],
                assigned_by=teachers[1], due_date=now + timedelta(days=5)),
            Assignment.objects.create(klass=classes[3], exercise=exercises[11],
                assigned_by=teachers[2], due_date=now + timedelta(days=14)),
            Assignment.objects.create(klass=classes[3], exercise=exercises[12],
                assigned_by=teachers[2], due_date=now + timedelta(days=14)),
        ]

        # ---------- Submissions (order preserved for feedback indexing) ----------
        self.stdout.write("Seeding submissions…")
        submissions = []
        for s in students[0:4]:
            submissions.append(Submission.objects.create(
                assignment=assignments[0], exercise=exercises[0], student=s,
                submission_type=SubmissionType.SPEAKING,
                audio_recording_url=f"https://mock.cdn/recordings/{s.email}-cafe.m4a"))
        for s in students[0:5]:
            submissions.append(Submission.objects.create(
                assignment=assignments[1], exercise=exercises[2], student=s,
                submission_type=SubmissionType.WRITING,
                writing_text="'Good morning' is most appropriate — formal yet warm and not time-restrictive within work hours."))
        essay_text = (
            "School uniforms reduce socio-economic disparity by removing visible markers "
            "of wealth in the classroom. They also simplify morning routines and create a "
            "sense of belonging. However, critics argue uniforms suppress self-expression. "
            "On balance, the unifying effect outweighs the cost: a shared identity helps "
            "students focus on learning rather than appearance."
        )
        for s in students[2:6]:
            submissions.append(Submission.objects.create(
                assignment=assignments[2], exercise=exercises[7], student=s,
                submission_type=SubmissionType.WRITING, writing_text=essay_text))
        for s in students[4:8]:
            submissions.append(Submission.objects.create(
                assignment=assignments[3], exercise=exercises[1], student=s,
                submission_type=SubmissionType.WRITING,
                writing_text=(
                    "A: Excuse me, could you tell me how to get to the train station?\n"
                    "B: Sure! Walk two blocks straight, then turn left at the bakery. It's on your right.\n"
                    "A: Thank you so much!")))
        ielts_essay = (
            "Modern technology has fundamentally reshaped human interaction. While it is true "
            "that constant connectivity through smartphones can reduce face-to-face contact, I "
            "largely disagree that we have become less social overall. Platforms enable us to "
            "maintain relationships across continents and join communities aligned with niche "
            "interests, which was previously impossible…"
        )
        for s in students[6:10]:
            submissions.append(Submission.objects.create(
                assignment=assignments[4], exercise=exercises[11], student=s,
                submission_type=SubmissionType.WRITING, writing_text=ielts_essay))
        for s in students[6:9]:
            submissions.append(Submission.objects.create(
                assignment=assignments[5], exercise=exercises[12], student=s,
                submission_type=SubmissionType.SPEAKING,
                audio_recording_url=f"https://mock.cdn/recordings/{s.email}-ielts-part2.m4a"))
        submissions.append(Submission.objects.create(
            assignment=None, exercise=exercises[3], student=students[0],
            submission_type=SubmissionType.WRITING,
            writing_text="I lived in Hanoi for five years. I have visited Hanoi twice this year."))
        submissions.append(Submission.objects.create(
            assignment=None, exercise=exercises[9], student=students[1],
            submission_type=SubmissionType.SPEAKING,
            audio_recording_url="https://mock.cdn/recordings/bob-pronunciation.m4a"))

        # ---------- Feedback ----------
        self.stdout.write("Seeding feedback…")
        Feedback.objects.bulk_create([
            Feedback(submission=submissions[0], reviewer=teachers[0],
                     score=Decimal("85.00"), comments="Clear pronunciation. Slow down on 'pastry'."),
            Feedback(submission=submissions[1], reviewer=teachers[0],
                     score=Decimal("78.00"), comments="Good vocabulary. Work on intonation at end of questions."),
            Feedback(submission=submissions[2], reviewer=teachers[0],
                     score=Decimal("92.00"), comments="Excellent — natural and confident."),
            Feedback(submission=submissions[4], reviewer=teachers[0],
                     score=Decimal("88.00"), comments="Correct, with thoughtful reasoning."),
            Feedback(submission=submissions[5], reviewer=teachers[0],
                     score=Decimal("80.00"), comments="Right answer; could expand the explanation."),
            Feedback(submission=submissions[9], reviewer=teachers[0],
                     score=Decimal("84.00"), comments="Solid thesis. Add more concrete examples in body paragraph 2."),
            Feedback(submission=submissions[10], reviewer=teachers[0],
                     score=Decimal("77.00"), comments="Watch comma splices in paragraph 3."),
            Feedback(submission=submissions[13], reviewer=teachers[1],
                     score=Decimal("90.00"), comments="Perfect — natural and polite."),
            Feedback(submission=submissions[14], reviewer=teachers[1],
                     score=Decimal("82.00"), comments="Good, but try varying the phrasing."),
            Feedback(submission=submissions[17], reviewer=teachers[2],
                     score=Decimal("75.00"), comments="Band 6.5. Develop counter-arguments more thoroughly."),
            Feedback(submission=submissions[18], reviewer=teachers[2],
                     score=Decimal("82.00"), comments="Band 7. Strong introduction; conclusion could be sharper."),
            Feedback(submission=submissions[19], reviewer=teachers[2],
                     score=Decimal("88.00"), comments="Band 7.5. Excellent range of vocabulary."),
            Feedback(submission=submissions[21], reviewer=teachers[2],
                     score=Decimal("80.00"), comments="Band 7. Good fluency; reduce hesitation markers."),
            Feedback(submission=submissions[24], reviewer=teachers[0],
                     score=Decimal("70.00"), comments="Second sentence — well done."),
        ])

        # ---------- Progress ----------
        self.stdout.write("Seeding progress…")
        StudentModuleProgress.objects.bulk_create([
            StudentModuleProgress(student=students[0], module=modules[0],
                completion_percentage=Decimal("65.00"), last_accessed_at=now),
            StudentModuleProgress(student=students[0], module=modules[1],
                completion_percentage=Decimal("40.00"), last_accessed_at=now - timedelta(days=2)),
            StudentModuleProgress(student=students[1], module=modules[3],
                completion_percentage=Decimal("25.00"), last_accessed_at=now - timedelta(days=1)),
            StudentModuleProgress(student=students[2], module=modules[2],
                completion_percentage=Decimal("55.00"), last_accessed_at=now),
            StudentModuleProgress(student=students[3], module=modules[2],
                completion_percentage=Decimal("80.00"), last_accessed_at=now),
            StudentModuleProgress(student=students[6], module=modules[4],
                completion_percentage=Decimal("45.00"), last_accessed_at=now),
            StudentModuleProgress(student=students[7], module=modules[4],
                completion_percentage=Decimal("60.00"), last_accessed_at=now - timedelta(hours=6)),
            StudentModuleProgress(student=students[8], module=modules[4],
                completion_percentage=Decimal("100.00"), last_accessed_at=now - timedelta(days=1)),
        ])

        self.stdout.write(self.style.SUCCESS("\n=== Seed complete ==="))
        self.stdout.write(
            f"  Users:        {User.objects.count():>4}  (1 admin, 3 teachers, 12 students — 1 suspended)")
        self.stdout.write(f"  Classes:      {Class.objects.count():>4}")
        self.stdout.write(f"  Enrollments:  {ClassStudent.objects.count():>4}")
        self.stdout.write(f"  LessonPlans:  {LessonPlan.objects.count():>4}")
        self.stdout.write(f"  Modules:      {LearningModule.objects.count():>4}")
        self.stdout.write(f"  Exercises:    {Exercise.objects.count():>4}")
        self.stdout.write(f"  Assignments:  {Assignment.objects.count():>4}")
        self.stdout.write(f"  Submissions:  {Submission.objects.count():>4}")
        self.stdout.write(f"  Feedback:     {Feedback.objects.count():>4}")
        self.stdout.write(f"  Progress:     {StudentModuleProgress.objects.count():>4}")
        self.stdout.write("\nLogin (password for everyone): password123")
        self.stdout.write("  Admin:    admin@english.app  (also Django /admin/ superuser)")
        self.stdout.write("  Teachers: emma/david/sophie .teacher@english.app")
        self.stdout.write("  Students: alice … luna .student@english.app")

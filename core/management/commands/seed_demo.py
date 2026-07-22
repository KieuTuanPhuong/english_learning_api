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
    Question,
    QuestionOption,
    StudentModuleProgress,
    Submission,
    SubmissionType,
    SubmissionStatus,
    User,
    UserRole,
    UserStatus,
    StudyMaterial,
    AiModel,
    SystemLog,
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
        StudyMaterial.objects.all().delete()
        Exercise.objects.all().delete()
        LearningModule.objects.all().delete()
        LessonPlan.objects.all().delete()
        ClassStudent.objects.all().delete()
        Class.objects.all().delete()
        User.objects.all().delete()
        AiModel.objects.all().delete()
        SystemLog.objects.all().delete()

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
                audio_prompt_url="https://mock.cdn/audio/cafe-order.mp3",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[0], title="Asking for directions",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a short dialogue asking a stranger for directions to the train station.",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[0], title="Greetings quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Which greeting is most appropriate in a business meeting? Explain why.",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[1], title="Past simple vs present perfect",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write 5 sentences contrasting past simple and present perfect tense.",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[1], title="Articles practice",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Fill in the blanks: ___ apple, ___ honest person, ___ university.",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[1], title="Preposition story",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a short story (100 words) using 'in', 'on', 'at' correctly.",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[2], title="Thesis statement workshop",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write three different thesis statements arguing about social media's impact on teens.",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[2], title="Persuasive essay",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Write a 5-paragraph persuasive essay (~400 words) on: 'Should schools require uniforms?'",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[2], title="Transitions quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Name 5 transition words for contrast and 5 for cause/effect.",
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[3], title="Minimal pairs: ship vs sheep",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Record yourself saying these word pairs: ship/sheep, bit/beat, fit/feet.",
                audio_prompt_url="https://mock.cdn/audio/minimal-pairs.mp3",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[3], title="Intonation practice",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Read the provided paragraph aloud, paying attention to falling intonation on statements.",
                audio_prompt_url="https://mock.cdn/audio/intonation.mp3",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[4], title="IELTS Writing Task 2 — Opinion",
                exercise_type=ExerciseType.WRITING,
                prompt_text="Some people believe technology has made us less social. To what extent do you agree? Write 250+ words.",
                created_by=teachers[2]),
            Exercise.objects.create(module=modules[4], title="IELTS Speaking Part 2",
                exercise_type=ExerciseType.SPEAKING,
                prompt_text="Describe a place you have visited recently. You should say where it is, when you went, what you did, and why you enjoyed it. Speak for 1-2 minutes.",
                audio_prompt_url="https://mock.cdn/audio/ielts-part2.mp3",
                created_by=teachers[2]),
            Exercise.objects.create(module=modules[4], title="IELTS vocabulary quiz",
                exercise_type=ExerciseType.QUIZ,
                prompt_text="Provide synonyms for: ubiquitous, ameliorate, paradigm, scrutinize.",
                created_by=teachers[2]),
            Exercise.objects.create(module=modules[4], title="IELTS Writing Task 1 — Chart",
                exercise_type=ExerciseType.WRITING,
                prompt_text="The chart shows energy consumption by source from 2000-2020. Summarise the key trends in 150+ words.",
                created_by=teachers[2]),
            # NEW: a reading-comprehension exercise that uses content_text (passage)
            # + prompt_text (instructions). created_by = module owner.
            Exercise.objects.create(module=modules[1], title="Reading: A Day at the Market",
                exercise_type=ExerciseType.READING,
                prompt_text="Read the passage, then answer the comprehension questions.",
                content_text=(
                    "Every Saturday, Mai visits the local market near her home. She buys "
                    "fresh vegetables, ripe mangoes, and a loaf of warm bread. The vendors "
                    "know her by name and often save the best fruit for her. By nine o'clock "
                    "the market is crowded, so Mai always arrives early to avoid the rush."
                ),
                created_by=teachers[0]),
            Exercise.objects.create(module=modules[4], title="Reading: The Future of Work",
                exercise_type=ExerciseType.READING,
                prompt_text="Read the passage and answer the questions that follow.",
                content_text=(
                    "Remote work, once a rare privilege, has become a permanent fixture for "
                    "millions. Proponents cite flexibility and the elimination of commutes, "
                    "while critics warn of blurred boundaries between home and office. As "
                    "companies experiment with hybrid models, the definition of a 'workplace' "
                    "continues to evolve."
                ),
                created_by=teachers[2]),
            # NEW: listening exercises — stimulus = audio_prompt_url; transcript kept in
            # content_text for reference (the client may keep it hidden).
            Exercise.objects.create(module=modules[0], title="Listening: A Voicemail Message",
                exercise_type=ExerciseType.LISTENING,
                prompt_text="Listen to the voicemail and answer the questions. You may listen twice.",
                content_text=(
                    "Transcript — Hi Sarah, it's Mark from the dentist's office. I'm calling to "
                    "confirm your appointment on Thursday at 2 p.m. If you need to reschedule, "
                    "please call us back before Wednesday evening. Thank you!"
                ),
                audio_prompt_url="https://mock.cdn/audio/voicemail-dentist.mp3",
                created_by=teachers[1]),
            Exercise.objects.create(module=modules[4], title="Listening: Weather Forecast",
                exercise_type=ExerciseType.LISTENING,
                prompt_text="Listen to the weather forecast and answer the questions.",
                content_text=(
                    "Transcript — Good morning. Today will start cloudy with light rain in the "
                    "morning, clearing by the afternoon. Temperatures will reach a high of "
                    "eighteen degrees. Tomorrow looks sunny and warmer, so it's a good day for "
                    "outdoor plans."
                ),
                audio_prompt_url="https://mock.cdn/audio/weather-forecast.mp3",
                created_by=teachers[2]),
        ]

        # ---------- Questions & Options (receptive auto-graded items) ----------
        # Each question's answer key is the option(s) flagged is_correct;
        # Submission.grade() scores a student's `answers` ({question_id: [option_id,...]})
        # against that key. "Word filling" gap-fills are modeled as single-correct-option
        # questions so they auto-grade today (free-text fill-in needs backend support —
        # see english-learning-web/docs/READING_LISTENING_QUIZ_PLAN.md).
        self.stdout.write("Seeding questions & options…")

        def make_questions(exercise, specs):
            """specs: list of (text, [(option_text, is_correct), ...]). Returns [Question]."""
            created = []
            for q_order, (q_text, opts) in enumerate(specs, start=1):
                q = Question.objects.create(exercise=exercise, text=q_text, order=q_order)
                for o_order, (o_text, correct) in enumerate(opts, start=1):
                    QuestionOption.objects.create(
                        question=q, text=o_text, is_correct=correct, order=o_order)
                created.append(q)
            return created

        # exercises[15] = Reading: A Day at the Market (beginner)
        market_qs = make_questions(exercises[15], [
            ("How often does Mai visit the local market?", [
                ("Every Saturday", True),
                ("Every Sunday", False),
                ("Every morning", False),
                ("Once a month", False),
            ]),
            ("Why does Mai always arrive early?", [
                ("To meet her friends", False),
                ("To avoid the crowd", True),
                ("Because the market closes early", False),
                ("To get a discount", False),
            ]),
            ("True or False: The vendors do not recognise Mai.", [
                ("True", False),
                ("False", True),
            ]),
            # Word filling (gap-fill, choose the missing word)
            ("Fill the gap: Mai buys fresh vegetables, ripe ___, and a loaf of warm bread.", [
                ("mangoes", True),
                ("apples", False),
                ("potatoes", False),
                ("flowers", False),
            ]),
        ])

        # exercises[16] = Reading: The Future of Work (advanced)
        make_questions(exercises[16], [
            ("According to the passage, remote work has become:", [
                ("a rare privilege", False),
                ("a permanent fixture", True),
                ("an illegal practice", False),
                ("a temporary trend", False),
            ]),
            ("What do critics of remote work warn about?", [
                ("Higher salaries", False),
                ("Blurred boundaries between home and office", True),
                ("Too many commutes", False),
                ("A lack of technology", False),
            ]),
            ("Fill the gap: Companies are experimenting with ___ models.", [
                ("hybrid", True),
                ("ancient", False),
                ("broken", False),
                ("silent", False),
            ]),
        ])

        # exercises[17] = Listening: A Voicemail Message (beginner)
        make_questions(exercises[17], [
            ("Who is calling Sarah?", [
                ("Her doctor", False),
                ("Mark from the dentist's office", True),
                ("Her manager", False),
                ("A delivery driver", False),
            ]),
            ("When is the appointment?", [
                ("Wednesday at 2 p.m.", False),
                ("Thursday at 2 p.m.", True),
                ("Thursday at 10 a.m.", False),
                ("Friday at 2 p.m.", False),
            ]),
            ("True or False: Sarah must call back before Wednesday evening to reschedule.", [
                ("True", True),
                ("False", False),
            ]),
            ("Fill the gap: You should call back before ___ evening to reschedule.", [
                ("Wednesday", True),
                ("Thursday", False),
                ("Tuesday", False),
                ("Friday", False),
            ]),
        ])

        # exercises[18] = Listening: Weather Forecast (advanced)
        make_questions(exercises[18], [
            ("What is the weather like in the morning?", [
                ("Sunny and warm", False),
                ("Cloudy with light rain", True),
                ("Snowy", False),
                ("Foggy all day", False),
            ]),
            ("What is today's high temperature?", [
                ("Eight degrees", False),
                ("Eighteen degrees", True),
                ("Eighty degrees", False),
                ("Twenty-eight degrees", False),
            ]),
            ("Fill the gap: Tomorrow looks ___ and warmer.", [
                ("sunny", True),
                ("rainy", False),
                ("windy", False),
                ("cold", False),
            ]),
        ])

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

        # Receptive auto-graded demo submissions: build `answers` from the answer key and
        # call grade() to populate auto_score (shows the quiz scoring end-to-end).
        def answer_key(questions):
            return {
                str(q.id): [o.id for o in q.options.all() if o.is_correct]
                for q in questions
            }

        market_key = answer_key(market_qs)
        # Alice answers the market reading perfectly → 100.
        sub_full = Submission.objects.create(
            assignment=None, exercise=exercises[15], student=students[0],
            submission_type=SubmissionType.READING, answers=market_key)
        sub_full.grade()
        sub_full.save(update_fields=["auto_score"])
        # Bob gets two of four wrong (clears first + last question picks) → 50.
        market_partial = {qid: ids[:] for qid, ids in market_key.items()}
        q_ids = list(market_partial.keys())
        market_partial[q_ids[0]] = []
        market_partial[q_ids[-1]] = []
        sub_partial = Submission.objects.create(
            assignment=None, exercise=exercises[15], student=students[1],
            submission_type=SubmissionType.READING, answers=market_partial)
        sub_partial.grade()
        sub_partial.save(update_fields=["auto_score"])

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

        # Reflect grading lifecycle in submissions status
        graded_idx = [0, 1, 2, 4, 5, 9, 10, 13, 14, 17, 18, 19, 21, 24]
        for i in graded_idx:
            submissions[i].status = SubmissionStatus.GRADED
            submissions[i].save(update_fields=["status"])

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

        # ---------- Study Materials ----------
        self.stdout.write("Seeding study materials…")
        StudyMaterial.objects.bulk_create([
            StudyMaterial(
                title="English Grammar Handbook (PDF)",
                file_url="https://mock.cdn/docs/grammar-handbook.pdf",
                description="Comprehensive reference: tenses, articles, prepositions.",
                uploaded_by=admin),
            StudyMaterial(
                title="IELTS Writing Band Descriptors",
                file_url="https://mock.cdn/docs/ielts-writing-descriptors.pdf",
                description="Official band 5–9 criteria for Writing Task 1 & 2.",
                uploaded_by=admin),
            StudyMaterial(
                title="Common Phrasal Verbs Cheat Sheet",
                file_url="https://mock.cdn/docs/phrasal-verbs.pdf",
                description="Must-know phrasal verbs for everyday conversations.",
                uploaded_by=admin,
                klass=classes[0]),
        ])

        # ---------- AI Models ----------
        self.stdout.write("Seeding AI models…")
        AiModel.objects.create(
            model_name="GPT-4o English Evaluator",
            endpoint_url="https://api.openai.com/v1/chat/completions",
            version_identifier="gpt-4o-2024-05-13",
            is_active=True,
            updated_by=admin,
        )

        self.stdout.write(self.style.SUCCESS("\n=== Seed complete ==="))
        self.stdout.write(
            f"  Users:            {User.objects.count():>4}  (1 admin, 3 teachers, 12 students — 1 suspended)")
        self.stdout.write(f"  Classes:          {Class.objects.count():>4}")
        self.stdout.write(f"  Enrollments:      {ClassStudent.objects.count():>4}")
        self.stdout.write(f"  LessonPlans:      {LessonPlan.objects.count():>4}")
        self.stdout.write(f"  Modules:          {LearningModule.objects.count():>4}")
        self.stdout.write(f"  Exercises:        {Exercise.objects.count():>4}")
        self.stdout.write(f"  Questions:        {Question.objects.count():>4}")
        self.stdout.write(f"  Question Options: {QuestionOption.objects.count():>4}")
        self.stdout.write(f"  Assignments:      {Assignment.objects.count():>4}")
        self.stdout.write(f"  Submissions:      {Submission.objects.count():>4}")
        self.stdout.write(f"  Feedback:         {Feedback.objects.count():>4}")
        self.stdout.write(f"  Progress:         {StudentModuleProgress.objects.count():>4}")
        self.stdout.write(f"  Study Materials:  {StudyMaterial.objects.count():>4}")
        self.stdout.write(f"  AI Models:        {AiModel.objects.count():>4}")
        self.stdout.write(f"  System Logs:      {SystemLog.objects.count():>4}")
        self.stdout.write("\nLogin (password for everyone): password123")
        self.stdout.write("  Admin:    admin@english.app  (also Django /admin/ superuser)")
        self.stdout.write("  Teachers: emma/david/sophie .teacher@english.app")
        self.stdout.write("  Students: alice … luna .student@english.app")

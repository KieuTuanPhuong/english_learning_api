"""
Seed script for the English Learning API demo database.

Usage:
    ./venv/bin/python -m app.seed

Wipes all rows then inserts a realistic demo dataset:
  - 1 admin, 3 teachers, 12 students  (password for all: "password123")
  - 4 classes with enrollments + lesson plans
  - 5 learning modules with 15 exercises
  - 6 assignments, ~24 submissions, ~14 feedback entries, progress rows
"""

from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from sqlalchemy import text

from .database import SessionLocal, engine, Base
from . import models  # noqa: F401  ensure models register
from .models import (
    User,
    UserRole,
    UserStatus,
    Class,
    ClassStudent,
    LessonPlan,
    LearningModule,
    DifficultyLevel,
    Exercise,
    ExerciseType,
    Assignment,
    Submission,
    SubmissionType,
    Feedback,
    StudentModuleProgress,
)
from .auth import hash_password


TABLES_IN_FK_ORDER = [
    "feedback",
    "submissions",
    "assignments",
    "student_module_progress",
    "exercises",
    "learning_modules",
    "lesson_plans",
    "class_students_lnk",
    "classes",
    "users",
]


def wipe(db):
    print("Wiping existing data…")
    db.execute(
        text(f"TRUNCATE {', '.join(TABLES_IN_FK_ORDER)} RESTART IDENTITY CASCADE;")
    )
    db.commit()


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        wipe(db)

        # ---------- Users ----------
        print("Seeding users…")
        pw = hash_password("password123")

        admin = User(
            email="admin@english.app",
            password_hash=pw,
            full_name="Site Admin",
            role=UserRole.admin,
            status=UserStatus.active,
        )

        teachers_data = [
            ("emma.teacher@english.app", "Emma Carter"),
            ("david.teacher@english.app", "David Nguyen"),
            ("sophie.teacher@english.app", "Sophie Tran"),
        ]
        teachers = [
            User(
                email=e,
                password_hash=pw,
                full_name=n,
                role=UserRole.teacher,
                status=UserStatus.active,
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
        # one suspended student to exercise the status flow
        students = []
        for i, (e, n) in enumerate(students_data):
            status = UserStatus.suspended if i == 11 else UserStatus.active
            students.append(
                User(
                    email=e,
                    password_hash=pw,
                    full_name=n,
                    role=UserRole.student,
                    status=status,
                )
            )

        db.add(admin)
        db.add_all(teachers)
        db.add_all(students)
        db.commit()
        for u in [admin, *teachers, *students]:
            db.refresh(u)

        # ---------- Classes ----------
        print("Seeding classes…")
        classes = [
            Class(
                class_name="English 101 — Beginners",
                teacher_id=teachers[0].id,
                academic_year="2025-2026",
            ),
            Class(
                class_name="Intermediate Writing",
                teacher_id=teachers[0].id,
                academic_year="2025-2026",
            ),
            Class(
                class_name="Conversational English",
                teacher_id=teachers[1].id,
                academic_year="2025-2026",
            ),
            Class(
                class_name="IELTS Prep — Advanced",
                teacher_id=teachers[2].id,
                academic_year="2025-2026",
            ),
        ]
        db.add_all(classes)
        db.commit()
        for c in classes:
            db.refresh(c)

        # ---------- Enrollments ----------
        print("Seeding enrollments…")
        enroll_plan = {
            0: students[0:6],
            1: students[2:8],
            2: students[4:10],
            3: students[6:11],
        }
        for class_idx, roster in enroll_plan.items():
            for s in roster:
                db.add(ClassStudent(class_id=classes[class_idx].id, student_id=s.id))
        db.commit()

        # ---------- Lesson Plans ----------
        print("Seeding lesson plans…")
        today = date.today()
        lesson_plans = [
            LessonPlan(
                class_id=classes[0].id,
                title="Week 1 — Greetings & Self-Intro",
                objectives="Learn basic greetings, introduce yourself, ask about others.",
                start_date=today,
                end_date=today + timedelta(days=7),
            ),
            LessonPlan(
                class_id=classes[1].id,
                title="Unit 3 — Persuasive Essays",
                objectives="Structure an argument, use evidence, write a 5-paragraph essay.",
                start_date=today + timedelta(days=3),
                end_date=today + timedelta(days=17),
            ),
            LessonPlan(
                class_id=classes[3].id,
                title="IELTS Writing Task 2 — Opinion Essays",
                objectives="Master opinion essay structure for Band 7+.",
                start_date=today,
                end_date=today + timedelta(days=14),
            ),
        ]
        db.add_all(lesson_plans)
        db.commit()

        # ---------- Learning Modules ----------
        print("Seeding learning modules…")
        modules = [
            LearningModule(
                title="Everyday Conversations",
                description="Real-life dialogues for ordering food, asking directions, small talk.",
                difficulty_level=DifficultyLevel.beginner,
                created_by=teachers[1].id,
            ),
            LearningModule(
                title="Grammar Foundations",
                description="Tenses, articles, prepositions explained with practice.",
                difficulty_level=DifficultyLevel.beginner,
                created_by=teachers[0].id,
            ),
            LearningModule(
                title="Academic Writing",
                description="Essay structure, citations, formal register, transitions.",
                difficulty_level=DifficultyLevel.intermediate,
                created_by=teachers[0].id,
            ),
            LearningModule(
                title="Pronunciation Workshop",
                description="Vowel sounds, intonation, common mispronunciations.",
                difficulty_level=DifficultyLevel.intermediate,
                created_by=teachers[1].id,
            ),
            LearningModule(
                title="IELTS Mastery",
                description="Full IELTS prep: Reading, Writing, Listening, Speaking strategies.",
                difficulty_level=DifficultyLevel.advanced,
                created_by=teachers[2].id,
            ),
        ]
        db.add_all(modules)
        db.commit()
        for m in modules:
            db.refresh(m)

        # ---------- Exercises ----------
        print("Seeding exercises…")
        exercises = [
            Exercise(
                module_id=modules[0].id,
                title="Ordering at a cafe",
                exercise_type=ExerciseType.speaking,
                prompt_text="Record yourself ordering a coffee and a pastry.",
                audio_prompt_url="https://mock.cdn/audio/cafe-order.mp3",
            ),
            Exercise(
                module_id=modules[0].id,
                title="Asking for directions",
                exercise_type=ExerciseType.writing,
                prompt_text="Write a short dialogue asking a stranger for directions to the train station.",
            ),
            Exercise(
                module_id=modules[0].id,
                title="Greetings quiz",
                exercise_type=ExerciseType.quiz,
                prompt_text="Which greeting is most appropriate in a business meeting? Explain why.",
            ),
            Exercise(
                module_id=modules[1].id,
                title="Past simple vs present perfect",
                exercise_type=ExerciseType.writing,
                prompt_text="Write 5 sentences contrasting past simple and present perfect tense.",
            ),
            Exercise(
                module_id=modules[1].id,
                title="Articles practice",
                exercise_type=ExerciseType.quiz,
                prompt_text="Fill in the blanks: ___ apple, ___ honest person, ___ university.",
            ),
            Exercise(
                module_id=modules[1].id,
                title="Preposition story",
                exercise_type=ExerciseType.writing,
                prompt_text="Write a short story (100 words) using 'in', 'on', 'at' correctly.",
            ),
            Exercise(
                module_id=modules[2].id,
                title="Thesis statement workshop",
                exercise_type=ExerciseType.writing,
                prompt_text="Write three different thesis statements arguing about social media's impact on teens.",
            ),
            Exercise(
                module_id=modules[2].id,
                title="Persuasive essay",
                exercise_type=ExerciseType.writing,
                prompt_text="Write a 5-paragraph persuasive essay (~400 words) on: 'Should schools require uniforms?'",
            ),
            Exercise(
                module_id=modules[2].id,
                title="Transitions quiz",
                exercise_type=ExerciseType.quiz,
                prompt_text="Name 5 transition words for contrast and 5 for cause/effect.",
            ),
            Exercise(
                module_id=modules[3].id,
                title="Minimal pairs: ship vs sheep",
                exercise_type=ExerciseType.speaking,
                prompt_text="Record yourself saying these word pairs: ship/sheep, bit/beat, fit/feet.",
                audio_prompt_url="https://mock.cdn/audio/minimal-pairs.mp3",
            ),
            Exercise(
                module_id=modules[3].id,
                title="Intonation practice",
                exercise_type=ExerciseType.speaking,
                prompt_text="Read the provided paragraph aloud, paying attention to falling intonation on statements.",
                audio_prompt_url="https://mock.cdn/audio/intonation.mp3",
            ),
            Exercise(
                module_id=modules[4].id,
                title="IELTS Writing Task 2 — Opinion",
                exercise_type=ExerciseType.writing,
                prompt_text="Some people believe technology has made us less social. To what extent do you agree? Write 250+ words.",
            ),
            Exercise(
                module_id=modules[4].id,
                title="IELTS Speaking Part 2",
                exercise_type=ExerciseType.speaking,
                prompt_text="Describe a place you have visited recently. You should say where it is, when you went, what you did, and why you enjoyed it. Speak for 1-2 minutes.",
                audio_prompt_url="https://mock.cdn/audio/ielts-part2.mp3",
            ),
            Exercise(
                module_id=modules[4].id,
                title="IELTS vocabulary quiz",
                exercise_type=ExerciseType.quiz,
                prompt_text="Provide synonyms for: ubiquitous, ameliorate, paradigm, scrutinize.",
            ),
            Exercise(
                module_id=modules[4].id,
                title="IELTS Writing Task 1 — Chart",
                exercise_type=ExerciseType.writing,
                prompt_text="The chart shows energy consumption by source from 2000-2020. Summarise the key trends in 150+ words.",
            ),
        ]
        db.add_all(exercises)
        db.commit()
        for e in exercises:
            db.refresh(e)

        # ---------- Assignments ----------
        print("Seeding assignments…")
        now = datetime.now(timezone.utc)
        assignments = [
            Assignment(
                class_id=classes[0].id,
                exercise_id=exercises[0].id,
                assigned_by=teachers[0].id,
                due_date=now + timedelta(days=3),
            ),
            Assignment(
                class_id=classes[0].id,
                exercise_id=exercises[2].id,
                assigned_by=teachers[0].id,
                due_date=now + timedelta(days=7),
            ),
            Assignment(
                class_id=classes[1].id,
                exercise_id=exercises[7].id,
                assigned_by=teachers[0].id,
                due_date=now + timedelta(days=10),
            ),
            Assignment(
                class_id=classes[2].id,
                exercise_id=exercises[1].id,
                assigned_by=teachers[1].id,
                due_date=now + timedelta(days=5),
            ),
            Assignment(
                class_id=classes[3].id,
                exercise_id=exercises[11].id,
                assigned_by=teachers[2].id,
                due_date=now + timedelta(days=14),
            ),
            Assignment(
                class_id=classes[3].id,
                exercise_id=exercises[12].id,
                assigned_by=teachers[2].id,
                due_date=now + timedelta(days=14),
            ),
        ]
        db.add_all(assignments)
        db.commit()
        for a in assignments:
            db.refresh(a)

        # ---------- Submissions ----------
        print("Seeding submissions…")
        submissions = []

        for s in students[0:4]:
            submissions.append(
                Submission(
                    assignment_id=assignments[0].id,
                    exercise_id=exercises[0].id,
                    student_id=s.id,
                    submission_type=SubmissionType.speaking,
                    audio_recording_url=f"https://mock.cdn/recordings/{s.email}-cafe.m4a",
                )
            )
        for s in students[0:5]:
            submissions.append(
                Submission(
                    assignment_id=assignments[1].id,
                    exercise_id=exercises[2].id,
                    student_id=s.id,
                    submission_type=SubmissionType.writing,
                    writing_text="'Good morning' is most appropriate — formal yet warm and not time-restrictive within work hours.",
                )
            )
        essay_text = (
            "School uniforms reduce socio-economic disparity by removing visible markers "
            "of wealth in the classroom. They also simplify morning routines and create a "
            "sense of belonging. However, critics argue uniforms suppress self-expression. "
            "On balance, the unifying effect outweighs the cost: a shared identity helps "
            "students focus on learning rather than appearance."
        )
        for s in students[2:6]:
            submissions.append(
                Submission(
                    assignment_id=assignments[2].id,
                    exercise_id=exercises[7].id,
                    student_id=s.id,
                    submission_type=SubmissionType.writing,
                    writing_text=essay_text,
                )
            )
        for s in students[4:8]:
            submissions.append(
                Submission(
                    assignment_id=assignments[3].id,
                    exercise_id=exercises[1].id,
                    student_id=s.id,
                    submission_type=SubmissionType.writing,
                    writing_text=(
                        "A: Excuse me, could you tell me how to get to the train station?\n"
                        "B: Sure! Walk two blocks straight, then turn left at the bakery. It's on your right.\n"
                        "A: Thank you so much!"
                    ),
                )
            )
        ielts_essay = (
            "Modern technology has fundamentally reshaped human interaction. While it is true "
            "that constant connectivity through smartphones can reduce face-to-face contact, I "
            "largely disagree that we have become less social overall. Platforms enable us to "
            "maintain relationships across continents and join communities aligned with niche "
            "interests, which was previously impossible…"
        )
        for s in students[6:10]:
            submissions.append(
                Submission(
                    assignment_id=assignments[4].id,
                    exercise_id=exercises[11].id,
                    student_id=s.id,
                    submission_type=SubmissionType.writing,
                    writing_text=ielts_essay,
                )
            )
        for s in students[6:9]:
            submissions.append(
                Submission(
                    assignment_id=assignments[5].id,
                    exercise_id=exercises[12].id,
                    student_id=s.id,
                    submission_type=SubmissionType.speaking,
                    audio_recording_url=f"https://mock.cdn/recordings/{s.email}-ielts-part2.m4a",
                )
            )

        submissions.append(
            Submission(
                assignment_id=None,
                exercise_id=exercises[3].id,
                student_id=students[0].id,
                submission_type=SubmissionType.writing,
                writing_text="I lived in Hanoi for five years. I have visited Hanoi twice this year.",
            )
        )
        submissions.append(
            Submission(
                assignment_id=None,
                exercise_id=exercises[9].id,
                student_id=students[1].id,
                submission_type=SubmissionType.speaking,
                audio_recording_url="https://mock.cdn/recordings/bob-pronunciation.m4a",
            )
        )

        db.add_all(submissions)
        db.commit()
        for s in submissions:
            db.refresh(s)

        # ---------- Feedback ----------
        print("Seeding feedback…")
        feedback_entries = [
            Feedback(
                submission_id=submissions[0].id,
                reviewer_id=teachers[0].id,
                score=Decimal("85.00"),
                comments="Clear pronunciation. Slow down on 'pastry'.",
            ),
            Feedback(
                submission_id=submissions[1].id,
                reviewer_id=teachers[0].id,
                score=Decimal("78.00"),
                comments="Good vocabulary. Work on intonation at end of questions.",
            ),
            Feedback(
                submission_id=submissions[2].id,
                reviewer_id=teachers[0].id,
                score=Decimal("92.00"),
                comments="Excellent — natural and confident.",
            ),
            Feedback(
                submission_id=submissions[4].id,
                reviewer_id=teachers[0].id,
                score=Decimal("88.00"),
                comments="Correct, with thoughtful reasoning.",
            ),
            Feedback(
                submission_id=submissions[5].id,
                reviewer_id=teachers[0].id,
                score=Decimal("80.00"),
                comments="Right answer; could expand the explanation.",
            ),
            Feedback(
                submission_id=submissions[9].id,
                reviewer_id=teachers[0].id,
                score=Decimal("84.00"),
                comments="Solid thesis. Add more concrete examples in body paragraph 2.",
            ),
            Feedback(
                submission_id=submissions[10].id,
                reviewer_id=teachers[0].id,
                score=Decimal("77.00"),
                comments="Watch comma splices in paragraph 3.",
            ),
            Feedback(
                submission_id=submissions[13].id,
                reviewer_id=teachers[1].id,
                score=Decimal("90.00"),
                comments="Perfect — natural and polite.",
            ),
            Feedback(
                submission_id=submissions[14].id,
                reviewer_id=teachers[1].id,
                score=Decimal("82.00"),
                comments="Good, but try varying the phrasing.",
            ),
            Feedback(
                submission_id=submissions[17].id,
                reviewer_id=teachers[2].id,
                score=Decimal("75.00"),
                comments="Band 6.5. Develop counter-arguments more thoroughly.",
            ),
            Feedback(
                submission_id=submissions[18].id,
                reviewer_id=teachers[2].id,
                score=Decimal("82.00"),
                comments="Band 7. Strong introduction; conclusion could be sharper.",
            ),
            Feedback(
                submission_id=submissions[19].id,
                reviewer_id=teachers[2].id,
                score=Decimal("88.00"),
                comments="Band 7.5. Excellent range of vocabulary.",
            ),
            Feedback(
                submission_id=submissions[21].id,
                reviewer_id=teachers[2].id,
                score=Decimal("80.00"),
                comments="Band 7. Good fluency; reduce hesitation markers.",
            ),
            Feedback(
                submission_id=submissions[24].id,
                reviewer_id=teachers[0].id,
                score=Decimal("70.00"),
                comments="Second sentence — well done.",
            ),
        ]
        db.add_all(feedback_entries)
        db.commit()

        # ---------- Progress ----------
        print("Seeding progress…")
        progress = [
            StudentModuleProgress(
                student_id=students[0].id,
                module_id=modules[0].id,
                completion_percentage=Decimal("65.00"),
                last_accessed_at=now,
            ),
            StudentModuleProgress(
                student_id=students[0].id,
                module_id=modules[1].id,
                completion_percentage=Decimal("40.00"),
                last_accessed_at=now - timedelta(days=2),
            ),
            StudentModuleProgress(
                student_id=students[1].id,
                module_id=modules[3].id,
                completion_percentage=Decimal("25.00"),
                last_accessed_at=now - timedelta(days=1),
            ),
            StudentModuleProgress(
                student_id=students[2].id,
                module_id=modules[2].id,
                completion_percentage=Decimal("55.00"),
                last_accessed_at=now,
            ),
            StudentModuleProgress(
                student_id=students[3].id,
                module_id=modules[2].id,
                completion_percentage=Decimal("80.00"),
                last_accessed_at=now,
            ),
            StudentModuleProgress(
                student_id=students[6].id,
                module_id=modules[4].id,
                completion_percentage=Decimal("45.00"),
                last_accessed_at=now,
            ),
            StudentModuleProgress(
                student_id=students[7].id,
                module_id=modules[4].id,
                completion_percentage=Decimal("60.00"),
                last_accessed_at=now - timedelta(hours=6),
            ),
            StudentModuleProgress(
                student_id=students[8].id,
                module_id=modules[4].id,
                completion_percentage=Decimal("100.00"),
                last_accessed_at=now - timedelta(days=1),
            ),
        ]
        db.add_all(progress)
        db.commit()

        print("\n=== Seed complete ===")
        print(
            f"  Users:        {db.query(User).count():>4}  "
            f"(1 admin, 3 teachers, 12 students — 1 suspended)"
        )
        print(f"  Classes:      {db.query(Class).count():>4}")
        print(f"  Enrollments:  {db.query(ClassStudent).count():>4}")
        print(f"  LessonPlans:  {db.query(LessonPlan).count():>4}")
        print(f"  Modules:      {db.query(LearningModule).count():>4}")
        print(f"  Exercises:    {db.query(Exercise).count():>4}")
        print(f"  Assignments:  {db.query(Assignment).count():>4}")
        print(f"  Submissions:  {db.query(Submission).count():>4}")
        print(f"  Feedback:     {db.query(Feedback).count():>4}")
        print(f"  Progress:     {db.query(StudentModuleProgress).count():>4}")
        print("\nLogin credentials (password for everyone): password123")
        print("  Admin:    admin@english.app")
        print(
            "  Teachers: emma.teacher@english.app, david.teacher@english.app, sophie.teacher@english.app"
        )
        print("  Students: alice.student@english.app … luna.student@english.app")
    finally:
        db.close()


if __name__ == "__main__":
    seed()

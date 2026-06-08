"""Domain models, ported from the FastAPI/SQLAlchemy app.

Note on naming: a foreign key to ``Class`` is named ``klass`` on the model
(``class`` is a Python keyword). Serializers expose it to the API as
``class_id`` to keep the wire format unchanged.
"""

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models


# ---------- Enums ----------
class UserRole(models.TextChoices):
    STUDENT = "student", "Student"
    TEACHER = "teacher", "Teacher"
    ADMIN = "admin", "Admin"


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    INACTIVE = "inactive", "Inactive"


class DifficultyLevel(models.TextChoices):
    BEGINNER = "beginner", "Beginner"
    INTERMEDIATE = "intermediate", "Intermediate"
    ADVANCED = "advanced", "Advanced"


class ExerciseType(models.TextChoices):
    # Productive skills: open-ended output, graded subjectively via Feedback.
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"
    # Receptive skills: consume a stimulus, answer fixed questions, auto-graded.
    READING = "reading", "Reading"        # stimulus = prompt_text (passage)
    LISTENING = "listening", "Listening"  # stimulus = audio_prompt_url (clip)
    QUIZ = "quiz", "Quiz"                  # no stimulus (vocab/grammar)


class SubmissionType(models.TextChoices):
    # Productive: payload in writing_text / audio_recording_url.
    WRITING = "writing", "Writing"
    SPEAKING = "speaking", "Speaking"
    # Receptive: payload in `answers` JSON, auto-scored into `auto_score`.
    READING = "reading", "Reading"
    LISTENING = "listening", "Listening"
    QUIZ = "quiz", "Quiz"


# ---------- User ----------
class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", UserRole.ADMIN)
        extra.setdefault("status", UserStatus.ACTIVE)
        extra.setdefault("full_name", "Admin")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra["is_staff"] is not True or extra["is_superuser"] is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, max_length=255, db_index=True)
    full_name = models.CharField(max_length=100)
    avatar_url = models.CharField(max_length=500, blank=True, null=True)
    role = models.CharField(max_length=20, choices=UserRole.choices)
    status = models.CharField(
        max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE
    )
    # Django auth plumbing — `status` drives app logic; is_active gates auth backend.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name", "role"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


# ---------- Class ----------
class Class(models.Model):
    class_name = models.CharField(max_length=100)
    teacher = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="classes_taught",
    )
    academic_year = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "classes"
        verbose_name_plural = "classes"

    def __str__(self):
        return self.class_name


class ClassStudent(models.Model):
    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="students"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="class_memberships"
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "class_students_lnk"
        unique_together = (("klass", "student"),)

    def __str__(self):
        return f"{self.student_id} in {self.klass_id}"


class LessonPlan(models.Model):
    klass = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="lesson_plans"
    )
    title = models.CharField(max_length=255)
    objectives = models.TextField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lesson_plans"

    def __str__(self):
        return self.title


# ---------- Learning Module ----------
class LearningModule(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    difficulty_level = models.CharField(
        max_length=20, choices=DifficultyLevel.choices, null=True, blank=True
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="modules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learning_modules"

    def __str__(self):
        return self.title


class StudentModuleProgress(models.Model):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="module_progress"
    )
    module = models.ForeignKey(
        LearningModule, on_delete=models.CASCADE, related_name="progress"
    )
    completion_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "student_module_progress"
        unique_together = (("student", "module"),)

    def __str__(self):
        return f"{self.student_id}/{self.module_id}: {self.completion_percentage}%"


class Exercise(models.Model):
    module = models.ForeignKey(
        LearningModule, null=True, blank=True, on_delete=models.CASCADE,
        related_name="exercises",
    )
    title = models.CharField(max_length=255)
    exercise_type = models.CharField(max_length=20, choices=ExerciseType.choices)
    prompt_text = models.TextField()
    audio_prompt_url = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exercises"

    def __str__(self):
        return self.title


class Question(models.Model):
    """A single question on a receptive exercise (reading/listening/quiz).

    The stimulus (reading passage / listening audio) lives on the parent
    ``Exercise`` (``prompt_text`` / ``audio_prompt_url``); each Question hangs
    off that stimulus and carries its own options + answer key.
    """

    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="questions"
    )
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "questions"
        ordering = ["order", "id"]

    def __str__(self):
        return f"Q{self.order} of exercise {self.exercise_id}"


class QuestionOption(models.Model):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options"
    )
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "question_options"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.text} ({'correct' if self.is_correct else 'wrong'})"


class Assignment(models.Model):
    klass = models.ForeignKey(
        Class, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assignments",
    )
    exercise = models.ForeignKey(
        Exercise, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assignments",
    )
    assigned_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assignments_made",
    )
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assignments"

    def __str__(self):
        return f"Assignment {self.pk}"


class Submission(models.Model):
    assignment = models.ForeignKey(
        Assignment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="submissions",
    )
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="submissions"
    )
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="submissions"
    )
    submission_type = models.CharField(max_length=20, choices=SubmissionType.choices)
    # Productive payload (writing/speaking).
    writing_text = models.TextField(null=True, blank=True)
    audio_recording_url = models.CharField(max_length=500, null=True, blank=True)
    # Receptive payload (reading/listening/quiz): {question_id: [option_id, ...]}.
    answers = models.JSONField(null=True, blank=True)
    auto_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "submissions"

    def __str__(self):
        return f"Submission {self.pk} by {self.student_id}"

    def grade(self):
        """Auto-grade a receptive submission from ``answers`` against the
        exercise's question answer key. Sets and returns ``auto_score`` as a
        percentage (0–100), or ``None`` if the exercise has no questions.

        ``answers`` shape: ``{question_id: [selected_option_id, ...]}``. A
        question counts as correct when the selected option set exactly matches
        the set of options flagged ``is_correct`` (handles multi-answer items).
        """
        questions = list(
            self.exercise.questions.prefetch_related("options")
        )
        if not questions:
            self.auto_score = None
            return None

        answers = self.answers or {}
        correct = 0
        for q in questions:
            key_set = {o.id for o in q.options.all() if o.is_correct}
            picked = answers.get(str(q.id), answers.get(q.id, []))
            if set(picked) == key_set:
                correct += 1

        from decimal import Decimal, ROUND_HALF_UP

        self.auto_score = (
            Decimal(100 * correct) / Decimal(len(questions))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.auto_score


class Feedback(models.Model):
    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="feedback"
    )
    reviewer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="feedback_given",
    )
    score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    comments = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feedback"
        verbose_name_plural = "feedback"

    def __str__(self):
        return f"Feedback {self.pk} on {self.submission_id}"

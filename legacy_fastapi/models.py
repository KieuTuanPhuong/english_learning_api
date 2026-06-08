import enum
from datetime import datetime
from sqlalchemy import (
    String,
    Integer,
    ForeignKey,
    Text,
    DateTime,
    Date,
    Numeric,
    Enum as SAEnum,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class UserRole(str, enum.Enum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class UserStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    inactive = "inactive"


class DifficultyLevel(str, enum.Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class ExerciseType(str, enum.Enum):
    writing = "writing"
    speaking = "speaking"
    quiz = "quiz"


class SubmissionType(str, enum.Enum):
    writing = "writing"
    speaking = "speaking"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status"),
        default=UserStatus.active,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    classes_taught: Mapped[list["Class"]] = relationship(
        back_populates="teacher", foreign_keys="Class.teacher_id"
    )
    class_memberships: Mapped[list["ClassStudent"]] = relationship(
        back_populates="student"
    )
    module_progress: Mapped[list["StudentModuleProgress"]] = relationship(
        back_populates="student"
    )
    submissions: Mapped[list["Submission"]] = relationship(back_populates="student")


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_name: Mapped[str] = mapped_column(String(100), nullable=False)
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    academic_year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    teacher: Mapped["User | None"] = relationship(
        back_populates="classes_taught", foreign_keys=[teacher_id]
    )
    students: Mapped[list["ClassStudent"]] = relationship(
        back_populates="class_", cascade="all, delete-orphan"
    )
    lesson_plans: Mapped[list["LessonPlan"]] = relationship(
        back_populates="class_", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="class_")


class ClassStudent(Base):
    __tablename__ = "class_students_lnk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    class_: Mapped["Class"] = relationship(back_populates="students")
    student: Mapped["User"] = relationship(back_populates="class_memberships")


class LessonPlan(Base):
    __tablename__ = "lesson_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objectives: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    class_: Mapped["Class"] = relationship(back_populates="lesson_plans")


class LearningModule(Base):
    __tablename__ = "learning_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty_level: Mapped[DifficultyLevel | None] = mapped_column(
        SAEnum(DifficultyLevel, name="difficulty_level"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    exercises: Mapped[list["Exercise"]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    progress: Mapped[list["StudentModuleProgress"]] = relationship(
        back_populates="module"
    )


class StudentModuleProgress(Base):
    __tablename__ = "student_module_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("learning_modules.id"), nullable=False
    )
    completion_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0.00, nullable=False
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    student: Mapped["User"] = relationship(back_populates="module_progress")
    module: Mapped["LearningModule"] = relationship(back_populates="progress")


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_modules.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    exercise_type: Mapped[ExerciseType] = mapped_column(
        SAEnum(ExerciseType, name="exercise_type"), nullable=False
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_prompt_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    module: Mapped["LearningModule | None"] = relationship(back_populates="exercises")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="exercise")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="exercise")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("classes.id"), nullable=True
    )
    exercise_id: Mapped[int | None] = mapped_column(
        ForeignKey("exercises.id"), nullable=True
    )
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    class_: Mapped["Class | None"] = relationship(back_populates="assignments")
    exercise: Mapped["Exercise | None"] = relationship(back_populates="assignments")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="assignment")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id"), nullable=True
    )
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    submission_type: Mapped[SubmissionType] = mapped_column(
        SAEnum(SubmissionType, name="submission_type"), nullable=False
    )
    writing_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    assignment: Mapped["Assignment | None"] = relationship(back_populates="submissions")
    exercise: Mapped["Exercise"] = relationship(back_populates="submissions")
    student: Mapped["User"] = relationship(back_populates="submissions")
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id"), nullable=False
    )
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    submission: Mapped["Submission"] = relationship(back_populates="feedback")

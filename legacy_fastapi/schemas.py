from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from .models import UserRole, UserStatus, DifficultyLevel, ExerciseType, SubmissionType


# ---------- Auth / User ----------
class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=100)
    avatar_url: str | None = None
    role: UserRole


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=128)


class UserUpdate(BaseModel):
    full_name: str | None = None
    avatar_url: str | None = None
    status: UserStatus | None = None


class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: UserStatus
    created_at: datetime
    updated_at: datetime


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Class ----------
class ClassBase(BaseModel):
    class_name: str = Field(min_length=1, max_length=100)
    teacher_id: int | None = None
    academic_year: str | None = None


class ClassCreate(ClassBase):
    pass


class ClassUpdate(BaseModel):
    class_name: str | None = None
    teacher_id: int | None = None
    academic_year: str | None = None


class ClassOut(ClassBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class ClassStudentLink(BaseModel):
    student_id: int


class ClassStudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    class_id: int
    student_id: int
    joined_at: datetime


# ---------- Lesson Plan ----------
class LessonPlanBase(BaseModel):
    class_id: int
    title: str = Field(min_length=1, max_length=255)
    objectives: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class LessonPlanCreate(LessonPlanBase):
    pass


class LessonPlanOut(LessonPlanBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------- Learning Module ----------
class LearningModuleBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    difficulty_level: DifficultyLevel | None = None


class LearningModuleCreate(LearningModuleBase):
    pass


class LearningModuleUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    difficulty_level: DifficultyLevel | None = None


class LearningModuleOut(LearningModuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_by: int | None
    created_at: datetime


class ProgressUpsert(BaseModel):
    module_id: int
    completion_percentage: Decimal = Field(ge=0, le=100)


class ProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    student_id: int
    module_id: int
    completion_percentage: Decimal
    last_accessed_at: datetime | None


# ---------- Exercise ----------
class ExerciseBase(BaseModel):
    module_id: int | None = None
    title: str = Field(min_length=1, max_length=255)
    exercise_type: ExerciseType
    prompt_text: str
    audio_prompt_url: str | None = None


class ExerciseCreate(ExerciseBase):
    pass


class ExerciseOut(ExerciseBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------- Assignment ----------
class AssignmentBase(BaseModel):
    class_id: int | None = None
    exercise_id: int | None = None
    due_date: datetime | None = None


class AssignmentCreate(AssignmentBase):
    pass


class AssignmentOut(AssignmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    assigned_by: int | None
    created_at: datetime


# ---------- Submission ----------
class SubmissionBase(BaseModel):
    exercise_id: int
    assignment_id: int | None = None
    submission_type: SubmissionType
    writing_text: str | None = None
    audio_recording_url: str | None = None


class SubmissionCreate(SubmissionBase):
    pass


class SubmissionOut(SubmissionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    student_id: int
    submitted_at: datetime


# ---------- Feedback ----------
class FeedbackBase(BaseModel):
    submission_id: int
    score: Decimal | None = Field(default=None, ge=0, le=100)
    comments: str | None = None


class FeedbackCreate(FeedbackBase):
    pass


class FeedbackOut(FeedbackBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reviewer_id: int | None
    created_at: datetime

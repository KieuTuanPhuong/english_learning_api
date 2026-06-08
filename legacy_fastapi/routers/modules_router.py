from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..models import (
    LearningModule,
    Exercise,
    Submission,
    Feedback,
    StudentModuleProgress,
    User,
    UserRole,
)
from ..auth import get_current_user, require_roles

router = APIRouter(prefix="/modules", tags=["modules"])


# ---------- Learning Modules ----------
@router.post("/", response_model=schemas.LearningModuleOut, status_code=201)
def create_module(
    payload: schemas.LearningModuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    obj = LearningModule(
        title=payload.title,
        description=payload.description,
        difficulty_level=payload.difficulty_level,
        created_by=current_user.id,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/", response_model=list[schemas.LearningModuleOut])
def list_modules(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.scalars(select(LearningModule).offset(skip).limit(limit)).all()


@router.get("/{module_id}", response_model=schemas.LearningModuleOut)
def get_module(
    module_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    obj = db.get(LearningModule, module_id)
    if not obj:
        raise HTTPException(404, "Module not found")
    return obj


@router.put("/{module_id}", response_model=schemas.LearningModuleOut)
def update_module(
    module_id: int,
    payload: schemas.LearningModuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    obj = db.get(LearningModule, module_id)
    if not obj:
        raise HTTPException(404, "Module not found")
    if current_user.role == UserRole.teacher and obj.created_by != current_user.id:
        raise HTTPException(403, "Not your module")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{module_id}", status_code=204)
def delete_module(
    module_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.admin)),
):
    obj = db.get(LearningModule, module_id)
    if not obj:
        raise HTTPException(404, "Module not found")
    db.delete(obj)
    db.commit()
    return None


# ---------- Exercises ----------
@router.post(
    "/{module_id}/exercises", response_model=schemas.ExerciseOut, status_code=201
)
def create_exercise(
    module_id: int,
    payload: schemas.ExerciseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    module = db.get(LearningModule, module_id)
    if not module:
        raise HTTPException(404, "Module not found")
    if current_user.role == UserRole.teacher and module.created_by != current_user.id:
        raise HTTPException(403, "Not your module")
    obj = Exercise(
        module_id=module_id,
        title=payload.title,
        exercise_type=payload.exercise_type,
        prompt_text=payload.prompt_text,
        audio_prompt_url=payload.audio_prompt_url,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{module_id}/exercises", response_model=list[schemas.ExerciseOut])
def list_exercises(
    module_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return db.scalars(select(Exercise).where(Exercise.module_id == module_id)).all()


@router.get("/exercises/{exercise_id}", response_model=schemas.ExerciseOut)
def get_exercise(
    exercise_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    obj = db.get(Exercise, exercise_id)
    if not obj:
        raise HTTPException(404, "Exercise not found")
    return obj


@router.delete("/exercises/{exercise_id}", status_code=204)
def delete_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    obj = db.get(Exercise, exercise_id)
    if not obj:
        raise HTTPException(404, "Exercise not found")
    db.delete(obj)
    db.commit()
    return None


# ---------- Submissions ----------
@router.post("/submissions", response_model=schemas.SubmissionOut, status_code=201)
def submit(
    payload: schemas.SubmissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.student)),
):
    exercise = db.get(Exercise, payload.exercise_id)
    if not exercise:
        raise HTTPException(404, "Exercise not found")
    obj = Submission(
        student_id=current_user.id,
        exercise_id=payload.exercise_id,
        assignment_id=payload.assignment_id,
        submission_type=payload.submission_type,
        writing_text=payload.writing_text,
        audio_recording_url=payload.audio_recording_url,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/submissions/me", response_model=list[schemas.SubmissionOut])
def list_my_submissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.scalars(
        select(Submission).where(Submission.student_id == current_user.id)
    ).all()


@router.get(
    "/exercises/{exercise_id}/submissions", response_model=list[schemas.SubmissionOut]
)
def list_exercise_submissions(
    exercise_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    return db.scalars(
        select(Submission).where(Submission.exercise_id == exercise_id)
    ).all()


# ---------- Feedback ----------
@router.post("/feedback", response_model=schemas.FeedbackOut, status_code=201)
def create_feedback(
    payload: schemas.FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    submission = db.get(Submission, payload.submission_id)
    if not submission:
        raise HTTPException(404, "Submission not found")
    obj = Feedback(
        submission_id=payload.submission_id,
        reviewer_id=current_user.id,
        score=payload.score,
        comments=payload.comments,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get(
    "/submissions/{submission_id}/feedback", response_model=list[schemas.FeedbackOut]
)
def list_feedback(
    submission_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.scalars(
        select(Feedback).where(Feedback.submission_id == submission_id)
    ).all()


# ---------- Progress ----------
@router.put("/progress", response_model=schemas.ProgressOut)
def upsert_progress(
    payload: schemas.ProgressUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.student)),
):
    existing = db.scalar(
        select(StudentModuleProgress).where(
            StudentModuleProgress.student_id == current_user.id,
            StudentModuleProgress.module_id == payload.module_id,
        )
    )
    now = datetime.now(timezone.utc)
    if existing:
        existing.completion_percentage = payload.completion_percentage
        existing.last_accessed_at = now
        obj = existing
    else:
        obj = StudentModuleProgress(
            student_id=current_user.id,
            module_id=payload.module_id,
            completion_percentage=payload.completion_percentage,
            last_accessed_at=now,
        )
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/progress/me", response_model=list[schemas.ProgressOut])
def my_progress(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.scalars(
        select(StudentModuleProgress).where(
            StudentModuleProgress.student_id == current_user.id
        )
    ).all()

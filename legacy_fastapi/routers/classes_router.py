from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..models import Class, ClassStudent, LessonPlan, Assignment, User, UserRole
from ..auth import get_current_user, require_roles

router = APIRouter(prefix="/classes", tags=["classes"])


# ---------- Class CRUD ----------
@router.post("/", response_model=schemas.ClassOut, status_code=201)
def create_class(
    payload: schemas.ClassCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    teacher_id = payload.teacher_id
    if current_user.role == UserRole.teacher:
        teacher_id = current_user.id
    obj = Class(
        class_name=payload.class_name,
        teacher_id=teacher_id,
        academic_year=payload.academic_year,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/", response_model=list[schemas.ClassOut])
def list_classes(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Class).offset(skip).limit(limit)
    if current_user.role == UserRole.teacher:
        stmt = (
            select(Class)
            .where(Class.teacher_id == current_user.id)
            .offset(skip)
            .limit(limit)
        )
    elif current_user.role == UserRole.student:
        stmt = (
            select(Class)
            .join(ClassStudent, ClassStudent.class_id == Class.id)
            .where(ClassStudent.student_id == current_user.id)
            .offset(skip)
            .limit(limit)
        )
    return db.scalars(stmt).all()


@router.get("/{class_id}", response_model=schemas.ClassOut)
def get_class(
    class_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    obj = db.get(Class, class_id)
    if not obj:
        raise HTTPException(404, "Class not found")
    return obj


@router.put("/{class_id}", response_model=schemas.ClassOut)
def update_class(
    class_id: int,
    payload: schemas.ClassUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    obj = db.get(Class, class_id)
    if not obj:
        raise HTTPException(404, "Class not found")
    if current_user.role == UserRole.teacher and obj.teacher_id != current_user.id:
        raise HTTPException(403, "Not your class")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{class_id}", status_code=204)
def delete_class(
    class_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.admin)),
):
    obj = db.get(Class, class_id)
    if not obj:
        raise HTTPException(404, "Class not found")
    db.delete(obj)
    db.commit()
    return None


# ---------- Enrollments ----------
@router.post(
    "/{class_id}/students", response_model=schemas.ClassStudentOut, status_code=201
)
def enroll_student(
    class_id: int,
    payload: schemas.ClassStudentLink,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    cls = db.get(Class, class_id)
    if not cls:
        raise HTTPException(404, "Class not found")
    if current_user.role == UserRole.teacher and cls.teacher_id != current_user.id:
        raise HTTPException(403, "Not your class")
    student = db.get(User, payload.student_id)
    if not student or student.role != UserRole.student:
        raise HTTPException(400, "Target user is not a student")
    existing = db.scalar(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == payload.student_id,
        )
    )
    if existing:
        raise HTTPException(400, "Already enrolled")
    link = ClassStudent(class_id=class_id, student_id=payload.student_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.get("/{class_id}/students", response_model=list[schemas.UserOut])
def list_students(
    class_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    cls = db.get(Class, class_id)
    if not cls:
        raise HTTPException(404, "Class not found")
    stmt = (
        select(User)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .where(ClassStudent.class_id == class_id)
    )
    return db.scalars(stmt).all()


@router.delete("/{class_id}/students/{student_id}", status_code=204)
def unenroll_student(
    class_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    cls = db.get(Class, class_id)
    if not cls:
        raise HTTPException(404, "Class not found")
    if current_user.role == UserRole.teacher and cls.teacher_id != current_user.id:
        raise HTTPException(403, "Not your class")
    link = db.scalar(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == student_id,
        )
    )
    if not link:
        raise HTTPException(404, "Enrollment not found")
    db.delete(link)
    db.commit()
    return None


# ---------- Lesson Plans ----------
@router.post(
    "/{class_id}/lesson-plans", response_model=schemas.LessonPlanOut, status_code=201
)
def create_lesson_plan(
    class_id: int,
    payload: schemas.LessonPlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    cls = db.get(Class, class_id)
    if not cls:
        raise HTTPException(404, "Class not found")
    if current_user.role == UserRole.teacher and cls.teacher_id != current_user.id:
        raise HTTPException(403, "Not your class")
    if payload.class_id != class_id:
        raise HTTPException(400, "class_id in body must match URL")
    lp = LessonPlan(**payload.model_dump())
    db.add(lp)
    db.commit()
    db.refresh(lp)
    return lp


@router.get("/{class_id}/lesson-plans", response_model=list[schemas.LessonPlanOut])
def list_lesson_plans(
    class_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return db.scalars(select(LessonPlan).where(LessonPlan.class_id == class_id)).all()


# ---------- Assignments ----------
@router.post(
    "/{class_id}/assignments", response_model=schemas.AssignmentOut, status_code=201
)
def create_assignment(
    class_id: int,
    payload: schemas.AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.teacher, UserRole.admin)),
):
    cls = db.get(Class, class_id)
    if not cls:
        raise HTTPException(404, "Class not found")
    if current_user.role == UserRole.teacher and cls.teacher_id != current_user.id:
        raise HTTPException(403, "Not your class")
    obj = Assignment(
        class_id=class_id,
        exercise_id=payload.exercise_id,
        assigned_by=current_user.id,
        due_date=payload.due_date,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{class_id}/assignments", response_model=list[schemas.AssignmentOut])
def list_assignments(
    class_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return db.scalars(select(Assignment).where(Assignment.class_id == class_id)).all()

"""DRF permission classes — port of FastAPI's `require_roles` + the
suspended-user check that lived in `get_current_user`."""

from rest_framework.permissions import BasePermission

from .models import UserRole, UserStatus


class IsActiveUser(BasePermission):
    """Mirror of the old `get_current_user` status gate: an authenticated but
    non-active (suspended/inactive) user is forbidden. Anonymous users pass
    through so `IsAuthenticated` can raise 401 instead of 403."""

    message = "User not active"

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return True
        return user.status == UserStatus.ACTIVE


class _RolePermission(BasePermission):
    allowed_roles: tuple = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user.is_authenticated and user.role in self.allowed_roles)


class IsAdmin(_RolePermission):
    allowed_roles = (UserRole.ADMIN,)


class IsTeacherOrAdmin(_RolePermission):
    allowed_roles = (UserRole.TEACHER, UserRole.ADMIN)


class IsStudent(_RolePermission):
    allowed_roles = (UserRole.STUDENT,)


def can_view_submission_feedback(user, submission) -> bool:
    """True if `user` may read feedback on `submission`.

    Allowed: the submission's student (owner), the teacher who owns the
    submission's class (via assignment.klass.teacher), a teacher who reviewed
    it, or any admin. Mirrors REFACTOR_PLAN §4 and docs.md RBAC rows 10-11.

    Per docs.md §6 safeguard 1 (Nullable Evaluator), do NOT assume a reviewer
    exists: feedback can be AI-generated (reviewer_id null).
    """
    if user.role == UserRole.ADMIN:
        return True
    if submission.student_id == user.id:
        return True
    if user.role == UserRole.TEACHER:
        # Class teacher (assignment may be null for ad-hoc practice subs).
        assignment = submission.assignment
        if assignment and assignment.klass_id and assignment.klass.teacher_id == user.id:
            return True
        # Teacher who authored a feedback row on this submission (reviewer).
        if submission.feedback.filter(reviewer_id=user.id).exists():
            return True
    return False

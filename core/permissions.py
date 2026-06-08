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

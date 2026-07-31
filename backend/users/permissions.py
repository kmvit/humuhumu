from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import User


class IsAdminRole(BasePermission):
    """Доступ только пользователю с ролью админа."""

    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated and request.user.role == User.Role.ADMIN
        )


class IsCashierOrAdmin(BasePermission):
    """Доступ кассиру или админу."""

    def has_permission(self, request, view):
        return bool(
            request.user.is_authenticated
            and request.user.role in (User.Role.CASHIER, User.Role.ADMIN)
        )


class ReadOnlyOrAdmin(BasePermission):
    """Чтение — всем, включая гостей без входа (меню публичное), запись — только админу."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(
            request.user.is_authenticated and request.user.role == User.Role.ADMIN
        )

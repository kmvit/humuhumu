"""Управление сотрудниками из панели владельца.

Раньше завести официанта или повара можно было только через Django-админку,
а она требует суперпользователя — то есть клиенту пришлось бы выдать ключи
от всей базы. Здесь тот же быт, но в границах: владелец заведения (роль
admin) заводит людей, меняет им пароли и увольняет.

Границы намеренные:
- клиенты (роль client) сюда не попадают — это гости, а не персонал;
- суперпользователей не видно и не тронуть: это наш, «Падачи», доступ
  для поддержки, владелец заведения его не редактирует;
- увольнение — деактивация, а не удаление: у сотрудника остаются смены,
  заказы и выплаты, и удаление порвало бы историю.
"""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import User
from .permissions import IsAdminRole

#: Кого владелец может заводить. Своя роль (admin) в списке есть —
#: у заведения может быть второй управляющий; суперпользователя это не даёт.
STAFF_ROLES = [
    User.Role.WAITER,
    User.Role.COOK,
    User.Role.BAR,
    User.Role.WAREHOUSE,
    User.Role.ADMIN,
]


class StaffSerializer(serializers.ModelSerializer):
    """Сотрудник глазами владельца. Пароль только на запись."""

    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    name = serializers.CharField(source="first_name", required=False, allow_blank=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "role",
            "role_display",
            "phone",
            "is_active",
            "password",
        )

    def validate_role(self, value):
        if value not in STAFF_ROLES:
            raise serializers.ValidationError("Такую роль назначить нельзя")
        return value

    def validate_username(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Логин обязателен")
        qs = User.objects.filter(username__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Такой логин уже занят")
        return value

    def validate_password(self, value):
        if not value:
            return value
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", "")
        if not password:
            raise serializers.ValidationError({"password": "Задайте пароль"})
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class StaffViewSet(viewsets.ModelViewSet):
    """CRUD по сотрудникам заведения. Только для роли «админ»."""

    serializer_class = StaffSerializer
    permission_classes = [IsAdminRole]
    # Удаления нет намеренно: увольнение — это is_active=False (см. dismiss).
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        # Суперпользователи — доступ поддержки «Падачи»; заведение их не
        # видит и не редактирует.
        return (
            User.objects.filter(role__in=STAFF_ROLES, is_superuser=False)
            .order_by("role", "first_name", "username")
        )

    def _guard_self(self, user) -> Response | None:
        """Себя не увольняем и роль себе не меняем — можно остаться без админа."""
        if user.pk == self.request.user.pk:
            return Response(
                {"detail": "Свою учётную запись здесь изменить нельзя"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        # Смена пароля самому себе — единственное безопасное самодействие.
        touches_more = set(request.data) - {"password"}
        if touches_more and (blocked := self._guard_self(user)):
            return blocked
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        """Уволить: вход закрыт, история смен и заказов цела."""
        user = self.get_object()
        if blocked := self._guard_self(user):
            return blocked
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        """Вернуть сотрудника: снова может войти под своим логином."""
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active"])
        return Response(self.get_serializer(user).data)

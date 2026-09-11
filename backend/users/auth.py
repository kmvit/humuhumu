"""Вход в границах заведения.

Логин уникален внутри заведения, а не глобально: «owner» есть у каждого
кафе, и это правильно — иначе первому клиенту достались бы удобные имена,
а остальным пришлось бы выдумывать.

Отсюда следствие: стандартный ModelBackend больше не годится — он ищет по
username во всей таблице и на двух «owner» сломается. Здесь поиск сужен
текущим заведением (его определил TenantMiddleware по домену).

Пароль сверяется всегда, даже когда пользователь не найден: иначе по
времени ответа можно было бы перебирать существующие логины.
"""
from django.contrib.auth.backends import ModelBackend
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from core.tenancy import current_organization_or_none

from .models import User


class OrganizationBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None

        org = current_organization_or_none()
        qs = User.objects.filter(username=username)
        if org is not None:
            qs = qs.filter(organization=org)
        user = qs.first()

        if user is None:
            # Холостая проверка пароля: ответ занимает столько же времени,
            # сколько для существующего логина.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        """Пользователь из сессии/токена обязан принадлежать заведению.

        Токен, выписанный на одном домене, не должен работать на другом —
        иначе сотрудник одного кафе ходил бы по API соседнего.
        """
        user = super().get_user(user_id)
        if user is None:
            return None
        org = current_organization_or_none()
        if org is not None and user.organization_id != org.pk:
            return None
        return user


class OrganizationJWTAuthentication(JWTAuthentication):
    """JWT, действующий только в своём заведении.

    SimpleJWT достаёт пользователя по id из токена, а id в общей базе
    сквозной — без этой проверки токен, выписанный в одном кафе, открыл бы
    API соседнего. Токены не «протухают» при этом: просто не действуют не
    на своём домене.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        org = current_organization_or_none()
        if org is not None and user.organization_id != org.pk:
            raise AuthenticationFailed(
                "Токен выдан другому заведению", code="wrong_tenant"
            )
        return user

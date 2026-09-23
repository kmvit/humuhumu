from rest_framework import generics, permissions
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import MeSerializer, RegisterSerializer


class LoginSerializer(TokenObtainPairSerializer):
    """То же получение токена, но с человеческим текстом отказа.

    Библиотека отвечает «No active account found with the given
    credentials» — по-английски и мимо сути: официант у планшета читает
    это как поломку, а не как опечатку в пароле. Формулировка намеренно
    не говорит, что именно не сошлось: иначе по ответу перебирали бы
    существующие логины.

    Живёт здесь, а не рядом с OrganizationBackend в users/auth.py: тот
    модуль называют настройки DRF, и импорт simplejwt оттуда замыкает
    круг — настройки тянут auth, auth тянет DRF, DRF ещё не готов.
    """

    default_error_messages = {
        "no_active_account": "Неверный логин или пароль",
    }


class LoginView(TokenObtainPairView):
    """POST /api/auth/token/ — вход сотрудника или гостя."""

    serializer_class = LoginSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — регистрация клиента (доступно без авторизации)."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveAPIView):
    """GET /api/users/me/ — текущий пользователь и баланс."""

    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

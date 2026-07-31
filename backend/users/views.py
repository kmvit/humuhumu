from rest_framework import generics, permissions

from .models import User
from .serializers import MeSerializer, RegisterSerializer


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — регистрация клиента (доступно без авторизации)."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveAPIView):
    """GET /api/users/me/ — текущий пользователь и баланс."""

    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

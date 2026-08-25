from rest_framework import generics
from rest_framework.permissions import AllowAny

from users.permissions import IsWarehouseOrAdmin

from .models import SiteSettings
from .serializers import SiteSettingsSerializer


class SiteSettingsView(generics.RetrieveUpdateAPIView):
    """GET /api/site/ — публичные настройки сайта (видны и до авторизации).

    PATCH /api/site/ — тема оформления и акцентный цвет; доступно
    менеджеру (складу) и админу.
    """

    serializer_class = SiteSettingsSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsWarehouseOrAdmin()]
        return [AllowAny()]

    def get_object(self):
        return SiteSettings.load()

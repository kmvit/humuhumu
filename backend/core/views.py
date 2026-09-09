from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import IsStaffRole, IsWarehouseOrAdmin

from .license import status_payload, sync_license

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


@api_view(["GET"])
@permission_classes([IsStaffRole])
def license_status(request):
    """GET /api/license/status/ — статус подписки для баннеров персонала.

    Не в /api/site/: тот публичный, а «оплачено до» гостям знать незачем.
    """
    return Response(status_payload())


@api_view(["POST"])
@permission_classes([IsStaffRole])
def license_refresh(request):
    """POST /api/license/refresh/ — кнопка «Проверить оплату».

    Доступна любому сотруднику: у экрана блокировки может стоять официант,
    а не владелец. Синхронно идём на пульт и возвращаем свежий статус.
    """
    sync_license()
    return Response(status_payload())

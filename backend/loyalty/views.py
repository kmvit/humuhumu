from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import SiteSettings
from core.plans import RequiresLoyalty
from users.permissions import IsStaffRole

from .models import LoyaltyMember
from .serializers import (
    BonusTransactionSerializer,
    EnrollSerializer,
    MemberSerializer,
    normalize_phone,
)
from .services import LoyaltyError


class EnrollView(generics.CreateAPIView):
    """POST /api/loyalty/enroll/ — записать гостя в программу.

    Открыт без авторизации: гость регистрируется сам по QR, а официант —
    с рабочего экрана; и тому и другому нужен один и тот же вызов.
    """

    serializer_class = EnrollSerializer
    permission_classes = [AllowAny, RequiresLoyalty]

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            member = ser.save()
        except LoyaltyError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsStaffRole, RequiresLoyalty])
def lookup(request):
    """GET /api/loyalty/lookup/?phone=... — найти гостя по телефону.

    Нужен официанту на закрытии счёта: гость называет номер, официант видит
    имя и баланс. Отдаём только участников программы.
    """
    try:
        phone = normalize_phone(request.query_params.get("phone", ""))
    except Exception:
        return Response(
            {"detail": "Введите номер, например +7 999 000-00-00"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    member = LoyaltyMember.objects.filter(user__phone=phone).select_related("user").first()
    if member is None:
        return Response(
            {"detail": "Гость не найден — предложите зарегистрироваться"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(MemberSerializer(member).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, RequiresLoyalty])
def me(request):
    """GET /api/loyalty/me/ — бонусы текущего гостя и история операций."""
    member = LoyaltyMember.objects.filter(user=request.user).first()
    if member is None:
        return Response({"detail": "Вы не в бонусной программе"}, status=status.HTTP_404_NOT_FOUND)
    return Response(
        {
            **MemberSerializer(member).data,
            "transactions": BonusTransactionSerializer(
                member.transactions.all()[:50], many=True
            ).data,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def program(request):
    """GET /api/loyalty/program/ — условия программы для экрана регистрации.

    Публично и без тарифного гейта: на тарифе без бонусов отвечаем
    enabled=false, и фронт просто не показывает раздел.
    """
    from core.plans import features

    site = SiteSettings.load()
    on = site.bonus_enabled and "loyalty" in features()
    return Response(
        {
            "enabled": on,
            "welcome": site.bonus_welcome,
            "earn_percent": site.bonus_earn_percent,
            "redeem_waiter": on and site.bonus_redeem_waiter,
            "redeem_guest": on and site.bonus_redeem_guest,
        }
    )

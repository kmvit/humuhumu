from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import SiteSettings
from core.plans import RequiresLoyalty
from users.permissions import IsAdminRole, IsStaffRole

from .models import LoyaltyMember
from .serializers import (
    AdminMemberSerializer,
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

    def get_serializer_context(self):
        user = self.request.user
        return {
            **super().get_serializer_context(),
            "staff": user.is_authenticated and user.is_staff_role,
        }

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


@api_view(["GET", "POST"])
@permission_classes([IsAdminRole, RequiresLoyalty])
def members(request):
    """GET/POST /api/loyalty/members/ — гости программы для менеджера.

    GET ?q= ищет по имени или телефону; отдаём первые 50 и общее число —
    листать тысячи гостей в панели незачем, нужного находят поиском.
    POST заводит гостя: обычной записью или переносом с остатком.
    """
    from django.db.models import Q

    if request.method == "POST":
        ser = AdminMemberSerializer(data=request.data, context={"staff": True})
        ser.is_valid(raise_exception=True)
        try:
            member = ser.save()
        except LoyaltyError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)

    qs = LoyaltyMember.objects.select_related("user")
    q = (request.query_params.get("q") or "").strip()
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        cond = Q(user__first_name__icontains=q)
        if len(digits) >= 3:
            cond |= Q(user__phone__contains=digits)
            # 8 900… и +7 900… — один номер: в базе он всегда с семёркой.
            # Но восьмёрка бывает и просто цифрой хвоста — ищем оба варианта.
            if digits[0] == "8":
                cond |= Q(user__phone__contains="7" + digits[1:])
        qs = qs.filter(cond)
    # limit=0 — только число гостей, для шапки раздела
    limit = 0 if request.query_params.get("limit") == "0" else 50
    return Response(
        {"count": qs.count(), "results": MemberSerializer(qs[:limit], many=True).data}
    )

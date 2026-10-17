from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import SiteSettings
from core.plans import RequiresLoyalty
from users.permissions import IsAdminRole, IsStaffRole

from .models import LoyaltyMember
from .serializers import (
    AdjustSerializer,
    AdminMemberSerializer,
    MemberUpdateSerializer,
    BonusTransactionSerializer,
    EnrollSerializer,
    MemberSerializer,
    normalize_phone,
)
from .services import LoyaltyError, adjust_balance, earn_percent_for


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

    GET ?q= ищет по имени или телефону, ?sort= — new (новые сверху),
    balance (больше бонусов), name; отдаём по 50 начиная с ?offset= и общее
    число — страница бонусов догружает «показать ещё».
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
    order = {
        "balance": ("-balance", "user__first_name"),
        "name": ("user__first_name", "id"),
    }.get(request.query_params.get("sort"), ("-created_at", "-id"))
    qs = qs.order_by(*order)
    # limit=0 — только число гостей, для шапки раздела
    limit = 0 if request.query_params.get("limit") == "0" else 50
    try:
        offset = max(0, int(request.query_params.get("offset", 0)))
    except ValueError:
        offset = 0
    return Response(
        {
            "count": qs.count(),
            "results": MemberSerializer(qs[offset:offset + limit], many=True).data,
        }
    )


def _member_card(member) -> dict:
    """Карточка гостя для владельца: данные, итоги и история операций."""
    from django.db.models import Count, Max, Q, Sum

    from .models import BonusTransaction as T

    stats = member.transactions.aggregate(
        earned=Sum("amount", filter=Q(type=T.Type.EARN)),
        spent=Sum("amount", filter=Q(type=T.Type.REDEEM)),
        returned=Sum("amount", filter=Q(type=T.Type.RETURN)),
        visits=Count("id", filter=Q(type=T.Type.EARN)),
        last=Max("created_at", filter=Q(type__in=[T.Type.EARN, T.Type.REDEEM])),
    )
    return {
        **MemberSerializer(member).data,
        # сколько ему начисляется сейчас — личный или общий
        "effective_percent": str(earn_percent_for(member)),
        "earned_total": str(stats["earned"] or 0),
        # списания в журнале отрицательные; возвращённые за отмену — плюс
        "spent_total": str(-(stats["spent"] or 0) - (stats["returned"] or 0)),
        "visits": stats["visits"],
        "last_visit": stats["last"],
        "transactions": BonusTransactionSerializer(
            member.transactions.select_related("created_by")[:100], many=True
        ).data,
    }


@api_view(["GET", "PATCH"])
@permission_classes([IsAdminRole, RequiresLoyalty])
def member_detail(request, pk: int):
    """GET/PATCH /api/loyalty/members/<id>/ — карточка гостя и её правка."""
    member = LoyaltyMember.objects.select_related("user").filter(pk=pk).first()
    if member is None:
        return Response({"detail": "Гость не найден"}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "PATCH":
        ser = MemberUpdateSerializer(member, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
    return Response(_member_card(member))


@api_view(["POST"])
@permission_classes([IsAdminRole, RequiresLoyalty])
def member_adjust(request, pk: int):
    """POST /api/loyalty/members/<id>/adjust/ — начислить или снять руками."""
    member = LoyaltyMember.objects.select_related("user").filter(pk=pk).first()
    if member is None:
        return Response({"detail": "Гость не найден"}, status=status.HTTP_404_NOT_FOUND)
    ser = AdjustSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        adjust_balance(
            member, ser.validated_data["amount"], ser.validated_data["comment"],
            by=request.user,
        )
    except LoyaltyError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    member.refresh_from_db()
    return Response(_member_card(member))


@api_view(["POST"])
@permission_classes([IsAdminRole, RequiresLoyalty])
def import_members(request):
    """POST /api/loyalty/members/import/ — перенос гостей из файла прежней системы.

    Без commit — только разбор: кто перенесётся, кого пропустим и почему.
    С commit=1 — перенос тех же строк. Файл присылается оба раза: держать
    чужую базу гостей на сервере между шагами незачем.
    """
    from .importer import ImportFileError, plan_import, run_import

    if not SiteSettings.load().bonus_enabled:
        return Response(
            {"detail": "Сначала включите бонусную программу"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Выберите файл"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        plan = plan_import(upload)
    except ImportFileError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    if request.data.get("commit") not in ("1", "true", True):
        return Response({**plan.as_dict(), "imported": 0})

    # Гостей переносит владелец, а согласие на обработку они давали прежней
    # программе — подтверждает это он, как сотрудник при записи на кассе.
    if request.data.get("consent") not in ("1", "true", True):
        return Response(
            {"consent": "Подтвердите, что гости давали согласие на обработку данных"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        members = run_import(plan, consent=True)
    except LoyaltyError as e:
        # кто-то из гостей появился между разбором и переносом — ничего
        # не записано, свежий разбор его просто пропустит
        return Response(
            {"detail": f"{e}. Ничего не перенесено — загрузите файл ещё раз."},
            status=status.HTTP_409_CONFLICT,
        )
    return Response({**plan.as_dict(), "imported": len(members)}, status=status.HTTP_201_CREATED)


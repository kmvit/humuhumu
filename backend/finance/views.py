"""API раздела «Финансы». Пока — ведомость по зарплате."""
from datetime import date as date_cls

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from users.models import User
from users.permissions import IsStaffRole, IsWarehouseOrAdmin

from .models import PayrollPayout
from .services import money, parse_month, statement, user_days


class PayrollViewSet(ViewSet):
    """Ведомость за месяц: начислено, выплачено, остаток.

    Работник видит только свою строку, менеджер и админ — всех и итог по
    заведению; отмечать выплаты может только менеджер или админ.
    """

    permission_classes = [IsStaffRole]

    def get_permissions(self):
        if self.action in ("pay", "unpay"):
            return [IsWarehouseOrAdmin()]
        return super().get_permissions()

    @property
    def is_manager(self):
        return self.request.user.role in (User.Role.WAREHOUSE, User.Role.ADMIN)

    def _period(self, request) -> date_cls:
        return parse_month(request.query_params.get("month"), timezone.localdate())

    def list(self, request):
        me = None if self.is_manager else request.user
        return Response(statement(self._period(request), user=me))

    @action(detail=False, methods=["get"])
    def days(self, request):
        """Расшифровка по дням: ?user=<id>&month=YYYY-MM."""
        try:
            user_id = int(request.query_params.get("user", ""))
        except ValueError:
            return Response({"detail": "Нужен ?user="}, status=status.HTTP_400_BAD_REQUEST)
        if not self.is_manager and user_id != request.user.id:
            return Response({"detail": "Доступ только к своим сменам"},
                            status=status.HTTP_403_FORBIDDEN)
        return Response(
            {"user": user_id, "days": user_days(self._period(request), user_id)}
        )

    @action(detail=False, methods=["post"])
    def pay(self, request):
        """Отметить выплату. Сумма любая — аванс и расчёт идут отдельными строками."""
        try:
            user_id = int(request.data.get("user", ""))
            amount = money(request.data.get("amount", "0"))
        except (ValueError, TypeError):
            return Response({"detail": "Нужны user и amount"},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({"detail": "Сумма должна быть больше нуля"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not User.objects.filter(pk=user_id).exists():
            return Response({"detail": "Работник не найден"},
                            status=status.HTTP_400_BAD_REQUEST)

        period = self._period(request)
        PayrollPayout.objects.create(
            user_id=user_id,
            period=period,
            amount=amount,
            paid_on=timezone.localdate(),
            comment=str(request.data.get("comment", ""))[:200],
            created_by=request.user,
        )
        return Response(statement(period))

    @action(detail=False, methods=["post"])
    def unpay(self, request):
        """Откатить последнюю выплату работнику за месяц — если ошиблись."""
        try:
            user_id = int(request.data.get("user", ""))
        except (ValueError, TypeError):
            return Response({"detail": "Нужен user"}, status=status.HTTP_400_BAD_REQUEST)
        period = self._period(request)
        last = PayrollPayout.objects.filter(user_id=user_id, period=period).first()
        if last:
            last.delete()
        return Response(statement(period))

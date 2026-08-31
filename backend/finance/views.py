"""API раздела «Финансы». Пока — ведомость по зарплате."""
from datetime import date as date_cls

from django.utils import timezone
from django.db.models import Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from users.models import User
from users.permissions import IsWarehouseOrAdmin

from .models import Expense, ExpenseCategory, PayrollPayout
from .serializers import ExpenseCategorySerializer, ExpenseSerializer
from .services import money, month_bounds, parse_month, report, statement, user_days


class PayrollViewSet(ViewSet):
    """Ведомость за месяц: начислено, выплачено, остаток.

    Весь раздел «Финансы» — управленческий: доступ только менеджеру
    («Склад») и админу. Свою выплату работник смотрит в «Сменах».
    """

    permission_classes = [IsWarehouseOrAdmin]

    def _period(self, request) -> date_cls:
        return parse_month(request.query_params.get("month"), timezone.localdate())

    def list(self, request):
        return Response(statement(self._period(request)))

    @action(detail=False, methods=["get"])
    def days(self, request):
        """Расшифровка по дням: ?user=<id>&month=YYYY-MM."""
        try:
            user_id = int(request.query_params.get("user", ""))
        except ValueError:
            return Response({"detail": "Нужен ?user="}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"user": user_id, "days": user_days(self._period(request), user_id)}
        )

    @action(detail=False, methods=["get"])
    def report(self, request):
        """Отчёт о прибыли за месяц: выручка → себестоимость → ФОТ → расходы."""
        return Response(report(self._period(request)))

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


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    """Справочник статей расходов заведения."""

    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer
    permission_classes = [IsWarehouseOrAdmin]
    pagination_class = None


class ExpenseViewSet(viewsets.ModelViewSet):
    """Прочие расходы: аренда, коммуналка, реклама и прочее.

    Список — за месяц (?month=YYYY-MM), с итогом и разбивкой по статьям,
    чтобы не считать в голове.
    """

    serializer_class = ExpenseSerializer
    permission_classes = [IsWarehouseOrAdmin]
    pagination_class = None

    def _period(self):
        return parse_month(self.request.query_params.get("month"), timezone.localdate())

    def get_queryset(self):
        first, last = month_bounds(self._period())
        return (
            Expense.objects.filter(date__range=(first, last))
            .select_related("category")
            .order_by("-date", "-id")
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        by_category = [
            {
                "category": row["category"],
                "name": row["category__name"],
                "total": str(money(row["total"])),
            }
            for row in qs.values("category", "category__name")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        ]
        total = sum((e.amount for e in qs), money(0))
        period = self._period()
        first, last = month_bounds(period)
        return Response(
            {
                "period": first.isoformat(),
                "from": first.isoformat(),
                "to": last.isoformat(),
                "rows": self.get_serializer(qs, many=True).data,
                "by_category": by_category,
                "total": str(money(total)),
            }
        )

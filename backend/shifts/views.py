from calendar import monthrange
from datetime import date as date_cls
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from users.models import User
from users.permissions import IsStaffRole, IsWarehouseOrAdmin

from .models import Shift
from .services import add_member, payroll, remove_member, shift_report, user_name


class ShiftViewSet(viewsets.ViewSet):
    """Смены: состав рабочего дня, выручка и расчёт оплаты.

    Состав смены ставит менеджер (роль «Склад») или админ. Остальной персонал
    только смотрит: свои смены, кто с ними в смене и выручку дня.
    """

    permission_classes = [IsStaffRole]

    def get_permissions(self):
        if self.action in ("add_member", "remove_member", "staff"):
            return [IsWarehouseOrAdmin()]
        return super().get_permissions()

    @property
    def is_manager(self):
        return self.request.user.role in (User.Role.WAREHOUSE, User.Role.ADMIN)

    # ——— разбор параметров ———

    def _day(self, source):
        """Дата из ?date= или из тела запроса; по умолчанию сегодня."""
        raw = source.get("date")
        if not raw:
            return timezone.localdate()
        try:
            return date_cls.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    def _range(self, request):
        """Период из ?from=&to= (по умолчанию — последние 30 дней)."""
        today = timezone.localdate()
        try:
            start = date_cls.fromisoformat(request.query_params["from"])
        except (KeyError, ValueError):
            start = today - timedelta(days=30)
        try:
            end = date_cls.fromisoformat(request.query_params["to"])
        except (KeyError, ValueError):
            end = today
        return start, end

    def _shifts(self, request):
        qs = Shift.objects.filter(
            date__range=self._range(request)
        ).prefetch_related("members__user")
        if request.user.role != User.Role.ADMIN:
            # работник видит только те смены, где был сам
            return qs.filter(members__user=request.user).distinct()
        if request.query_params.get("user"):
            qs = qs.filter(members__user=request.query_params["user"]).distinct()
        return qs

    def _day_response(self, request, day):
        shift = Shift.objects.filter(date=day).prefetch_related("members__user").first()
        report = shift_report(shift, day=day)
        report["in_shift"] = any(
            m["user"] == request.user.id for m in report["members"]
        )
        report["can_edit"] = self.is_manager
        return Response(report)

    # ——— чтение ———

    def list(self, request):
        """История смен за период: состав, выручка, расчёт на человека."""
        return Response([shift_report(s) for s in self._shifts(request)])

    @action(detail=False, methods=["get"])
    def day(self, request):
        """Смена на дату (?date=ГГГГ-ММ-ДД, по умолчанию сегодня)."""
        day = self._day(request.query_params)
        if day is None:
            return Response(
                {"detail": "Дата в формате ГГГГ-ММ-ДД"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._day_response(request, day)

    @action(detail=False, methods=["get"])
    def month(self, request):
        """Календарь: все смены месяца (?month=ГГГГ-ММ, по умолчанию текущий)."""
        first = None
        raw = request.query_params.get("month")
        if raw:
            try:
                year, mon = (int(part) for part in raw.split("-")[:2])
                first = date_cls(year, mon, 1)
            except (ValueError, TypeError):
                return Response(
                    {"detail": "Месяц в формате ГГГГ-ММ"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if first is None:
            first = timezone.localdate().replace(day=1)
        last = first.replace(day=monthrange(first.year, first.month)[1])

        days = []
        for shift in Shift.objects.filter(
            date__range=(first, last)
        ).prefetch_related("members__user"):
            report = shift_report(shift)
            report["mine"] = any(
                m["user"] == request.user.id for m in report["members"]
            )
            days.append(report)
        return Response({"month": f"{first.year}-{first.month:02d}", "days": days})

    @action(detail=False, methods=["get"])
    def staff(self, request):
        """Кого можно поставить в смену — активные сотрудники."""
        users = User.objects.filter(
            is_active=True,
            role__in=[
                User.Role.WAITER,
                User.Role.COOK,
                User.Role.BAR,
                User.Role.WAREHOUSE,
                User.Role.ADMIN,
            ],
        ).order_by("role", "first_name", "username")
        return Response(
            [
                {
                    "id": u.id,
                    "name": user_name(u),
                    "role": u.role,
                    "role_display": u.get_role_display(),
                }
                for u in users
            ]
        )

    @action(detail=False, methods=["get"])
    def payroll(self, request):
        """К выплате за период. Работнику — только его строка, админу — все."""
        me = None if request.user.role == User.Role.ADMIN else request.user
        start, end = self._range(request)
        rows = payroll(self._shifts(request), user=me)
        return Response(
            {"from": start.isoformat(), "to": end.isoformat(), "rows": rows}
        )

    # ——— состав смены (менеджер) ———

    def _member_action(self, request, add):
        day = self._day(request.data)
        if day is None:
            return Response(
                {"detail": "Дата в формате ГГГГ-ММ-ДД"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        worker = User.objects.filter(id=request.data.get("user")).first()
        if worker is None or not worker.is_staff_role:
            return Response(
                {"detail": "Работник не найден"}, status=status.HTTP_404_NOT_FOUND
            )
        if add:
            add_member(worker, day, by=request.user)
        else:
            remove_member(worker, day)
        return self._day_response(request, day)

    @action(detail=False, methods=["post"], url_path="add_member")
    def add_member(self, request):
        """Поставить работника в смену на день."""
        return self._member_action(request, add=True)

    @action(detail=False, methods=["post"], url_path="remove_member")
    def remove_member(self, request):
        """Убрать работника из смены."""
        return self._member_action(request, add=False)

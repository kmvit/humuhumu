from calendar import monthrange
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.plans import RequiresShifts
from users.models import User
from users.permissions import IsAdminRole, IsStaffRole, IsWarehouseOrAdmin

from orders.models import Table

from .models import Shift, ShiftSettings
from .services import add_member, money, payroll, remove_member, shift_report, user_name


class ShiftViewSet(viewsets.ViewSet):
    """Смены: состав рабочего дня, выручка и расчёт оплаты.

    Состав смены ставит менеджер (роль «Склад») или админ. Остальной персонал
    только смотрит: свои смены, кто с ними в смене и выручку дня.
    """

    permission_classes = [IsStaffRole, RequiresShifts]

    def get_permissions(self):
        if self.action in ("add_member", "remove_member", "staff", "set_penalty"):
            return [IsWarehouseOrAdmin(), RequiresShifts()]
        # Ставка и процент бонуса — деньги персонала: их задаёт владелец,
        # а не менеджер, который ставит состав смены.
        if self.action == "pay_settings":
            return [IsAdminRole(), RequiresShifts()]
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
        if not self.is_manager:
            # работник видит только те смены, где был сам
            return qs.filter(members__user=request.user).distinct()
        # менеджер и админ считают зарплату — им видны все смены
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
        users = User.tenant.filter(
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
    def performers(self, request):
        """Кого бариста выбирает в поле «выполнил» — сегодняшняя смена.

        Доступно любому сотруднику, а не только менеджеру: выбирает-то
        человек за стойкой. Сначала идут те, кто в смене, — им и жать.
        Если смену на сегодня не поставили, список не пустеет, а показывает
        весь персонал: поле не должно молчать из-за забывчивости менеджера.
        """
        day = timezone.localdate()
        shift = Shift.objects.filter(date=day).first()
        in_shift = set(
            shift.members.values_list("user_id", flat=True) if shift else []
        )
        users = User.tenant.filter(
            is_active=True,
            role__in=[User.Role.WAITER, User.Role.COOK, User.Role.BAR, User.Role.ADMIN],
        )
        rows = [
            {
                "id": u.id,
                "name": user_name(u),
                "role_display": u.get_role_display(),
                "in_shift": u.id in in_shift,
            }
            for u in users
        ]
        rows.sort(key=lambda r: (not r["in_shift"], r["name"]))
        return Response(rows)

    @action(detail=False, methods=["get"])
    def payroll(self, request):
        """К выплате за период. Работнику — только его строка, менеджеру — все."""
        me = None if self.is_manager else request.user
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
        worker = User.tenant.filter(id=request.data.get("user")).first()
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

    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def pay_settings(self, request):
        """Правила оплаты: ставка за день, процент бонуса, штрафной стол.

        Раньше жили только в Django-админке, то есть правил их
        разработчик. Владельцу они нужны у себя: ставка меняется чаще,
        чем выходит обновление.

        Правка применяется и к уже открытым сменам от сегодня и дальше —
        иначе владелец поднял бы ставку и не увидел этого в сегодняшней
        смене. Прошлые смены не трогаем никогда: там история выплат.
        """
        cfg = ShiftSettings.load()
        if request.method == "GET":
            return Response(self._settings_payload(cfg))

        try:
            if "daily_rate" in request.data:
                cfg.daily_rate = self._money(request.data["daily_rate"], "Оплата за смену")
            if "bonus_percent" in request.data:
                cfg.bonus_percent = self._money(request.data["bonus_percent"], "Бонус")
                if cfg.bonus_percent > 100:
                    raise ValueError("Бонус не может быть больше 100% от выручки")
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if "penalty_table" in request.data:
            raw = request.data["penalty_table"]
            cfg.penalty_table = Table.objects.filter(pk=raw).first() if raw else None

        cfg.save()
        self._apply_to_open_shifts(cfg)
        return Response(self._settings_payload(cfg))

    @staticmethod
    def _money(raw, label: str) -> Decimal:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise ValueError(f"{label}: нужно число")
        if value < 0:
            raise ValueError(f"{label}: не может быть отрицательной")
        return value

    @staticmethod
    def _apply_to_open_shifts(cfg) -> None:
        """Подтянуть новые правила к сегодняшней и будущим сменам."""
        Shift.objects.filter(date__gte=timezone.localdate()).update(
            daily_rate=cfg.daily_rate,
            bonus_percent=cfg.bonus_percent,
            penalty_table=cfg.penalty_table.name if cfg.penalty_table else "",
        )

    @staticmethod
    def _settings_payload(cfg) -> dict:
        return {
            # С копейками — как во всех суммах раздела, чтобы поле не
            # прыгало между «2000» и «2000.00» после сохранения.
            "daily_rate": str(money(cfg.daily_rate)),
            "bonus_percent": str(money(cfg.bonus_percent)),
            "penalty_table": cfg.penalty_table_id,
            # Столы для выбора штрафного: на стойке их нет вовсе, и поле
            # там просто не показывается.
            "tables": [
                {"id": t.id, "name": t.name}
                for t in Table.objects.filter(is_active=True).order_by("sort_order", "name")
            ],
        }

    @action(detail=False, methods=["post"], url_path="set_penalty")
    def set_penalty(self, request):
        """Задать ручной штраф за смену (делится на всех, минус из выплаты)."""
        day = self._day(request.data)
        if day is None:
            return Response(
                {"detail": "Дата в формате ГГГГ-ММ-ДД"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shift = Shift.objects.filter(date=day).first()
        if shift is None:
            return Response(
                {"detail": "На этот день смена не поставлена"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            penalty = Decimal(str(request.data.get("penalty", "0")))
        except (InvalidOperation, TypeError):
            return Response(
                {"detail": "Неверная сумма"}, status=status.HTTP_400_BAD_REQUEST
            )
        if penalty < 0:
            return Response(
                {"detail": "Штраф не может быть отрицательным"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shift.manual_penalty = penalty
        shift.save(update_fields=["manual_penalty"])
        return self._day_response(request, day)

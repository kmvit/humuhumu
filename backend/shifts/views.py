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

from .models import (
    STAFF_ROLES,
    hours_between,
    FocusItem,
    Scheme,
    Shift,
    ShiftMember,
    ShiftRate,
    ShiftSettings,
    ShiftType,
)
from .services import (
    _MISSING,
    add_member,
    apply_rules_from,
    apply_rules_to_shift,
    clean_amount,
    clean_kpi_grid,
    clean_kpi_roles,
    money,
    retime_type,
    rules_diff,
    payroll,
    remove_member,
    shift_report,
    update_member,
    user_name,
)


class ShiftViewSet(viewsets.ViewSet):
    """Смены: состав рабочего дня, выручка и расчёт оплаты.

    Состав смены ставит менеджер (роль «Склад») или админ. Остальной персонал
    только смотрит: свои смены, кто с ними в смене и выручку дня.
    """

    permission_classes = [IsStaffRole, RequiresShifts]

    def get_permissions(self):
        # Список фокусных позиций видит весь персонал — им и продавать;
        # заводит и правит менеджер или админ.
        if self.action == "focus" and self.request.method == "GET":
            return super().get_permissions()
        if self.action in ("focus", "focus_item"):
            return [IsWarehouseOrAdmin(), RequiresShifts()]
        if self.action in (
            "add_member", "remove_member", "update_member", "staff", "set_penalty",
            "options",
        ):
            return [IsWarehouseOrAdmin(), RequiresShifts()]
        # Ставка и процент бонуса — деньги персонала: их задаёт владелец,
        # а не менеджер, который ставит состав смены. Типы смен тоже: у
        # каждого своя ставка.
        if self.action in ("pay_settings", "shift_types", "shift_type", "apply_rules"):
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

    #: Деньги человека в строке смены. При оплате за результат у каждого
    #: своя сумма — это его личное дело, коллегам её видеть незачем.
    _PERSONAL_MONEY = (
        "base", "bonus", "senior_bonus", "penalty", "payout", "hourly",
        "kpi_revenue", "kpi", "kpi_bonus", "kpi_next",
        "focus_count", "focus_bonus", "focus_items",
        "upsell_count", "upsell_bonus", "upsell_items",
    )

    def _report(self, shift=None, day=None) -> dict:
        """Отчёт смены с учётом того, кто смотрит.

        Менеджер и админ считают зарплату — им видно всё. Сотрудник при
        оплате за результат видит свои деньги целиком, а у коллег — только
        имя, роль и время: так же, как в «К выплате» он видит одну свою
        строку, а выручку заведения не видит вовсе. При оплате поровну
        скрывать нечего — сумма у всех одна и показывается карточкой.
        """
        report = shift_report(shift, day=day)
        if self.is_manager or report["scheme"] != Scheme.RESULT:
            return report
        me = self.request.user.id
        for m in report["members"]:
            if m["user"] != me:
                for key in self._PERSONAL_MONEY:
                    m[key] = None
        for key in ("revenue", "payout_total", "kpi_unassigned"):
            report[key] = None
        return report

    def _day_response(self, request, day):
        shift = Shift.objects.filter(date=day).prefetch_related("members__user").first()
        report = self._report(shift, day=day)
        report["in_shift"] = any(
            m["user"] == request.user.id for m in report["members"]
        )
        report["can_edit"] = self.is_manager
        # Прошлая смена считается по правилам дня открытия. Владельцу
        # показываем, чем они отличаются от текущих, — и даём пересчитать.
        report["rules_diff"] = (
            rules_diff(shift)
            if shift is not None
            and request.user.role == User.Role.ADMIN
            and day < timezone.localdate()
            else []
        )
        return Response(report)

    # ——— чтение ———

    def list(self, request):
        """История смен за период: состав, выручка, расчёт на человека."""
        return Response([self._report(s) for s in self._shifts(request)])

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
            report = self._report(shift)
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
    def options(self, request):
        """Из чего менеджер выбирает, ставя человека в смену: типы и роли.

        Ставки сюда не идут: менеджер ставит состав, цена смены — дело
        владельца (см. pay_settings).
        """
        cfg = ShiftSettings.load()
        return Response(
            {
                "scheme": cfg.scheme,
                "roles": [{"value": r.value, "label": r.label} for r in STAFF_ROLES],
                "shift_types": [
                    {k: v for k, v in _type_payload(t).items() if k != "rates"}
                    for t in ShiftType.objects.prefetch_related("rates")
                ],
            }
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
            try:
                extra = self._member_fields(request.data)
            except ValueError as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            add_member(
                worker,
                day,
                by=request.user,
                shift_type=extra.get("shift_type"),
                role=extra.get("role"),
                is_senior=bool(extra.get("is_senior")),
            )
        else:
            remove_member(worker, day)
        return self._day_response(request, day)

    @staticmethod
    def _member_fields(data) -> dict:
        """Тип смены, роль, старший и время из тела запроса — то, что прислали."""
        out = {}
        if "shift_type" in data:
            raw = data["shift_type"]
            out["shift_type"] = None
            if raw not in (None, ""):
                out["shift_type"] = ShiftType.objects.filter(pk=raw).first()
                if out["shift_type"] is None:
                    raise ValueError("Тип смены не найден")
        if data.get("role"):
            if data["role"] not in STAFF_ROLES:
                raise ValueError("Такой роли в смене не бывает")
            out["role"] = data["role"]
        if "is_senior" in data:
            out["is_senior"] = bool(data["is_senior"])
        for key in ("starts_at", "ends_at"):
            if key in data:
                out[key] = _parse_time(data[key])
        return out

    @action(detail=False, methods=["post"], url_path="add_member")
    def add_member(self, request):
        """Поставить работника в смену на день."""
        return self._member_action(request, add=True)

    @action(detail=False, methods=["post"], url_path="remove_member")
    def remove_member(self, request):
        """Убрать работника из смены."""
        return self._member_action(request, add=False)

    @action(detail=False, methods=["post"], url_path="update_member")
    def update_member(self, request):
        """Поправить человека в смене: тип, роль, старший, пришёл/ушёл."""
        day = self._day(request.data)
        if day is None:
            return Response(
                {"detail": "Дата в формате ГГГГ-ММ-ДД"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        member = (
            ShiftMember.objects.filter(shift__date=day, user_id=request.data.get("user"))
            .select_related("shift", "user")
            .first()
        )
        if member is None:
            return Response(
                {"detail": "Этого человека нет в смене"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            fields = self._member_fields(request.data)
            # Каким станет время после правки: присланное, иначе из нового
            # типа, иначе прежнее. Равные отметки считались бы сутками
            # работы — это опечатка.
            new_type = fields.get("shift_type", member.shift_type)
            retyped = "shift_type" in fields and new_type is not None
            start = fields.get(
                "starts_at", new_type.starts_at if retyped else member.starts_at
            )
            end = fields.get("ends_at", new_type.ends_at if retyped else member.ends_at)
            if start and start == end:
                raise ValueError("Пришёл и ушёл в одно время — проверьте часы")
            # Время теперь деньги (оплата по часам). Уход раньше прихода
            # читается как смена через полночь — и 11:30 при приходе в 14:00
            # превратились бы в 21,5 часа. Таких смен не бывает.
            if start and end and hours_between(start, end) > 20:
                raise ValueError(
                    "Получается больше 20 часов — проверьте время прихода и ухода"
                )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        update_member(
            member,
            shift_type=fields.get("shift_type", _MISSING),
            role=fields.get("role"),
            is_senior=fields.get("is_senior"),
            starts_at=fields.get("starts_at", _MISSING),
            ends_at=fields.get("ends_at", _MISSING),
        )
        return self._day_response(request, day)

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
            if "scheme" in request.data:
                if request.data["scheme"] not in Scheme.values:
                    raise ValueError("Нет такой схемы оплаты")
                cfg.scheme = request.data["scheme"]
            if "prorate" in request.data:
                cfg.prorate = bool(request.data["prorate"])
            if "senior_bonus" in request.data:
                cfg.senior_bonus = self._money(
                    request.data["senior_bonus"], "Надбавка старшему"
                )
            if "kpi_roles" in request.data:
                cfg.kpi_roles = clean_kpi_roles(request.data["kpi_roles"] or [])
            if "kpi_grid" in request.data:
                cfg.kpi_grid = clean_kpi_grid(request.data["kpi_grid"])
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
        apply_rules_from(timezone.localdate())
        return Response(self._settings_payload(cfg))

    @staticmethod
    def _money(raw, label: str) -> Decimal:
        return clean_amount(raw, label)

    @staticmethod
    def _settings_payload(cfg) -> dict:
        return {
            # С копейками — как во всех суммах раздела, чтобы поле не
            # прыгало между «2000» и «2000.00» после сохранения.
            "scheme": cfg.scheme,
            "schemes": [{"value": v, "label": l} for v, l in Scheme.choices],
            "senior_bonus": str(money(cfg.senior_bonus)),
            "prorate": cfg.prorate,
            "kpi_roles": cfg.kpi_roles or [],
            "kpi_grid": cfg.kpi_grid or [],
            "roles": [{"value": r.value, "label": r.label} for r in STAFF_ROLES],
            "shift_types": [_type_payload(t) for t in ShiftType.objects.prefetch_related("rates")],
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

    @action(detail=False, methods=["post"], url_path="apply_rules")
    def apply_rules(self, request):
        """Пересчитать прошлую смену по текущим правилам оплаты.

        Только владелец и только явным действием: по умолчанию прошлое
        не переписывается. Нужно, когда правила поменяли сегодня, а
        вчерашнюю смену надо посчитать уже по ним.
        """
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
        apply_rules_to_shift(shift)
        return self._day_response(request, day)

    # ——— типы смен и ставки (владелец) ———

    @action(detail=False, methods=["post"], url_path="types")
    def shift_types(self, request):
        """Завести тип смены: название, начало, конец, ставки по ролям."""
        shift_type = ShiftType()
        try:
            self._fill_type(shift_type, request.data, creating=True)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            self._settings_payload(ShiftSettings.load()), status=status.HTTP_201_CREATED
        )

    @action(
        detail=False, methods=["patch", "delete"], url_path=r"types/(?P<type_id>\d+)"
    )
    def shift_type(self, request, type_id=None):
        """Поправить или удалить тип смены.

        Удаление не трогает прошлые смены: название, время и ставку люди
        в сменах хранят у себя снимком.
        """
        shift_type = ShiftType.objects.filter(pk=type_id).first()
        if shift_type is None:
            return Response(
                {"detail": "Тип смены не найден"}, status=status.HTTP_404_NOT_FOUND
            )
        today = timezone.localdate()
        if request.method == "DELETE":
            # Иначе у людей в сегодняшней смене ставка молча упала бы до
            # ставки по умолчанию. Прошлые смены удалению не мешают — там
            # всё хранится снимком.
            busy = (
                ShiftMember.objects.filter(shift_type=shift_type, shift__date__gte=today)
                .order_by("shift__date")
                .values_list("shift__date", flat=True)
                .first()
            )
            if busy:
                return Response(
                    {
                        "detail": f"Этот тип стоит в смене {busy:%d.%m} — сначала "
                        "переставьте людей на другой тип"
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            shift_type.delete()
        else:
            old_start, old_end = shift_type.starts_at, shift_type.ends_at
            try:
                self._fill_type(shift_type, request.data, creating=False)
            except ValueError as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            retime_type(shift_type, old_start, old_end, today)
        apply_rules_from(timezone.localdate())
        return Response(self._settings_payload(ShiftSettings.load()))

    def _fill_type(self, shift_type, data, creating: bool) -> None:
        if creating or "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                raise ValueError("Назовите тип смены")
            shift_type.name = name[:40]
        for key, label in (("starts_at", "Начало"), ("ends_at", "Конец")):
            if creating or key in data:
                value = _parse_time(data.get(key))
                if value is None:
                    raise ValueError(f"{label}: время в формате ЧЧ:ММ")
                setattr(shift_type, key, value)
        if shift_type.starts_at == shift_type.ends_at:
            raise ValueError("Начало и конец смены совпадают")
        if "sort_order" in data:
            try:
                shift_type.sort_order = max(int(data["sort_order"] or 0), 0)
            except (TypeError, ValueError):
                raise ValueError("Порядок: нужно целое число")
        rates = {}
        for role, raw in (data.get("rates") or {}).items():
            if role not in STAFF_ROLES:
                raise ValueError("Ставка: неизвестная роль")
            rates[role] = None if raw in (None, "") else self._money(raw, "Ставка")
        shift_type.save()
        for role, rate in rates.items():
            if rate is None:
                ShiftRate.objects.filter(shift_type=shift_type, role=role).delete()
            else:
                ShiftRate.objects.update_or_create(
                    shift_type=shift_type, role=role, defaults={"rate": rate}
                )

    # ——— фокусные позиции ———

    @action(detail=False, methods=["get", "post"], url_path="focus")
    def focus(self, request):
        """Фокусные позиции: действующие и будущие; завести новую.

        Период задаётся датами (включительно), но действует позиция не
        раньше, чем её завели: иначе утренние продажи сегодняшнего дня
        задним числом получили бы надбавку, о которой никто не знал.
        """
        if request.method == "POST":
            try:
                self._create_focus(request)
            except ValueError as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(self._focus_payload(request), status=status.HTTP_201_CREATED)
        return Response(self._focus_payload(request))

    @action(
        detail=False, methods=["patch", "delete"], url_path=r"focus/(?P<focus_id>\d+)"
    )
    def focus_item(self, request, focus_id=None):
        """Поправить надбавку или срок; снять позицию.

        Идущую позицию не переписываем: правка суммы закрывает её «сейчас»
        и открывает новую с новой суммой — проданное раньше остаётся по
        старой. Закончившуюся не трогаем вовсе: это история выплат.
        """
        item = FocusItem.objects.filter(pk=focus_id).first()
        if item is None:
            return Response({"detail": "Позиция не найдена"}, status=status.HTTP_404_NOT_FOUND)
        now = timezone.now()
        if item.ends_at <= now:
            return Response(
                {"detail": "Позиция уже закончилась — по ней посчитаны выплаты"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        started = item.starts_at <= now
        if request.method == "DELETE":
            if started:
                item.ends_at = now
                item.save(update_fields=["ends_at"])
            else:
                item.delete()
            return Response(self._focus_payload(request))

        try:
            ends_at = item.ends_at
            if "date_to" in request.data:
                ends_at = self._focus_end(request.data["date_to"])
                if ends_at <= max(now, item.starts_at):
                    raise ValueError("Дата окончания уже прошла")
            bonus = item.bonus
            if "bonus" in request.data:
                bonus = self._money(request.data["bonus"], "Надбавка")
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if started and bonus != item.bonus:
            FocusItem.objects.create(
                product=item.product, variant=item.variant, bonus=bonus,
                starts_at=now, ends_at=ends_at, created_by=request.user,
            )
            item.ends_at = now
            item.save(update_fields=["ends_at"])
        else:
            item.bonus = bonus
            item.ends_at = ends_at
            item.save(update_fields=["bonus", "ends_at"])
        return Response(self._focus_payload(request))

    @staticmethod
    def _focus_end(raw):
        """Дата «по» включительно → начало следующего дня."""
        from datetime import datetime, time

        try:
            day = date_cls.fromisoformat(str(raw))
        except ValueError:
            raise ValueError("Дата в формате ГГГГ-ММ-ДД")
        return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))

    def _create_focus(self, request):
        from datetime import datetime, time

        from catalog.models import Product, ProductVariant

        data = request.data
        product = Product.objects.filter(pk=data.get("product")).first()
        if product is None:
            raise ValueError("Выберите позицию меню")
        variant = None
        if data.get("variant"):
            variant = ProductVariant.objects.filter(
                pk=data["variant"], product=product
            ).first()
            if variant is None:
                raise ValueError("У этой позиции нет такого варианта")
        bonus = self._money(data.get("bonus"), "Надбавка")
        if bonus <= 0:
            raise ValueError("Надбавка должна быть больше нуля")
        try:
            day_from = date_cls.fromisoformat(str(data.get("date_from")))
        except ValueError:
            raise ValueError("Дата в формате ГГГГ-ММ-ДД")
        ends_at = self._focus_end(data.get("date_to"))
        now = timezone.now()
        starts_at = max(
            timezone.make_aware(datetime.combine(day_from, time.min)), now
        )
        if ends_at <= starts_at:
            raise ValueError("Период уже прошёл или «по» раньше «с»")
        # Две записи на одно и то же в одно время — непонятно, какую
        # надбавку платить. «Любой объём» и конкретный объём — можно:
        # конкретный точнее и побеждает (см. focus_sales).
        clash = FocusItem.objects.filter(
            product=product, variant=variant, starts_at__lt=ends_at, ends_at__gt=starts_at
        ).exists()
        if clash:
            raise ValueError("Эта позиция уже в фокусе на эти дни — поправьте её")
        FocusItem.objects.create(
            product=product, variant=variant, bonus=bonus,
            starts_at=starts_at, ends_at=ends_at, created_by=request.user,
        )

    def _focus_payload(self, request) -> dict:
        now = timezone.now()
        items = (
            FocusItem.objects.filter(ends_at__gt=now)
            .select_related("product", "variant")
            .order_by("starts_at", "product__name")
        )
        out = {
            "items": [
                {
                    "id": f.id,
                    "title": f.title,
                    "product": f.product_id,
                    "variant": f.variant_id,
                    "bonus": str(money(f.bonus)),
                    "starts_at": timezone.localtime(f.starts_at).isoformat(),
                    # последний день включительно — так его и задавали
                    "date_to": (timezone.localtime(f.ends_at) - timedelta(seconds=1))
                    .date()
                    .isoformat(),
                    "active": f.starts_at <= now,
                }
                for f in items
            ],
            "can_edit": self.is_manager,
        }
        if self.is_manager:
            from catalog.models import Product

            out["products"] = [
                {
                    "id": p.id,
                    "name": p.name,
                    "variants": [
                        {"id": v.id, "label": v.label}
                        for v in p.variants.all()
                        if v.is_active and v.label
                    ],
                }
                for p in Product.objects.filter(is_available=True)
                .prefetch_related("variants")
                .order_by("name")
            ]
        return out

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


def _parse_time(raw):
    """«08:00» → time; пусто → None; мусор → ValueError."""
    from datetime import time

    if raw in (None, ""):
        return None
    try:
        return time.fromisoformat(str(raw))
    except ValueError:
        raise ValueError("Время в формате ЧЧ:ММ")


def _type_payload(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "starts_at": t.starts_at.strftime("%H:%M"),
        "ends_at": t.ends_at.strftime("%H:%M"),
        "hours": str(t.hours),
        "rates": {r.role: str(money(r.rate)) for r in t.rates.all()},
    }

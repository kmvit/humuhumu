"""Расчёт денег по смене.

Правила (задаёт владелец, см. ShiftSettings):
  выручка дня      — сумма закрытых счетов за день, кроме штрафного стола;
  списания (штраф) — сумма заказов штрафного стола за день (подарки гостям за
                     косяки персонала) и ручной штраф, делятся поровну и
                     вычитаются.

Две схемы оплаты (Scheme):
  «поровну»        — ставка за день + доля процента от выручки − списания;
  «за результат»   — ставка по типу смены и роли + надбавка старшему
                     − списания. Бонусы за КПД, фокус и допродажи
                     добавляются следующими этапами.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from orders.models import Order

from .models import Scheme, Shift, ShiftMember, ShiftRate, ShiftSettings, ShiftType

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Привести к рублям с копейками."""
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def user_name(user) -> str:
    """Как показывать работника в списке смены."""
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def day_revenue(day, penalty_table: str = "") -> Decimal:
    """Выручка дня — закрытые счета за этот день, минус возвраты этого дня.

    Возвращённый заказ из выручки дня продажи НЕ убираем: он был продан,
    смена его отработала, а деньги ушли обратно в другой день. Иначе
    вчерашняя выручка (и посчитанная по ней зарплата) менялась бы задним
    числом каждый раз, когда сегодня кому-то вернули деньги.
    """
    sold = Order.objects.filter(
        status__in=[Order.Status.PAID, Order.Status.REFUNDED], closed_at__date=day
    )
    # Вычитаем чек возвращённого заказа, а не сумму платежа возврата: выручка
    # смены считается по чеку вместе с бонусами, а платёж — только деньгами.
    # Иначе при оплате частью бонусами возврат оставлял бы их в выручке дня.
    refunded = Order.objects.filter(
        status=Order.Status.REFUNDED, refunded_at__date=day
    )
    if penalty_table:
        sold = sold.exclude(table=penalty_table)
        refunded = refunded.exclude(table=penalty_table)
    returned = money(refunded.aggregate(s=Sum("total"))["s"])
    return money(sold.aggregate(s=Sum("total"))["s"]) - returned


def day_penalty(day, penalty_table: str = "") -> Decimal:
    """Списания дня — заказы штрафного стола (подарки гостям за косяки).

    Считаем по дате создания: такой заказ могут и не закрывать — гость за него
    не платит.
    """
    if not penalty_table:
        return money(0)
    qs = Order.objects.filter(
        table=penalty_table, created_at__date=day
    ).exclude(status=Order.Status.CANCELLED)
    return money(qs.aggregate(s=Sum("total"))["s"])


def performer_stats(day, penalty_table: str = "") -> dict[int, dict]:
    """Сколько заказов за день выполнил каждый — для сдельной оплаты.

    Считаем закрытые счета: пока заказ не оплачен, выручки по нему нет,
    а считать «сделанное» по незакрытому — значит сложить одно и то же
    дважды, если гость передумает. Штрафной стол не в счёт: это подарки
    за косяки, а не работа.

    На деньги смены это пока не влияет — ставка и бонус делятся как
    прежде. Сначала цифры, потом решение, как по ним платить.
    """
    from django.db.models import Count

    # Возвращённый заказ тоже сделан: работа была, деньги ушли назад позже.
    # Иначе возврат задним числом вычёркивал бы заказ у того, кто его готовил.
    qs = Order.objects.filter(
        status__in=[Order.Status.PAID, Order.Status.REFUNDED],
        closed_at__date=day,
        performer__isnull=False,
    )
    if penalty_table:
        qs = qs.exclude(table=penalty_table)
    rows = qs.values("performer").annotate(n=Count("id"), total=Sum("total"))
    return {r["performer"]: {"orders": r["n"], "orders_total": money(r["total"])} for r in rows}


def get_shift(day, create: bool = False):
    """Смена на дату. С create=True заводит её, зафиксировав текущие параметры оплаты."""
    shift = Shift.objects.filter(date=day).first()
    if shift or not create:
        return shift
    cfg = ShiftSettings.load()
    shift, _ = Shift.objects.get_or_create(date=day, defaults=_shift_terms(cfg))
    return shift


def _shift_terms(cfg) -> dict:
    """Параметры оплаты, которые смена хранит снимком."""
    return {
        "scheme": cfg.scheme,
        "senior_bonus": cfg.senior_bonus,
        "daily_rate": cfg.daily_rate,
        "bonus_percent": cfg.bonus_percent,
        "penalty_table": cfg.penalty_table.name if cfg.penalty_table else "",
    }


def _apply_member_terms(member, cfg) -> None:
    """Ставка и участие в КПД — по типу смены и роли человека в этой смене.

    Нет ставки для этой пары — rate пустой, и в расчёт идёт общая ставка
    смены: человек не должен выйти на ноль из-за незаполненной клетки.
    """
    role = member.role or member.user.role
    member.rate = (
        ShiftRate.objects.filter(shift_type=member.shift_type, role=role)
        .values_list("rate", flat=True)
        .first()
        if member.shift_type_id
        else None
    )
    member.in_kpi = role in (cfg.kpi_roles or [])


def _set_type(member, shift_type) -> None:
    """Сменить тип смены у человека: время берётся из типа заново."""
    member.shift_type = shift_type
    member.shift_type_name = shift_type.name if shift_type else ""
    member.starts_at = shift_type.starts_at if shift_type else None
    member.ends_at = shift_type.ends_at if shift_type else None


def add_member(user, day, by=None, shift_type=None, role=None, is_senior=False):
    """Менеджер ставит работника в смену на день.

    Тип смены не указан — первый из настроенных: у кафе с одним видом
    смены менеджеру незачем выбирать каждый раз.
    """
    shift = get_shift(day, create=True)
    member = ShiftMember.objects.filter(shift=shift, user=user).first()
    if member:
        return shift, member
    member = ShiftMember(
        shift=shift, user=user, role=role or user.role, added_by=by, is_senior=is_senior
    )
    _set_type(member, shift_type or ShiftType.objects.first())
    _apply_member_terms(member, ShiftSettings.load())
    member.save()
    return shift, member


_MISSING = object()


def update_member(
    member,
    shift_type=_MISSING,
    role=None,
    is_senior=None,
    starts_at=_MISSING,
    ends_at=_MISSING,
):
    """Поправить человека в смене: тип, роль, старший, фактическое время.

    Время правится поверх типа — например, ушёл раньше. Смена типа
    сбрасывает время на время нового типа.
    """
    # Менеджер явно сменил тип или роль — ставка обязана пойти следом,
    # даже в прошлой смене (забыли поставить тип вчера и поправили
    # сегодня). Иначе в строке «Короткая», а платим как за полную.
    retype = shift_type is not _MISSING or bool(role)
    if shift_type is not _MISSING:
        _set_type(member, shift_type)
    if role:
        member.role = role
    if is_senior is not None:
        member.is_senior = is_senior
    if starts_at is not _MISSING:
        member.starts_at = starts_at
    if ends_at is not _MISSING:
        member.ends_at = ends_at
    # Старшего и время можно править у любой смены — ставку они не меняют.
    if retype or member.shift.date >= timezone.localdate():
        _apply_member_terms(member, ShiftSettings.load())
    member.save()
    return member


def retime_type(shift_type, old_start, old_end, day) -> None:
    """Владелец поправил время типа — подтянуть его у людей с этого дня.

    Трогаем только тех, у кого время совпадает со старым временем типа:
    если менеджер уже отметил, что человек ушёл раньше, это факт, и
    правка типа его не перетирает.
    """
    members = ShiftMember.objects.filter(shift_type=shift_type, shift__date__gte=day)
    members.filter(starts_at=old_start).update(starts_at=shift_type.starts_at)
    members.filter(ends_at=old_end).update(ends_at=shift_type.ends_at)
    members.update(shift_type_name=shift_type.name)


def apply_rules_from(day) -> None:
    """Подтянуть текущие правила к сменам с этого дня и дальше.

    Прошлые смены не трогаем никогда: там история выплат.
    """
    cfg = ShiftSettings.load()
    Shift.objects.filter(date__gte=day).update(**_shift_terms(cfg))
    for member in ShiftMember.objects.filter(shift__date__gte=day).select_related("user"):
        _apply_member_terms(member, cfg)
        member.save(update_fields=["rate", "in_kpi"])


def remove_member(user, day):
    """Менеджер убирает работника из смены. Пустая смена не хранится."""
    shift = get_shift(day)
    if shift is None:
        return None
    shift.members.filter(user=user).delete()
    if not shift.members.exists():
        shift.delete()
        return None
    return shift


def shift_report(shift=None, day=None) -> dict:
    """Смена + деньги. Без смены (никто ещё не отметился) — пустой состав и
    текущие параметры оплаты, выручку дня всё равно показываем."""
    if shift is not None:
        day = shift.date
        scheme, senior_bonus = shift.scheme, shift.senior_bonus
        rate, percent = shift.daily_rate, shift.bonus_percent
        penalty_table = shift.penalty_table
        manual_penalty = shift.manual_penalty
        members = list(shift.members.all())
    else:
        cfg = ShiftSettings.load()
        scheme, senior_bonus = cfg.scheme, cfg.senior_bonus
        rate, percent = cfg.daily_rate, cfg.bonus_percent
        penalty_table = cfg.penalty_table.name if cfg.penalty_table else ""
        manual_penalty = Decimal("0")
        members = []

    even = scheme == Scheme.EVEN
    revenue = day_revenue(day, penalty_table)
    penalty = day_penalty(day, penalty_table)
    by_performer = performer_stats(day, penalty_table)
    # Выручка дня уходит в минус, когда сегодня вернули вчерашний заказ.
    # Бонус тогда просто ноль: вычитать его из ставки значило бы заставить
    # сегодняшних людей платить за чужую продажу (решение владельца).
    # В оплате за результат процента от выручки нет вовсе.
    bonus_pool = money(max(revenue, Decimal("0")) * percent / 100) if even else money(0)
    count = len(members)

    if count:
        bonus_share = money(bonus_pool / count)
        penalty_share = money(penalty / count)
        manual_share = money(manual_penalty / count)
    else:
        bonus_share = penalty_share = manual_share = money(0)

    rows = []
    for m in members:
        role = m.role or m.user.role
        # В оплате за результат ставка своя у каждого — по типу смены и
        # роли; клетка не заполнена — общая ставка смены.
        base = money(rate if even or m.rate is None else m.rate)
        senior = money(senior_bonus if not even and m.is_senior else 0)
        bonus = bonus_share
        payout = max(
            money(base + bonus + senior - penalty_share - manual_share), money(0)
        )
        rows.append(
            {
                "id": m.id,
                "user": m.user_id,
                "name": user_name(m.user),
                "role": role,
                "role_display": dict(m.user.Role.choices).get(role, ""),
                "added_at": m.added_at.isoformat(),
                "shift_type": m.shift_type_id,
                "shift_type_name": m.shift_type_name,
                "starts_at": m.starts_at.strftime("%H:%M") if m.starts_at else None,
                "ends_at": m.ends_at.strftime("%H:%M") if m.ends_at else None,
                "hours": str(m.hours) if m.hours is not None else None,
                "is_senior": m.is_senior,
                "in_kpi": m.in_kpi,
                # из чего сложилась выплата этого человека
                "base": str(base),
                "bonus": str(bonus),
                "senior_bonus": str(senior),
                "penalty": str(money(penalty_share + manual_share)),
                "payout": str(payout),
                # Сделанное за день. На выплату пока не влияет — это
                # цифры, по которым владелец решит, платить ли сдельно.
                "orders": by_performer.get(m.user_id, {}).get("orders", 0),
                "orders_total": str(
                    by_performer.get(m.user_id, {}).get("orders_total", money(0))
                ),
            }
        )

    # «На человека» имеет смысл, только когда всем платят одинаково.
    per_person = (
        max(money(rate + bonus_share - penalty_share - manual_share), money(0))
        if count
        else money(0)
    )

    return {
        "id": shift.id if shift else None,
        "date": day.isoformat(),
        "scheme": scheme,
        "senior_bonus": str(money(senior_bonus)),
        "daily_rate": str(money(rate)),
        "bonus_percent": str(percent),
        "penalty_table": penalty_table,
        "revenue": str(revenue),
        "penalty": str(penalty),
        "manual_penalty": str(money(manual_penalty)),
        "bonus_pool": str(bonus_pool),
        "members_count": count,
        # на одного человека в смене
        "bonus_share": str(bonus_share),
        "penalty_share": str(penalty_share),
        "manual_penalty_share": str(manual_share),
        "payout": str(per_person if even else money(0)),
        "payout_total": str(money(sum((Decimal(r["payout"]) for r in rows), Decimal("0")))),
        "members": rows,
        # Те, кто выполнял заказы, но в смену не поставлен: менеджер забыл
        # отметить, а работа сделана. Без этой строки она пропала бы.
        "outsiders": _outsiders(by_performer, members),
    }


def _outsiders(by_performer: dict[int, dict], members) -> list[dict]:
    """Исполнители заказов, которых нет в составе смены."""
    from users.models import User

    ids = set(by_performer) - {m.user_id for m in members}
    if not ids:
        return []
    return [
        {
            "user": u.id,
            "name": user_name(u),
            "orders": by_performer[u.id]["orders"],
            "orders_total": str(by_performer[u.id]["orders_total"]),
        }
        for u in User.tenant.filter(pk__in=ids).order_by("first_name", "username")
    ]


def payroll(shifts, user=None) -> list[dict]:
    """Сводка к выплате по работникам за период (по готовым отчётам смен)."""
    rows: dict[int, dict] = {}
    for shift in shifts:
        report = shift_report(shift)
        for m in report["members"]:
            if user is not None and m["user"] != user.id:
                continue
            row = rows.setdefault(
                m["user"],
                {
                    "user": m["user"],
                    "name": m["name"],
                    "role": m["role"],
                    "role_display": m["role_display"],
                    "days": 0,
                    "hours": Decimal("0"),
                    "base": Decimal("0"),
                    "bonus": Decimal("0"),
                    "penalty": Decimal("0"),
                    "total": Decimal("0"),
                    # Сделанное за период: сколько заказов закрыто с его
                    # отметкой и на какую сумму. На выплату не влияет —
                    # это цифры для решения о сдельной оплате.
                    "orders": 0,
                    "orders_total": Decimal("0"),
                },
            )
            row["days"] += 1
            row["orders"] += m["orders"]
            row["orders_total"] += Decimal(m["orders_total"])
            row["hours"] += Decimal(m["hours"] or 0)
            row["base"] += Decimal(m["base"])
            # надбавка старшему — тоже бонус сверх ставки
            row["bonus"] += Decimal(m["bonus"]) + Decimal(m["senior_bonus"])
            # в «списания» идут и подарки со штрафного стола, и ручной штраф
            row["penalty"] += Decimal(m["penalty"])
            row["total"] += Decimal(m["payout"])
    return [
        {
            **r,
            **{
                k: str(money(r[k]))
                for k in ("hours", "base", "bonus", "penalty", "total", "orders_total")
            },
        }
        for r in sorted(rows.values(), key=lambda r: -r["total"])
    ]

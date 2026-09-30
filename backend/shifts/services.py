"""Расчёт денег по смене.

Правила (задаёт владелец, см. ShiftSettings):
  выручка дня      — сумма закрытых счетов за день, кроме штрафного стола;
  списания (штраф) — сумма заказов штрафного стола за день (подарки гостям за
                     косяки персонала) и ручной штраф, делятся поровну и
                     вычитаются.

Две схемы оплаты (Scheme):
  «поровну»        — ставка за день + доля процента от выручки − списания;
  «за результат»   — ставка по типу смены и роли + надбавка старшему
                     + бонус за КПД по сетке + фокусные позиции
                     + допродажи − списания.

КПД — личная выручка в час. Каждый оплаченный заказ делится поровну
между участниками КПД, которые были на смене в момент закрытия счёта;
личная выручка делится на отработанные часы, по сетке — надбавка.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from orders.models import Order

from .models import FocusItem, Scheme, Shift, ShiftMember, ShiftRate, ShiftSettings, ShiftType

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


def clean_amount(raw, label: str) -> Decimal:
    """Сумма из формы: число, конечное, не отрицательное — иначе ValueError."""
    from decimal import InvalidOperation

    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError):
        raise ValueError(f"{label}: нужно число")
    if not value.is_finite():
        raise ValueError(f"{label}: нужно число")
    if value < 0:
        raise ValueError(f"{label}: не может быть отрицательной")
    return value


def clean_kpi_grid(raw) -> list[dict]:
    """Сетка КПД: ступени «от ₽/час → надбавка», по возрастанию, без повторов.

    Одна проверка для приложения и Django-админки: кривая сетка уронила
    бы расчёт выплат у всех смен, где она лежит снимком.
    """
    if not isinstance(raw, list):
        raise ValueError("Сетка КПД: нужен список ступеней")
    steps = {}
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Сетка КПД: неверная ступень")
        start = clean_amount(row.get("from"), "Сетка КПД, «от»")
        bonus = clean_amount(row.get("bonus"), "Сетка КПД, надбавка")
        if start in steps:
            raise ValueError(f"Сетка КПД: ступень «от {start:g}» повторяется")
        steps[start] = bonus
    return [
        {"from": str(money(s)), "bonus": str(money(b))} for s, b in sorted(steps.items())
    ]


def clean_kpi_roles(raw) -> list[str]:
    from .models import STAFF_ROLES

    if not isinstance(raw, list) or any(r not in STAFF_ROLES for r in raw):
        raise ValueError("Роли в КПД: неизвестная роль")
    return list(dict.fromkeys(raw))


def member_interval(day, member):
    """Когда человек был на смене — пара aware-datetime или None.

    Конец раньше начала — ушёл после полуночи, уже следующим днём.
    """
    from datetime import datetime, timedelta

    if member.starts_at is None or member.ends_at is None:
        return None
    start = timezone.make_aware(datetime.combine(day, member.starts_at))
    end = timezone.make_aware(datetime.combine(day, member.ends_at))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def kpi_revenue(day, members, penalty_table: str = "") -> tuple[dict[int, Decimal], Decimal]:
    """Личная выручка участников КПД и сумма, которую некому засчитать.

    Заказ относится к моменту закрытия счёта и делится поровну между
    участниками, которые в этот момент на смене: один — всё, двое —
    пополам, трое — по трети. Начало смены входит, конец — нет.

    Возвращённый в тот же день заказ в КПД не идёт (решение владельца).
    Вернули позже — смену не пересчитываем: прошлое не переписываем.
    Заказ, закрытый, когда на смене не было ни одного участника (все
    ушли, забыли поставить время), попадает в «некому засчитать» — его
    видно менеджеру, а не делится втихую.
    """
    from datetime import datetime, time, timedelta

    from django.db.models import Q

    spans = {
        m.user_id: span
        for m in members
        if m.in_kpi and (span := member_interval(day, m)) is not None
    }
    personal = {uid: Decimal("0") for uid in spans}
    if not spans:
        return personal, money(0)
    # Весь день плюс хвост смены за полночь: заказы дня вне смен тоже
    # нужны — они и есть «некому засчитать». Берём по времени, не по дате.
    day_start = timezone.make_aware(datetime.combine(day, time.min))
    lo = min([day_start, *(s for s, _ in spans.values())])
    hi = max([day_start + timedelta(days=1), *(e for _, e in spans.values())])
    orders = Order.objects.filter(closed_at__gte=lo, closed_at__lt=hi).filter(
        Q(status=Order.Status.PAID)
        | Q(status=Order.Status.REFUNDED, refunded_at__date__gt=day)
    )
    if penalty_table:
        orders = orders.exclude(table=penalty_table)

    unassigned = Decimal("0")
    for closed_at, total in orders.values_list("closed_at", "total"):
        on_shift = [uid for uid, (s, e) in spans.items() if s <= closed_at < e]
        if not on_shift:
            unassigned += total
            continue
        share = Decimal(total) / len(on_shift)
        for uid in on_shift:
            personal[uid] += share
    return {uid: money(v) for uid, v in personal.items()}, money(unassigned)


def kpi_step(grid, kpi: Decimal) -> tuple[Decimal, dict | None]:
    """Надбавка по сетке и следующая ступень (для экрана «сколько не хватает»).

    Ступень берётся по «от» включительно: КПД ровно 3 500 при ступенях
    2 500 и 3 500 — это ступень 3 500.
    """
    steps = sorted(
        ((Decimal(str(s["from"])), Decimal(str(s["bonus"]))) for s in grid or []),
        key=lambda s: s[0],
    )
    bonus = Decimal("0")
    upcoming = None
    for start, amount in steps:
        if kpi >= start:
            bonus = amount
        elif upcoming is None:
            upcoming = {"from": str(money(start)), "bonus": str(money(amount))}
    return money(bonus), upcoming


def sold_orders(day, penalty_table: str = ""):
    """Оплаченные за день заказы, которые идут в личные бонусы.

    Возврат в тот же день — вне бонусов; вернули позже — смену не
    переписываем. Штрафной стол — подарки за косяки, не продажа.
    """
    from django.db.models import Q

    qs = Order.objects.filter(closed_at__date=day).filter(
        Q(status=Order.Status.PAID)
        | Q(status=Order.Status.REFUNDED, refunded_at__date__gt=day)
    )
    if penalty_table:
        qs = qs.exclude(table=penalty_table)
    return qs


def focus_sales(day, penalty_table: str = "") -> dict[int, dict]:
    """Фокусные позиции, проданные за день, — по исполнителям заказов.

    Продажа засчитывается по надбавке, действовавшей в момент закрытия
    счёта. Если на товар заведены и «любой объём», и конкретный объём,
    берётся конкретный: он точнее выражает, что хотели продвинуть.
    Заказ без исполнителя никому не засчитывается.
    """
    from datetime import datetime, time, timedelta

    from orders.models import OrderItem

    start = timezone.make_aware(datetime.combine(day, time.min))
    focus = list(
        FocusItem.objects.filter(starts_at__lt=start + timedelta(days=1), ends_at__gt=start)
        .select_related("product", "variant")
    )
    if not focus:
        return {}
    by_product: dict[int, list] = {}
    for f in focus:
        by_product.setdefault(f.product_id, []).append(f)

    items = (
        OrderItem.objects.filter(
            order__in=sold_orders(day, penalty_table).filter(performer__isnull=False),
            variant__product_id__in=by_product,
        )
        .select_related("variant", "order")
    )
    out: dict[int, dict] = {}
    for it in items:
        at = it.order.closed_at
        live = [f for f in by_product[it.variant.product_id] if f.starts_at <= at < f.ends_at]
        match = next((f for f in live if f.variant_id == it.variant_id), None) or next(
            (f for f in live if f.variant_id is None), None
        )
        if match is None:
            continue
        row = out.setdefault(
            it.order.performer_id, {"count": 0, "bonus": Decimal("0"), "items": {}}
        )
        row["count"] += it.quantity
        row["bonus"] += match.bonus * it.quantity
        row["items"][match.title] = row["items"].get(match.title, 0) + it.quantity
    for row in out.values():
        row["bonus"] = money(row["bonus"])
    return out


def upsell_sales(day, penalty_table: str = "") -> dict[int, dict]:
    """Допродажи за день — по исполнителям заказов.

    Допродажа — опция с надбавкой (сироп, альтернативное молоко, пенка).
    Надбавка берётся из заказа: она запомнилась, когда гость выбрал опцию,
    поэтому правка суммы в меню прошлые продажи не трогает. Две порции с
    сиропом — две допродажи.
    """
    from orders.models import OrderItemModifier

    rows = (
        OrderItemModifier.objects.filter(
            upsell_bonus__gt=0,
            order_item__order__in=sold_orders(day, penalty_table).filter(
                performer__isnull=False
            ),
        )
        .values_list(
            "order_item__order__performer", "name", "upsell_bonus", "order_item__quantity"
        )
    )
    out: dict[int, dict] = {}
    for performer, name, bonus, qty in rows:
        row = out.setdefault(performer, {"count": 0, "bonus": Decimal("0"), "items": {}})
        row["count"] += qty
        row["bonus"] += bonus * qty
        row["items"][name] = row["items"].get(name, 0) + qty
    for row in out.values():
        row["bonus"] = money(row["bonus"])
    return out


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
        "kpi_grid": cfg.kpi_grid or [],
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
        kpi_grid = shift.kpi_grid
        rate, percent = shift.daily_rate, shift.bonus_percent
        penalty_table = shift.penalty_table
        manual_penalty = shift.manual_penalty
        members = list(shift.members.all())
    else:
        cfg = ShiftSettings.load()
        scheme, senior_bonus = cfg.scheme, cfg.senior_bonus
        kpi_grid = cfg.kpi_grid
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

    if even:
        personal, unassigned, focus, upsell = {}, money(0), {}, {}
    else:
        personal, unassigned = kpi_revenue(day, members, penalty_table)
        focus = focus_sales(day, penalty_table)
        upsell = upsell_sales(day, penalty_table)

    rows = []
    for m in members:
        role = m.role or m.user.role
        # В оплате за результат ставка своя у каждого — по типу смены и
        # роли; клетка не заполнена — общая ставка смены.
        base = money(rate if even or m.rate is None else m.rate)
        senior = money(senior_bonus if not even and m.is_senior else 0)
        kpi = kpi_bonus = None
        upcoming = None
        if m.user_id in personal:
            hours = m.hours or Decimal("0")
            kpi = money(personal[m.user_id] / hours) if hours else money(0)
            kpi_bonus, upcoming = kpi_step(kpi_grid, kpi)
        empty = {"count": 0, "bonus": money(0), "items": {}}
        mine = focus.get(m.user_id, empty)
        ups = upsell.get(m.user_id, empty)
        bonus = (
            bonus_share
            if even
            else money((kpi_bonus or 0) + mine["bonus"] + ups["bonus"])
        )
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
                # КПД: личная выручка, в час, надбавка и следующая ступень.
                # None — человек не участвует в КПД (или оплата поровну).
                "kpi_revenue": str(personal[m.user_id]) if m.user_id in personal else None,
                "kpi": str(kpi) if kpi is not None else None,
                "kpi_bonus": str(kpi_bonus) if kpi_bonus is not None else None,
                "kpi_next": upcoming,
                # фокусные позиции: сколько штук продал и что именно
                "focus_count": mine["count"],
                "focus_bonus": str(mine["bonus"]),
                "focus_items": [
                    {"title": t, "count": n} for t, n in sorted(mine["items"].items())
                ],
                # допродажи: сколько опций и каких
                "upsell_count": ups["count"],
                "upsell_bonus": str(ups["bonus"]),
                "upsell_items": [
                    {"title": t, "count": n} for t, n in sorted(ups["items"].items())
                ],
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
        "kpi_grid": kpi_grid or [],
        # Выручка заказов, закрытых, когда на смене не было ни одного
        # участника КПД: её никому не засчитали.
        "kpi_unassigned": str(unassigned),
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
        "outsiders": _outsiders(by_performer, members, focus, upsell),
    }


def _outsiders(by_performer: dict[int, dict], members, focus=None, upsell=None) -> list[dict]:
    """Исполнители заказов, которых нет в составе смены.

    Их фокусные продажи тоже показываем: денег без смены не начислить,
    но менеджер должен увидеть, что человек работал и продавал.
    """
    from users.models import User

    focus = focus or {}
    upsell = upsell or {}
    ids = set(by_performer) - {m.user_id for m in members}
    if not ids:
        return []
    return [
        {
            "user": u.id,
            "name": user_name(u),
            "orders": by_performer[u.id]["orders"],
            "orders_total": str(by_performer[u.id]["orders_total"]),
            "focus_count": focus.get(u.id, {}).get("count", 0),
            "upsell_count": upsell.get(u.id, {}).get("count", 0),
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
                    # все надбавки вместе и отдельно — КПД и старшему:
                    # по ведомости выдают деньги, «бонус 1 000» без
                    # расшифровки не проверить
                    "bonus": Decimal("0"),
                    "kpi_bonus": Decimal("0"),
                    "focus_bonus": Decimal("0"),
                    "focus_count": 0,
                    "upsell_bonus": Decimal("0"),
                    "upsell_count": 0,
                    "senior_bonus": Decimal("0"),
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
            row["kpi_bonus"] += Decimal(m["kpi_bonus"] or 0)
            row["focus_bonus"] += Decimal(m["focus_bonus"])
            row["focus_count"] += m["focus_count"]
            row["upsell_bonus"] += Decimal(m["upsell_bonus"])
            row["upsell_count"] += m["upsell_count"]
            row["senior_bonus"] += Decimal(m["senior_bonus"])
            # в «списания» идут и подарки со штрафного стола, и ручной штраф
            row["penalty"] += Decimal(m["penalty"])
            row["total"] += Decimal(m["payout"])
    return [
        {
            **r,
            **{
                k: str(money(r[k]))
                for k in (
                    "hours", "base", "bonus", "kpi_bonus", "focus_bonus",
                    "upsell_bonus", "senior_bonus",
                    "penalty", "total", "orders_total",
                )
            },
        }
        for r in sorted(rows.values(), key=lambda r: -r["total"])
    ]

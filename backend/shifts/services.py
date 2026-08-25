"""Расчёт денег по смене.

Правила (задаются в админке, см. ShiftSettings):
  выручка дня      — сумма закрытых счетов за день, кроме штрафного стола;
  бонус            — процент от выручки, делится поровну на всех в смене;
  списания (штраф) — сумма заказов штрафного стола за день (подарки гостям за
                     косяки персонала), тоже делится поровну и вычитается;
  на человека      — ставка за день + доля бонуса − доля списаний.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from orders.models import Order

from .models import Shift, ShiftMember, ShiftSettings

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Привести к рублям с копейками."""
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def user_name(user) -> str:
    """Как показывать работника в списке смены."""
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def day_revenue(day, penalty_table: str = "") -> Decimal:
    """Выручка дня — закрытые счета за этот день (без штрафного стола)."""
    qs = Order.objects.filter(status=Order.Status.PAID, closed_at__date=day)
    if penalty_table:
        qs = qs.exclude(table=penalty_table)
    return money(qs.aggregate(s=Sum("total"))["s"])


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


def get_shift(day, create: bool = False):
    """Смена на дату. С create=True заводит её, зафиксировав текущие параметры оплаты."""
    shift = Shift.objects.filter(date=day).first()
    if shift or not create:
        return shift
    cfg = ShiftSettings.load()
    shift, _ = Shift.objects.get_or_create(
        date=day,
        defaults={
            "daily_rate": cfg.daily_rate,
            "bonus_percent": cfg.bonus_percent,
            "penalty_table": cfg.penalty_table.name if cfg.penalty_table else "",
        },
    )
    return shift


def add_member(user, day, by=None):
    """Менеджер ставит работника в смену на день."""
    shift = get_shift(day, create=True)
    member, _ = ShiftMember.objects.get_or_create(
        shift=shift, user=user, defaults={"role": user.role, "added_by": by}
    )
    return shift, member


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
        rate, percent = shift.daily_rate, shift.bonus_percent
        penalty_table = shift.penalty_table
        manual_penalty = shift.manual_penalty
        members = list(shift.members.all())
    else:
        cfg = ShiftSettings.load()
        rate, percent = cfg.daily_rate, cfg.bonus_percent
        penalty_table = cfg.penalty_table.name if cfg.penalty_table else ""
        manual_penalty = Decimal("0")
        members = []

    revenue = day_revenue(day, penalty_table)
    penalty = day_penalty(day, penalty_table)
    bonus_pool = money(revenue * percent / 100)
    count = len(members)

    if count:
        bonus_share = money(bonus_pool / count)
        penalty_share = money(penalty / count)
        manual_share = money(manual_penalty / count)
        payout = max(
            money(rate + bonus_share - penalty_share - manual_share), money(0)
        )
    else:
        bonus_share = penalty_share = manual_share = payout = money(0)

    return {
        "id": shift.id if shift else None,
        "date": day.isoformat(),
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
        "payout": str(payout),
        "members": [
            {
                "id": m.id,
                "user": m.user_id,
                "name": user_name(m.user),
                "role": m.role or m.user.role,
                "role_display": dict(m.user.Role.choices).get(
                    m.role or m.user.role, ""
                ),
                "added_at": m.added_at.isoformat(),
                "payout": str(payout),
            }
            for m in members
        ],
    }


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
                    "base": Decimal("0"),
                    "bonus": Decimal("0"),
                    "penalty": Decimal("0"),
                    "total": Decimal("0"),
                },
            )
            row["days"] += 1
            row["base"] += Decimal(report["daily_rate"])
            row["bonus"] += Decimal(report["bonus_share"])
            # в «списания» идут и подарки со штрафного стола, и ручной штраф
            row["penalty"] += Decimal(report["penalty_share"]) + Decimal(
                report["manual_penalty_share"]
            )
            row["total"] += Decimal(m["payout"])
    return [
        {**r, **{k: str(money(r[k])) for k in ("base", "bonus", "penalty", "total")}}
        for r in sorted(rows.values(), key=lambda r: -r["total"])
    ]

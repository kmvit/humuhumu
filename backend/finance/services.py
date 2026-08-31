"""Ведомость: начислено за месяц, выплачено, остаток.

Начисления НЕ пересчитываются здесь — берутся из shifts.services.payroll,
единственного места, где живёт формула «ставка + доля бонуса − списания».
Дублировать её нельзя: разойдутся цифры в «Сменах» и «Финансах».
"""
from calendar import monthrange
from datetime import date as date_cls
from decimal import Decimal

from django.db.models import Sum

from shifts.models import Shift
from shifts.services import money, payroll

from .models import PayrollPayout


def month_bounds(period: date_cls) -> tuple[date_cls, date_cls]:
    """Первый и последний день месяца, которому принадлежит дата."""
    first = period.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    return first, last


def parse_month(raw: str | None, today: date_cls) -> date_cls:
    """«2026-08» или «2026-08-01» → первое число месяца. Мусор → текущий месяц."""
    if raw:
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                from datetime import datetime

                return datetime.strptime(raw, fmt).date().replace(day=1)
            except ValueError:
                continue
    return today.replace(day=1)


def statement(period: date_cls, user=None) -> dict:
    """Ведомость за месяц.

    user — ограничить одной строкой (работник видит только себя).
    """
    first, last = month_bounds(period)

    shifts = (
        Shift.objects.filter(date__range=(first, last))
        .prefetch_related("members__user")
        .order_by("date")
    )
    if user is not None:
        shifts = shifts.filter(members__user=user).distinct()

    rows = payroll(shifts, user=user)

    # Выплаты за этот месяц — одним запросом, а не по строке на человека
    paid_qs = PayrollPayout.objects.filter(period=first)
    if user is not None:
        paid_qs = paid_qs.filter(user=user)
    paid_by_user = {
        r["user"]: r["total"]
        for r in paid_qs.values("user").annotate(total=Sum("amount"))
    }

    accrued_total = Decimal("0")
    paid_total = Decimal("0")
    for row in rows:
        accrued = Decimal(row["total"])
        paid = paid_by_user.get(row["user"], Decimal("0"))
        row["accrued"] = str(money(accrued))
        row["paid"] = str(money(paid))
        row["left"] = str(money(accrued - paid))
        row["settled"] = paid >= accrued and accrued > 0
        accrued_total += accrued
        paid_total += paid

    return {
        "period": first.isoformat(),
        "from": first.isoformat(),
        "to": last.isoformat(),
        "rows": rows,
        "totals": {
            # ФОТ за месяц — то, чего не было видно нигде
            "accrued": str(money(accrued_total)),
            "paid": str(money(paid_total)),
            "left": str(money(accrued_total - paid_total)),
            "people": len(rows),
            "shifts": shifts.count(),
        },
    }


def user_days(period: date_cls, user_id: int) -> list[dict]:
    """Расшифровка по дням: из чего сложилась сумма у конкретного работника."""
    from shifts.services import shift_report

    first, last = month_bounds(period)
    out = []
    for shift in (
        Shift.objects.filter(date__range=(first, last), members__user_id=user_id)
        .prefetch_related("members__user")
        .order_by("date")
        .distinct()
    ):
        report = shift_report(shift)
        out.append(
            {
                "date": report["date"],
                "revenue": report["revenue"],
                "members_count": report["members_count"],
                "daily_rate": report["daily_rate"],
                "bonus_share": report["bonus_share"],
                "penalty_share": report["penalty_share"],
                "manual_penalty_share": report["manual_penalty_share"],
                "payout": report["payout"],
            }
        )
    return out

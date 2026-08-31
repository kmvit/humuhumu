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

from .models import Expense, PayrollPayout


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


def statement(period: date_cls) -> dict:
    """Ведомость за месяц по всей команде.

    Раздел управленческий, поэтому без фильтра по работнику: сюда попадают
    только менеджер и админ (см. finance/views.py).
    """
    first, last = month_bounds(period)

    shifts = (
        Shift.objects.filter(date__range=(first, last))
        .prefetch_related("members__user")
        .order_by("date")
    )
    rows = payroll(shifts)

    # Выплаты за этот месяц — одним запросом, а не по строке на человека
    paid_qs = PayrollPayout.objects.filter(period=first)
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


# ─────────────────────────────────────────────────────────────────────────
# Отчёт о прибыли за месяц
# ─────────────────────────────────────────────────────────────────────────

def _cost_per_portion(product_ids) -> tuple[dict[int, Decimal], set[int]]:
    """Себестоимость порции по тех. карте и множество блюд с полной картой.

    Блюдо считается покрытым, только если у него есть тех. карта И у всех её
    товаров известна цена закупа. Иначе себестоимость занижена, и говорить
    о прибыли как о факте нельзя.
    """
    from inventory.models import RecipeItem
    from inventory.services import last_unit_costs

    rows = list(
        RecipeItem.objects.filter(product_id__in=list(product_ids))
        .values("product_id", "item_id", "quantity")
    )
    costs = last_unit_costs({r["item_id"] for r in rows})

    per_product: dict[int, Decimal] = {}
    complete: dict[int, bool] = {}
    for r in rows:
        unit = costs.get(r["item_id"])
        per_product[r["product_id"]] = per_product.get(
            r["product_id"], Decimal("0")
        ) + (r["quantity"] * (unit or Decimal("0")))
        complete[r["product_id"]] = complete.get(r["product_id"], True) and unit is not None

    covered = {pid for pid, ok in complete.items() if ok}
    return per_product, covered


def report(period: date_cls) -> dict:
    """Отчёт о прибыли за месяц: выручка → себестоимость → ФОТ → расходы.

    Закуп продуктов НЕ вычитается: расход периода — себестоимость проданного,
    а закуп это движение денег. Иначе получился бы двойной счёт, поэтому он
    идёт отдельной справочной строкой.
    """
    from django.db.models import Count, F, Sum
    from decimal import Decimal as D

    from inventory.models import Receipt, ReceiptItem
    from orders.models import Order, OrderItem
    from shifts.models import ShiftSettings

    first, last = month_bounds(period)
    cfg = ShiftSettings.load()
    penalty_table = cfg.penalty_table.name if cfg.penalty_table else ""

    orders = Order.objects.filter(
        status=Order.Status.PAID, closed_at__date__range=(first, last)
    )
    if penalty_table:
        orders = orders.exclude(table=penalty_table)

    agg = orders.aggregate(total=Sum("total"), checks=Count("id"))
    revenue = money(agg["total"])
    checks = agg["checks"] or 0
    avg_check = money(revenue / checks) if checks else money(0)

    by_method = {
        row["pay_method"]: money(row["s"])
        for row in orders.values("pay_method").annotate(s=Sum("total"))
    }

    # ——— себестоимость проданного ———
    sold = list(
        OrderItem.objects.filter(order__in=orders)
        .values("product_id")
        .annotate(qty=Sum("quantity"), sum=Sum(F("unit_price") * F("quantity")))
    )
    per_portion, covered_ids = _cost_per_portion([s["product_id"] for s in sold])

    cogs = money(0)
    covered_revenue = money(0)
    for s in sold:
        pid = s["product_id"]
        if pid in covered_ids:
            cogs += per_portion.get(pid, D("0")) * s["qty"]
            covered_revenue += money(s["sum"])
    cogs = money(cogs)

    # доля выручки, у которой известна себестоимость, — мера честности отчёта
    coverage = (
        float(covered_revenue / revenue) if revenue else 0.0
    )

    gross = money(revenue - cogs)
    margin = round(float(gross / revenue) * 100, 1) if revenue else 0.0

    fot = money(D(statement(period)["totals"]["accrued"]))
    other = money(
        Expense.objects.filter(date__range=(first, last)).aggregate(s=Sum("amount"))["s"]
    )
    profit = money(gross - fot - other)

    # справочно: сколько закупили продуктов (движение денег, не расход периода)
    purchases = money(
        ReceiptItem.objects.filter(
            receipt__in=Receipt.objects.filter(created_at__date__range=(first, last)),
            unit_cost__isnull=False,
        ).aggregate(s=Sum(F("quantity") * F("unit_cost")))["s"]
    )

    return {
        "period": first.isoformat(),
        "from": first.isoformat(),
        "to": last.isoformat(),
        "revenue": str(revenue),
        "checks": checks,
        "avg_check": str(avg_check),
        "cash": str(by_method.get("cash", money(0))),
        "card": str(by_method.get("card", money(0))),
        "cogs": str(cogs),
        "gross": str(gross),
        "margin": margin,
        "payroll": str(fot),
        "expenses": str(other),
        "profit": str(profit),
        "purchases": str(purchases),
        # честность: пока себестоимость известна не по всей выручке,
        # прибыль — оценка сверху, а не факт
        "cost_coverage": round(coverage * 100, 1),
        "is_estimate": coverage < 0.995,
    }

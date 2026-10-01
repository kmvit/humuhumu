"""Журнал платежей для владельца: кто, сколько, чем и через что заплатил.

Сводка в панели считает деньги по заказам и знает только «нал или
карта». Владельцу этого мало, когда у заведения два пути для карты —
онлайн у банка и на своей кассе: деньги приходят разными днями и на
разные счета, и сверять их приходится по отдельности.
"""
from __future__ import annotations

from decimal import Decimal

from .acquiring import acquirer_class, NoAcquirer
from .models import Payment
from .providers import is_kassa, provider_class

MANUAL, ONLINE, KASSA = "manual", "online", "kassa"

CHANNEL_TITLES = {
    MANUAL: "Отметка сотрудника",
    ONLINE: "Онлайн",
    KASSA: "Касса",
}


def channel_of(provider: str) -> str:
    """Каким путём прошёл платёж: касса, онлайн-банк или отметка сотрудника."""
    if is_kassa(provider):
        return KASSA
    if provider and provider != MANUAL and acquirer_class(provider) is not NoAcquirer:
        return ONLINE
    return MANUAL


def provider_title(provider: str) -> str:
    """Название провайдера для людей: «ЮKassa», «aQsi (смарт-касса Т-Банка)»."""
    cls = provider_class(provider)
    if cls is not None:
        return cls.title
    cls = acquirer_class(provider)
    if cls is not NoAcquirer:
        return cls.title
    return CHANNEL_TITLES[MANUAL]


#: Платежи, по которым деньги действительно пришли. «Возвращён» — тоже
#: пришли: возврат идёт отдельной записью своим днём и вычитается там.
MONEY_IN = (Payment.Status.SUCCEEDED, Payment.Status.REFUNDED)


def day_journal(day) -> dict:
    """Все платежи по заказам за день и итоги по каналам."""
    payments = (
        Payment.objects.filter(
            created_at__date=day,
            purpose__in=(Payment.Purpose.ORDER, Payment.Purpose.REFUND),
        )
        .select_related("order", "confirmed_by")
        .order_by("-created_at")
    )

    totals = {"cash": Decimal(0), "card_kassa": Decimal(0), "online": Decimal(0),
              "refunds": Decimal(0)}
    rows = []
    for p in payments:
        channel = channel_of(p.provider)
        refund = p.purpose == Payment.Purpose.REFUND
        if refund:
            # Возврат через кассу, который ещё идёт или не прошёл, деньги
            # не вернул — вычитать его из дня рано.
            if p.status == Payment.Status.SUCCEEDED:
                totals["refunds"] += p.amount
        elif p.status in MONEY_IN:
            if channel == ONLINE:
                totals["online"] += p.amount
            elif p.method == Payment.Method.CASH:
                totals["cash"] += p.amount
            else:
                totals["card_kassa"] += p.amount
        order = p.order
        rows.append({
            "id": p.pk,
            "created_at": p.created_at,
            "order": order.pk if order else None,
            "order_number": (order.daily_number or order.pk) if order else None,
            "customer_name": order.customer_name if order else "",
            "amount": p.amount,
            "refund": refund,
            "status": p.status,
            "status_display": p.get_status_display(),
            "method": p.method,
            "method_display": p.get_method_display() if p.method else "",
            "channel": channel,
            "channel_display": CHANNEL_TITLES[channel],
            "provider_display": provider_title(p.provider),
            "fiscal_receipt": p.fiscal_receipt,
            # Касса оплату не подтвердила — отметил сотрудник. Сверить с
            # отчётом кассы: не пробитый чек лежит там в отложенных.
            "confirmed_by": (
                (p.confirmed_by.get_full_name() or p.confirmed_by.username)
                if p.confirmed_by else ""
            ),
        })

    income = totals["cash"] + totals["card_kassa"] + totals["online"]
    return {
        "date": day.isoformat(),
        "rows": rows,
        "orders": day_orders(day),
        "totals": {**totals, "income": income, "net": income - totals["refunds"]},
    }


def day_orders(day) -> list[dict]:
    """Заказы, оформленные за день, — с тем, как и чем за них заплатили.

    Канал берём из последнего платежа, по которому пришли деньги: у заказа
    их бывает несколько (снятый с кассы, отказ банка и вторая попытка).
    """
    from django.db.models import OuterRef, Subquery

    from orders.models import Order

    paid = Payment.objects.filter(
        order=OuterRef("pk"), purpose=Payment.Purpose.ORDER, status__in=MONEY_IN,
    ).order_by("-created_at")
    orders = (
        Order.objects.filter(created_at__date=day)
        .annotate(
            pay_provider=Subquery(paid.values("provider")[:1]),
            pay_method_real=Subquery(paid.values("method")[:1]),
        )
        .prefetch_related("items__variant__product", "items__modifiers")
        .order_by("-created_at")
    )
    result = []
    for o in orders:
        channel = channel_of(o.pay_provider) if o.pay_provider else ""
        method = o.pay_method_real or ""
        items = []
        for it in o.items.all():
            name = it.display_name + (f" ({it.options_text})" if it.options_text else "")
            items.append(f"{name} × {it.quantity}")
        result.append({
            "id": o.pk,
            "number": o.daily_number,
            "created_at": o.created_at,
            "customer_name": o.customer_name,
            "table": o.table,
            "items": items,
            "total": o.total,
            "payable": o.payable,
            "bonus_spent": o.bonus_spent,
            "status": o.status,
            "status_display": o.get_status_display(),
            "paid": o.paid_at is not None,
            "channel": channel,
            "channel_display": CHANNEL_TITLES.get(channel, ""),
            "method": method,
            "method_display": dict(Payment.Method.choices).get(method, ""),
            "fiscal_receipt": o.fiscal_receipt,
        })
    return result

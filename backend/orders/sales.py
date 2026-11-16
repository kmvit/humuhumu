"""Продажи позиций по дате оплаты; возвраты по дате возврата."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import Order, OrderItem


def sales_report(start, end):
    lower = timezone.make_aware(datetime.combine(start, time.min))
    upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    sold = Q(order__paid_at__gte=lower, order__paid_at__lt=upper)
    returned = Q(order__refunded_at__gte=lower, order__refunded_at__lt=upper)
    items = (OrderItem.objects.filter(sold | returned)
             .exclude(order__status=Order.Status.CANCELLED)
             .select_related("order", "variant__product__category")
             .prefetch_related("modifiers"))
    rows = {}
    for item in items:
        row = rows.setdefault(item.variant_id, {
            "id": item.variant_id, "name": item.display_name,
            "category": item.variant.product.category.name,
            "quantity": 0, "amount": Decimal(0),
            "returned_quantity": 0, "returned_amount": Decimal(0),
        })
        if item.order.paid_at and lower <= item.order.paid_at < upper:
            row["quantity"] += item.quantity
            row["amount"] += item.subtotal
        if item.order.refunded_at and lower <= item.order.refunded_at < upper:
            row["returned_quantity"] += item.quantity
            row["returned_amount"] += item.subtotal
    rows = sorted(rows.values(), key=lambda r: (-r["quantity"], r["name"], r["id"]))
    return {"rows": rows, "totals": {
        key: sum((row[key] for row in rows), 0)
        for key in ("quantity", "amount", "returned_quantity", "returned_amount")
    }}

"""Бизнес-логика заказов."""
from django.db import transaction

from catalog.models import Product

from .models import Order, OrderItem


class OrderError(Exception):
    pass


@transaction.atomic
def create_order(*, waiter, items: list[dict], table: str = "") -> Order:
    """Создать заказ официантом. items = [{'product': id, 'quantity': n}, ...].

    Цены берём с сервера (не доверяем фронту). Заказ сразу уходит на кухню
    (статус «На кухне»). Оплату позже фиксирует кассир-бармен вручную.
    """
    if not items:
        raise OrderError("Пустой заказ")

    order = Order.objects.create(
        waiter=waiter, table=table, status=Order.Status.OPEN
    )

    for line in items:
        try:
            product = Product.objects.get(pk=line["product"], is_available=True)
        except Product.DoesNotExist:
            raise OrderError("Товар недоступен или не найден")
        quantity = int(line.get("quantity", 1))
        if quantity < 1:
            raise OrderError("Количество должно быть положительным")
        guest = line.get("guest")
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            unit_price=product.price,  # фиксируем цену на момент покупки
            guest=guest if guest else None,  # 0/None → общий
        )

    order.recalc_total()
    order.save(update_fields=["total"])

    return order

"""Бизнес-логика заказов."""
from django.db import transaction

from catalog.models import Product

from .models import Order, OrderItem


class OrderError(Exception):
    pass


@transaction.atomic
def create_order(*, client, pay_method: str, items: list[dict]) -> Order:
    """Создать заказ. items = [{'product': id, 'quantity': n}, ...].

    Цены берём с сервера (не доверяем фронту). При оплате токенами списываем сразу.
    При оплате картой заказ остаётся в статусе «ожидает оплаты» (платёж подключим позже).
    """
    if not items:
        raise OrderError("Пустой заказ")

    order = Order.objects.create(client=client, pay_method=pay_method)

    for line in items:
        product = Product.objects.get(pk=line["product"], is_available=True)
        quantity = int(line.get("quantity", 1))
        if quantity < 1:
            raise OrderError("Количество должно быть положительным")
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            unit_price=product.price,  # фиксируем цену на момент покупки
        )

    order.recalc_total()
    order.save(update_fields=["total"])

    if pay_method == Order.PayMethod.TOKENS:
        # списываем токены атомарно; при нехватке откатится вся транзакция (и заказ)
        from wallet.services import spend_tokens

        spend_tokens(client.wallet.id, order.total, order=order)
        order.status = Order.Status.PAID
        order.save(update_fields=["status"])

    return order

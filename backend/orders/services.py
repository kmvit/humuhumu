"""Бизнес-логика заказов."""
import uuid

from django.db import transaction

from catalog.models import Product

from .models import Order, OrderItem


class OrderError(Exception):
    pass


def _add_items(order: Order, items: list[dict]) -> None:
    """Добавить позиции к заказу, беря цены с сервера."""
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


@transaction.atomic
def create_order(*, waiter, items: list[dict], table: str = "", comment: str = "") -> Order:
    """Заказ, созданный официантом сразу на столе (статус «Открыт»)."""
    if not items:
        raise OrderError("Пустой заказ")
    order = Order.objects.create(
        waiter=waiter, table=table, comment=comment, status=Order.Status.OPEN
    )
    _add_items(order, items)
    order.recalc_total()
    order.save(update_fields=["total"])
    return order


@transaction.atomic
@transaction.atomic
def append_items(*, order: Order, items: list[dict]) -> Order:
    """Дописать позиции в уже открытый заказ (официант досчитывает по ходу)."""
    if not items:
        raise OrderError("Пустой список позиций")
    _add_items(order, items)
    order.recalc_total()
    order.save(update_fields=["total"])
    return order


def create_request(*, customer_name: str, items: list[dict], table: str = "", comment: str = "") -> Order:
    """Заявка от клиента без авторизации (статус «Ждёт официанта»).

    Стол приходит из QR-кода на столе (если клиент отсканировал). Официант
    подтверждает заявку — стол уже проставлен, выбирать вручную не нужно.
    """
    if not items:
        raise OrderError("Пустой заказ")
    order = Order.objects.create(
        status=Order.Status.REQUESTED,
        customer_name=customer_name,
        table=table,
        comment=comment,
        public_token=uuid.uuid4(),
    )
    _add_items(order, items)
    order.recalc_total()
    order.save(update_fields=["total"])
    return order

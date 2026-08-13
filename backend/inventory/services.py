"""Складская логика, которой пользуются другие приложения: списание и закуп."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    PurchaseLine,
    PurchaseList,
    ReceiptItem,
    RecipeItem,
    StockItem,
    StockMovement,
)

CENT = Decimal("0.001")


def last_unit_costs(item_ids) -> dict[int, Decimal]:
    """{id товара: последняя известная цена за базовую единицу} по приходам."""
    rows = (
        ReceiptItem.objects.filter(item_id__in=list(item_ids), unit_cost__isnull=False)
        .order_by("item_id", "-receipt__created_at", "-id")
        .values("item_id", "unit_cost")
    )
    costs: dict[int, Decimal] = {}
    for row in rows:
        costs.setdefault(row["item_id"], row["unit_cost"])
    return costs


@transaction.atomic
def write_off_order_item(order_item, user=None) -> list[StockItem]:
    """Списать ингредиенты позиции заказа по тех карте блюда.

    Идемпотентно: повторный перевод позиции в «готово» ничего не спишет.
    Остаток может уйти в минус — это видно в остатках и чинится инвентаризацией;
    блокировать кухню из-за расхождений в учёте нельзя. Возвращает товары,
    которых не хватило.
    """
    if order_item.stock_written_off_at:
        return []

    lines = RecipeItem.objects.filter(
        product_id=order_item.product_id
    ).select_related("item")
    comment = f"Заказ №{order_item.order_id} · {order_item.product.name}"
    short: list[StockItem] = []

    for line in lines:
        need = line.quantity * order_item.quantity
        if line.item.quantity < need:
            short.append(line.item)
        line.item.apply_movement(
            -need, StockMovement.Kind.SALE, user=user, comment=comment
        )

    order_item.stock_written_off_at = timezone.now()
    order_item.save(update_fields=["stock_written_off_at"])
    return short


@transaction.atomic
def return_order_item(order_item, user=None) -> None:
    """Вернуть на склад то, что списали за позицию (позицию убрали из заказа)."""
    if not order_item.stock_written_off_at:
        return

    lines = RecipeItem.objects.filter(
        product_id=order_item.product_id
    ).select_related("item")
    comment = f"Возврат: заказ №{order_item.order_id} · {order_item.product.name}"

    for line in lines:
        line.item.apply_movement(
            line.quantity * order_item.quantity,
            StockMovement.Kind.RETURN,
            user=user,
            comment=comment,
        )

    order_item.stock_written_off_at = None
    order_item.save(update_fields=["stock_written_off_at"])


def get_or_build_purchase(date) -> PurchaseList:
    """Список закупа на дату: создать, если нет, и дописать новые нехватки.

    Уже существующие строки не трогаем — кладовщик мог поправить количество или
    отметить покупку. Дописываем только товары, которых сейчас мало и которых в
    списке ещё нет.
    """
    purchase, _ = PurchaseList.objects.get_or_create(date=date)
    listed = set(purchase.lines.values_list("item_id", flat=True))

    fresh = [
        PurchaseLine(
            purchase=purchase,
            item=item,
            quantity=item.shortage.quantize(CENT),
            is_auto=True,
        )
        for item in StockItem.objects.filter(
            is_active=True, min_quantity__isnull=False
        ).select_related("category")
        if item.id not in listed and item.is_low and item.shortage > 0
    ]
    if fresh:
        PurchaseLine.objects.bulk_create(fresh)
    return purchase

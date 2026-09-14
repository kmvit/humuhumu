"""Складская логика, которой пользуются другие приложения: списание и закуп."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from catalog.models import ModifierEffect

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


def portion_consumption(order_item) -> dict[int, tuple[StockItem, Decimal]]:
    """Расход на ОДНУ порцию позиции: тех карта варианта плюс опции.

    Считается в три шага, и порядок важен:

    1. состав проданного объёма — тех карта варианта;
    2. «заменить» и «убрать» правят уже набранный состав. Количество они
       берут отсюда же, а не из себя: «на овсяном» верно и для 0,33, и для
       0,7, и переживёт правку рецепта;
    3. «добавить» прибавляет своё фиксированное количество — «+ шот
       эспрессо» это 18 г зерна независимо от объёма напитка.

    Возвращает {id товара: (товар, количество)}.
    """
    из_карты = {
        line.item_id: (line.item, line.quantity)
        for line in RecipeItem.objects.filter(
            variant_id=order_item.variant_id
        ).select_related("item")
    }

    effects = list(
        ModifierEffect.objects.filter(
            modifier__orderitemmodifier__order_item=order_item
        ).select_related("item", "replacement")
    )

    # сначала замены и снятия — они опираются на состав из карты
    for effect in effects:
        if effect.kind == ModifierEffect.Kind.REMOVE:
            из_карты.pop(effect.item_id, None)
        elif effect.kind == ModifierEffect.Kind.SWAP:
            было = из_карты.pop(effect.item_id, None)
            if было is None:
                # в карте этого объёма товара нет — заменять нечего, и
                # подставлять замену «из воздуха» нельзя: списали бы то,
                # чего в напитке не было
                continue
            _, qty = было
            item, prev = из_карты.get(effect.replacement_id, (effect.replacement, Decimal("0")))
            из_карты[effect.replacement_id] = (item, prev + qty)

    for effect in effects:
        if effect.kind != ModifierEffect.Kind.ADD:
            continue
        item, prev = из_карты.get(effect.item_id, (effect.item, Decimal("0")))
        из_карты[effect.item_id] = (item, prev + effect.quantity)

    return из_карты


@transaction.atomic
def write_off_order_item(order_item, user=None) -> list[StockItem]:
    """Списать ингредиенты позиции заказа: тех карта варианта плюс опции.

    Идемпотентно: повторный перевод позиции в «готово» ничего не спишет.
    Остаток может уйти в минус — это видно в остатках и чинится инвентаризацией;
    блокировать кухню из-за расхождений в учёте нельзя. Возвращает товары,
    которых не хватило.
    """
    if order_item.stock_written_off_at:
        return []

    comment = f"Заказ №{order_item.order_id} · {order_item.display_name}"
    options = order_item.options_text
    if options:
        comment += f" ({options})"
    short: list[StockItem] = []

    for item, per_portion in portion_consumption(order_item).values():
        need = per_portion * order_item.quantity
        if not need:
            continue
        if item.quantity < need:
            short.append(item)
        item.apply_movement(
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

    comment = f"Возврат: заказ №{order_item.order_id} · {order_item.display_name}"
    options = order_item.options_text
    if options:
        comment += f" ({options})"

    # Возвращаем ровно то, что списали: тот же расчёт с теми же опциями.
    for item, per_portion in portion_consumption(order_item).values():
        if not per_portion:
            continue
        item.apply_movement(
            per_portion * order_item.quantity,
            StockMovement.Kind.RETURN,
            user=user,
            comment=comment,
        )

    order_item.stock_written_off_at = None
    order_item.save(update_fields=["stock_written_off_at"])


@transaction.atomic
def delete_receipt(receipt) -> None:
    """Удалить приход и откатить его влияние на остатки.

    При создании приход увеличил остатки (движения kind=receipt). Здесь эти
    движения удаляются, а остаток каждого затронутого товара пересчитывается как
    сумма оставшихся движений — как будто прихода и не было. Остаток может уйти в
    минус, если товар уже частично списали по тех картам, — это корректно
    показывает, что приход был ошибочным.
    """
    item_ids = list(receipt.items.values_list("item_id", flat=True))
    # движения именно этого прихода (FK receipt) — снимаем их вклад в остаток
    StockMovement.objects.filter(receipt=receipt).delete()
    for item in StockItem.objects.select_for_update().filter(id__in=item_ids):
        total = item.movements.aggregate(s=Sum("delta"))["s"] or Decimal("0")
        item.quantity = total
        item.save(update_fields=["quantity"])
    receipt.delete()


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

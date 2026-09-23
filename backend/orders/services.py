"""Бизнес-логика заказов."""
import uuid

from django.db import transaction

from catalog.models import Modifier, ProductVariant

from .models import Order, OrderItem, OrderItemModifier


class OrderError(Exception):
    pass


def _resolve_variant(line: dict) -> ProductVariant:
    """Вариант из строки заказа: по variant, или по product старого клиента.

    Товар без варианта однозначен, пока вариант один; закэшированный бандл
    с многовариантным товаром получит первый по порядку — это осознанная
    цена совместимости на переходный период.
    """
    qs = ProductVariant.objects.filter(
        is_active=True, product__is_available=True
    ).select_related("product")
    if line.get("variant"):
        variant = qs.filter(pk=line["variant"]).first()
    else:
        variant = qs.filter(product_id=line["product"]).order_by(
            "sort_order", "id"
        ).first()
    if variant is None:
        raise OrderError("Товар недоступен или не найден")
    return variant


def _resolve_modifiers(variant: ProductVariant, ids) -> list[Modifier]:
    """Проверить выбранные опции и вернуть их.

    Проверяем на сервере, а не доверяем клиенту: цена позиции считается из
    надбавок, и подставленная опция чужого блюда стоила бы заведению денег.
    Заодно ловим нарушение «выбрать ровно одно» — иначе в чек уехали бы и
    коровье, и овсяное разом.
    """
    if not ids:
        chosen = []
    else:
        chosen = list(
            Modifier.objects.filter(
                id__in=list(ids), group__products=variant.product_id, group__is_active=True
            ).select_related("group")
        )
        if len(chosen) != len(set(ids)):
            raise OrderError("Опция недоступна для этого блюда")
        for modifier in chosen:
            if modifier.is_stopped:
                raise OrderError(f"«{modifier.name}» временно недоступно (на стопе)")

    picked = {}
    for modifier in chosen:
        picked.setdefault(modifier.group_id, []).append(modifier)

    for group in variant.product.modifier_groups.filter(is_active=True):
        n = len(picked.get(group.id, []))
        if n < group.min_choices:
            raise OrderError(f"Выберите: {group.name}")
        if group.max_choices and n > group.max_choices:
            raise OrderError(f"«{group.name}» — можно выбрать не больше {group.max_choices}")
    return chosen


def _add_items(order: Order, items: list[dict]) -> None:
    """Добавить позиции к заказу, беря цены с сервера."""
    for line in items:
        variant = _resolve_variant(line)
        if variant.is_stopped:
            raise OrderError(f"«{variant.full_name}» временно недоступно (на стопе)")
        quantity = int(line.get("quantity", 1))
        if quantity < 1:
            raise OrderError("Количество должно быть положительным")
        modifiers = _resolve_modifiers(variant, line.get("modifiers") or [])
        guest = line.get("guest")
        item = OrderItem.objects.create(
            order=order,
            variant=variant,
            quantity=quantity,
            unit_price=variant.price,  # фиксируем цену на момент покупки
            guest=guest if guest else None,  # 0/None → общий
        )
        # Название и надбавку снимаем сейчас: через полгода опция может
        # подорожать, а чек обязан остаться тем, что видел гость.
        OrderItemModifier.objects.bulk_create(
            OrderItemModifier(
                order_item=item,
                modifier=modifier,
                name=modifier.name,
                price_delta=modifier.price_delta,
            )
            for modifier in modifiers
        )


@transaction.atomic
def create_order(*, waiter, items: list[dict], table: str = "", comment: str = "") -> Order:
    """Заказ, заведённый сотрудником сразу в работу (статус «Открыт»).

    В зале это заказ на стол. На стойке столов нет: гость называет позиции
    у окна, и заказ должен получить номер — иначе его нечем выкрикнуть,
    а гостю нечего ждать.
    """
    from core.models import SiteSettings

    if not items:
        raise OrderError("Пустой заказ")
    counter = SiteSettings.load().service_mode == SiteSettings.ServiceMode.COUNTER
    order = Order.objects.create(
        waiter=waiter,
        table="" if counter else table,
        comment=comment,
        status=Order.Status.OPEN,
        daily_number=next_daily_number() if counter else None,
    )
    _add_items(order, items)
    order.recalc_total()
    order.save(update_fields=["total"])
    return order


@transaction.atomic
def append_items(*, order: Order, items: list[dict]) -> Order:
    """Дописать позиции в уже открытый заказ (официант досчитывает по ходу)."""
    if not items:
        raise OrderError("Пустой список позиций")
    _add_items(order, items)
    # order пришёл из get_object() с prefetch — сбрасываем кэш items,
    # иначе recalc_total() просуммирует старый список без новых позиций
    order._prefetched_objects_cache = {}
    order.recalc_total()
    order.save(update_fields=["total"])
    return order


def next_daily_number() -> int:
    """Следующий номер заказа за сегодня. Обнуляется каждый день."""
    from django.db.models import Max
    from django.utils import timezone

    today = timezone.localdate()
    last = (
        Order.objects.filter(created_at__date=today)
        .aggregate(n=Max("daily_number"))["n"]
    )
    return (last or 0) + 1


def prepay_required() -> bool:
    """Ждёт ли заказ гостя оплаты, прежде чем уйти на кухню.

    Только формат «Стойка»: в зале за стол отвечает официант, и заявка
    ему нужна как раз до оплаты — иначе он не примет гостя вовсе.

    И только при работающей онлайн-оплате. Требовать предоплату, когда
    банк не подключён, — это не принимать заказы совсем: заплатить
    гостю нечем, и каждый заказ через четверть часа отменялся бы сам.
    """
    from core.models import SiteSettings
    from payments.acquiring import online_payment_available

    site = SiteSettings.load()
    if site.service_mode != SiteSettings.ServiceMode.COUNTER or not site.prepay_required:
        return False
    return site.online_payment_on and online_payment_available()


@transaction.atomic
def start_order(order: Order) -> Order:
    """Пустить оплаченный заказ в работу: номер выдачи и путь на станции.

    Номер даём здесь, а не при создании: брошенные неоплаченные заказы
    иначе выедали бы номера, и бариста выкрикивал бы в окно 47-й при
    дюжине проданных за день.
    """
    if order.status != Order.Status.UNPAID:
        return order
    order.status = Order.Status.OPEN
    if order.daily_number is None:
        order.daily_number = next_daily_number()
    order.save(update_fields=["status", "daily_number"])
    return order


@transaction.atomic
def create_request(
    *, customer_name: str, items: list[dict], table: str = "", comment: str = "", client=None
) -> Order:
    """Заказ от гостя без авторизации.

    В транзакции, как и заказ официанта: строка позиции может не пройти
    проверку (стоп, чужая опция, недоступное блюдо), и без отката в базе
    оставался бы пустой заказ — он же занял бы номер выдачи и повис бы у
    официанта в заявках.

    В зале это заявка: официант подтверждает её на стол, который пришёл из
    QR-кода. На стойке подтверждать некому и стола нет — заказ сразу уходит
    в работу, а гость ждёт свой номер. Если на стойке включена предоплата,
    заказ сперва ждёт денег: бар не должен готовить тому, кто передумал по
    дороге.
    """
    from core.models import SiteSettings

    if not items:
        raise OrderError("Пустой заказ")

    counter = SiteSettings.load().service_mode == SiteSettings.ServiceMode.COUNTER
    # На стойке с предоплатой заказ ждёт денег: ни номера, ни станций у
    # него пока нет (см. prepay_required и start_order).
    waits_payment = counter and prepay_required()
    if waits_payment:
        status = Order.Status.UNPAID
    elif counter:
        status = Order.Status.OPEN
    else:
        status = Order.Status.REQUESTED
    order = Order.objects.create(
        status=status,
        client=client,
        customer_name=customer_name,
        table="" if counter else table,
        comment=comment,
        public_token=uuid.uuid4(),
        daily_number=None if waits_payment else next_daily_number(),
    )
    _add_items(order, items)
    order.recalc_total()
    order.save(update_fields=["total"])
    return order

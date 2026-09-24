"""Фоновые задачи каталога: фото блюд и подсчёт ходовых опций."""
from __future__ import annotations

from celery import shared_task

from core.tenancy import organization_context

from .image_ai import run_generation
from .models import ImageGeneration


@shared_task
def generate_product_image(generation_id: int) -> None:
    """Нарисовать одно фото блюда.

    Идемпотентна: берётся только за генерации в статусе «рисуется».

    Заведение в воркере никто не выбирал — ни запроса, ни домена. Поэтому
    саму генерацию ищем в обход фильтра (по id), а дальше работаем строго
    от имени ЕЁ заведения: иначе настройки стиля и образцы посуды приехали
    бы из соседнего кафе.
    """
    generation = ImageGeneration.all_objects.filter(pk=generation_id).first()
    if generation is None or generation.status != ImageGeneration.Status.PENDING:
        return

    with organization_context(generation.organization):
        run_generation(generation)


#: За какой срок считаем «ходовые» опции. Месяц — это и смена сезона
#: (тыквенный сироп осенью обгонит карамель), и достаточно чеков, чтобы
#: топ не скакал от одного гостя.
PICKS_WINDOW_DAYS = 30

#: Сколько опций группы фронт показывает быстрыми чипами. Здесь оно нужно
#: только как ориентир: само поле picks пересчитывается для всех.
TOP_MODIFIERS = 3


@shared_task
def refresh_modifier_picks_task() -> None:
    """Пересчитать, какие опции гости выбирают чаще, — по всем заведениям.

    Считаем по позициям заказов, а не по расходу склада: склад знает
    граммы, а не выбор гостя. У сиропа тех карты может не быть вовсе, а
    безлактозное и обычное молоко на складе нередко лежат одной позицией —
    по ним не отличить, кто что просил.

    Отменённые заказы не считаем: гость эту опцию так и не получил, а
    ошибочно заведённый и отменённый заказ не должен двигать топ.
    """
    from datetime import timedelta

    from django.db.models import Count
    from django.utils import timezone

    from core.models import Organization
    from orders.models import Order, OrderItemModifier

    from .models import Modifier

    since = timezone.now() - timedelta(days=PICKS_WINDOW_DAYS)
    for org in Organization.objects.all():
        # Строго от имени заведения: и заказы, и опции менеджер фильтрует
        # сам, иначе в общей установке топ приехал бы из соседнего кафе.
        with organization_context(org):
            counted = dict(
                OrderItemModifier.objects.filter(
                    order_item__order__created_at__gte=since
                )
                .exclude(order_item__order__status=Order.Status.CANCELLED)
                .values_list("modifier_id")
                .annotate(n=Count("id"))
                .values_list("modifier_id", "n")
            )
            stale = [
                m
                for m in Modifier.objects.all()
                if m.picks != counted.get(m.id, 0)
            ]
            for m in stale:
                m.picks = counted.get(m.id, 0)
            if stale:
                Modifier.objects.bulk_update(stale, ["picks"])

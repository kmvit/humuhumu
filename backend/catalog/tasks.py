"""Фоновые задачи каталога: генерация фото блюд."""
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

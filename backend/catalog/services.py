"""Счётчики генерации фото: месячный лимит и потраченные деньги."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from .models import ImageGeneration, ImageQuota


def image_quota() -> tuple[int, int]:
    """(израсходовано, лимит) генераций фото в текущем месяце."""
    month = timezone.localdate().replace(day=1)
    row = ImageQuota.objects.filter(month=month).first()
    if row is None:
        return 0, ImageQuota.MONTHLY_LIMIT
    return row.used, row.limit


def consume_image_quota(count: int = 1) -> None:
    """Списать генерации — до обращения к модели.

    Списываем за попытку: запрос к OpenRouter оплачен нами, чем бы он ни
    кончился. Если картинки не вышли не по вине заведения, лимит
    возвращается руками — в админке есть «Докуплено».
    """
    if count <= 0:
        return
    month = timezone.localdate().replace(day=1)
    row, created = ImageQuota.objects.get_or_create(month=month, defaults={"used": count})
    if not created:
        # F(), а не чтение-запись: две пачки разом не затрут счёт друг друга.
        ImageQuota.objects.filter(pk=row.pk).update(used=F("used") + count)


def month_spend() -> Decimal:
    """Сколько генерации стоили нам в этом месяце, в долларах.

    Владельцу показываем деньги, а не только остаток счётчика: цена
    приходит от OpenRouter по каждому запросу и хранится в генерации.
    """
    month = timezone.localdate().replace(day=1)
    total = ImageGeneration.objects.filter(created_at__date__gte=month).aggregate(
        total=Sum("cost_usd")
    )["total"]
    return total or Decimal("0")

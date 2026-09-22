"""Счётчики генерации фото: месячный лимит и потраченные деньги."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from core.tenancy import current_organization_or_none

from .models import ImageGeneration, ImageQuota


def _subscription():
    """Подписка текущего заведения — или None, если его не выбрали.

    Не core.license.local_subscription: тот спрашивает current_organization(),
    а она в общей админке хаба (заведений много, текущего нет) бросает
    исключение. Сюда же ходит колонка «Лимит месяца» в списке лимитов —
    и уронила бы страницу целиком. Нет заведения — нет и подписки.
    """
    org = current_organization_or_none()
    if org is None:
        return None
    return getattr(org, "subscription", None)


def images_enabled() -> bool:
    """Включена ли генерация фото у этого заведения.

    Рубильник стоит в подписке — её держит «Падача» в общей админке.
    Внешняя установка подписок не хранит (там работает сверка по HTTP), и
    для неё оставляем включённой: запретить её всё равно нечем.
    """
    subscription = _subscription()
    if subscription is None:
        return True
    return subscription.images_enabled


def monthly_limit() -> int:
    """Сколько картинок заведению положено в месяц.

    Число задаётся в подписке: расход по картинкам у всех разный, и
    тарифной сеткой (она у нас по формату зала) это не описать.
    """
    subscription = _subscription()
    if subscription is None:
        return ImageQuota.MONTHLY_LIMIT
    return subscription.image_limit


def image_quota() -> tuple[int, int]:
    """(израсходовано, лимит) генераций фото в текущем месяце.

    Лимит месяца = положенное подпиской + разовая добавка этого месяца
    («Докуплено» в админке): первое — правило, второе — исключение.
    """
    month = timezone.localdate().replace(day=1)
    limit = monthly_limit()
    row = ImageQuota.objects.filter(month=month).first()
    if row is None:
        return 0, limit
    return row.used, limit + row.extra


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

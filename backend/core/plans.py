"""Тарифы и что на каждом доступно — единственное место, где они существуют.

Лэндинг «Падачи» продаёт три ступени, и до этого модуля код никак их не
подкреплял: любая установка получала весь функционал. Здесь тариф заведения
(SiteSettings.plan) превращается в набор фич, а permission-классы закрывают
эндпоинты, которых в тарифе нет.

Вьюхи проверяют ФИЧУ («мне нужен склад»), а не тариф («мне нужен Максимум»):
когда тарифная сетка перекроится, поменяется один словарь, а не десяток вьюх.

База — меню, заказы, столы/стойка, оплаты, отчёты владельца — фичей не
считается и не проверяется: она есть на любом тарифе.
"""
from rest_framework.permissions import BasePermission

from .models import SiteSettings

#: Что включает каждый тариф. Источник правды — тарифная сетка лэндинга.
PLAN_FEATURES: dict[str, frozenset[str]] = {
    # Старт: зал или стойка, меню, QR-заказ, оплаты, отчёты — только база.
    SiteSettings.Plan.START: frozenset(),
    # Зал: + экраны кухни и бара (канбан, тайминги, «К подаче»).
    SiteSettings.Plan.HALL: frozenset({"stations"}),
    # Максимум: + склад с приходом по фото, смены/зарплата, финансы, бонусы.
    SiteSettings.Plan.MAX: frozenset(
        {"stations", "inventory", "shifts", "finance", "loyalty"}
    ),
}

#: С какого тарифа фича появляется — для текста ошибки и апселла на фронте.
FEATURE_PLAN_TITLE = {
    "stations": "Зал",
    "inventory": "Максимум",
    "shifts": "Максимум",
    "finance": "Максимум",
    "loyalty": "Максимум",
}


def current_plan() -> str:
    """Тариф текущего заведения.

    Если подписка лежит в этой же установке (общая база) — главная она:
    тариф назначается в разделе «Подписки», и настройки заведения не
    должны с ней спорить. Иначе берём поле настроек: так живут отдельные
    установки, которым тариф привозит сверка лицензии.
    """
    from .license import local_subscription

    subscription = local_subscription()
    if subscription is not None:
        return subscription.plan
    return SiteSettings.load().plan


def features(plan: str | None = None) -> frozenset[str]:
    """Набор фич тарифа; без аргумента — тарифа текущего заведения."""
    if plan is None:
        plan = current_plan()
    return PLAN_FEATURES.get(plan, frozenset())


def requires_feature(name: str) -> type[BasePermission]:
    """Permission-класс: эндпоинт отвечает, только если фича входит в тариф.

    Ставится РЯДОМ с ролевой проверкой, не вместо неё:
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory].
    """

    class _RequiresFeature(BasePermission):
        feature = name
        message = f"Доступно на тарифе «{FEATURE_PLAN_TITLE.get(name, '—')}»."

        def has_permission(self, request, view):
            return name in features()

    _RequiresFeature.__name__ = f"Requires_{name}"
    return _RequiresFeature


RequiresStations = requires_feature("stations")
RequiresInventory = requires_feature("inventory")
RequiresShifts = requires_feature("shifts")
RequiresFinance = requires_feature("finance")
RequiresLoyalty = requires_feature("loyalty")

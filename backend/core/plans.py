"""Тарифы и что на каждом доступно — единственное место, где они существуют.

Лэндинг «Падачи» продаёт две ступени, и делит их формат заведения, а не
набор функций: «Стойка» — точка без зала, «Зал» — с посадкой и официантами.
Функционал полный на обоих. Так решено после первых продаж: обе точки взяли
верхний тариф и прошли мимо нижних, а кофейне без склада и себестоимости
продукт не нужен вовсе — функциональная граница просто мешала покупать.

Единственная разница в возможностях — экраны кухни и бара (`stations`): в
режиме стойки один человек собирает заказ целиком, делить его между
станциями не по чему. То есть это не забор, а следствие формата.

Тариф заведения (SiteSettings.plan) превращается здесь в набор фич, а
permission-классы закрывают эндпоинты, которых в тарифе нет. Вьюхи проверяют
ФИЧУ («мне нужен склад»), а не тариф: когда сетка перекраивается снова,
меняется этот словарь, а не десяток вьюх.

База — меню, заказы, столы/стойка, оплаты, отчёты владельца — фичей не
считается и не проверяется: она есть на любом тарифе.
"""
from rest_framework.permissions import BasePermission

from .models import SiteSettings

#: Всё, что вообще бывает платного, — полный набор верхнего тарифа.
ALL_FEATURES = frozenset({"stations", "inventory", "shifts", "finance", "loyalty"})

#: Что включает каждый тариф. Источник правды — тарифная сетка лэндинга.
PLAN_FEATURES: dict[str, frozenset[str]] = {
    # Стойка: всё, кроме станций — в этом формате их некому разделять.
    SiteSettings.Plan.COUNTER: ALL_FEATURES - {"stations"},
    # Зал: всё.
    SiteSettings.Plan.HALL: ALL_FEATURES,
}

#: С какого тарифа фича появляется — для текста ошибки и апселла на фронте.
#: Остальные фичи входят в оба тарифа, поэтому их здесь нет.
FEATURE_PLAN_TITLE = {"stations": "Зал"}

#: Формат обслуживания у каждого тарифа. Цена зависит от формата, поэтому
#: выбирает его не заведение: подписка ставит тариф, тариф ставит формат.
PLAN_SERVICE_MODE: dict[str, str] = {
    SiteSettings.Plan.COUNTER: SiteSettings.ServiceMode.COUNTER,
    SiteSettings.Plan.HALL: SiteSettings.ServiceMode.HALL,
}


def mode_for_plan(plan: str) -> str:
    """Формат обслуживания, положенный тарифу. Незнакомый тариф — зал."""
    return PLAN_SERVICE_MODE.get(plan, SiteSettings.ServiceMode.HALL)


def apply_plan(site: SiteSettings, plan: str) -> bool:
    """Привести настройки заведения к тарифу. True, если что-то изменилось.

    Одно место на всех, кто назначает тариф снаружи: подписка в общей
    установке и сверка лицензии в отдельной. Формат пишется вместе с
    тарифом — иначе заведение платило бы за зал, а работало через стойку.
    """
    mode = mode_for_plan(plan)
    if site.plan == plan and site.service_mode == mode:
        return False
    site.plan = plan
    site.service_mode = mode
    site.save(update_fields=["plan", "service_mode"])
    return True


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

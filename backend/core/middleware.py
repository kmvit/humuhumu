"""Блокировка API при неоплаченной подписке.

Middleware, а не permission на каждой вьюхе: «заблокировано» должно
закрывать весь рабочий API разом, и список исключений в одном месте
читается лучше, чем сотня правок по вьюсетам.

Что остаётся открытым и почему:
- /api/site/, GET меню — гость у витрины не виноват, что кафе не оплатило;
- /api/auth/, /api/users/me/ — персонал должен суметь войти и увидеть
  экран блокировки, а не голую ошибку;
- /api/license/ — кнопка «Проверить оплату» обязана работать у
  заблокированных, иначе не разблокироваться без перезапуска;
- /api/payments/callback/ — уведомления банка идут не от нас и не должны
  теряться.
"""
import re

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from .license import BLOCKED, effective_status
from .models import Organization
from .tenancy import NoOrganizationSelected, set_current_organization

_OPEN_ALWAYS = (
    "/api/site/",
    "/api/auth/",
    "/api/users/me/",
    "/api/license/",
    "/api/payments/callback/",
)
_OPEN_GET = re.compile(r"^/api/(products|categories)/")


class LicenseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith("/api/") and not self._allowed(request):
            if effective_status() == BLOCKED:
                return JsonResponse(
                    {
                        "detail": "Подписка не оплачена — сервис приостановлен.",
                        "code": "license_blocked",
                    },
                    status=402,
                )
        return self.get_response(request)

    @staticmethod
    def _allowed(request) -> bool:
        if any(request.path.startswith(p) for p in _OPEN_ALWAYS):
            return True
        return request.method in ("GET", "HEAD") and bool(
            _OPEN_GET.match(request.path)
        )


class TenantMiddleware:
    """Опознать заведение по домену запроса.

    В общей установке несколько заведений живут в одной базе и различаются
    доменом: monti.padacha.ru — одно, kofeinya.padacha.ru — другое. Это
    ЕДИНСТВЕННОЕ место, где заведение выбирается; дальше весь код берёт
    его из current_organization().

    Отдельная установка (одно заведение в базе) работает как раньше: домен
    у заведения может быть пуст, и тогда подойдёт любой хост из
    DJANGO_ALLOWED_HOSTS — иначе после обновления перестали бы открываться
    уже работающие кафе.

    Стоит ДО LicenseMiddleware: тот проверяет подписку заведения, а какого
    именно — знает только этот.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    #: Админка — инструмент поддержки «Падачи», а не заведения. Она обязана
    #: открываться и по служебному адресу сервера, где никакого заведения
    #: нет: иначе, сменив домен клиенту, в неё было бы не попасть.
    ADMIN_PREFIX = "/admin/"

    #: Надтенантные пути: отвечают от имени установки, а не заведения.
    #: Выдача лицензии адресована ключом, а не доменом, и должна работать
    #: на любом хосте — иначе внешней установке некуда стучаться.
    AUTHORITY_PATHS = ("/api/license/",)

    def __call__(self, request):
        if request.path in self.AUTHORITY_PATHS:
            return self.get_response(request)
        host = Organization.normalize_host(request.get_host())
        org = Organization.objects.filter(domain=host).first()
        is_admin = request.path.startswith(self.ADMIN_PREFIX)

        if org is None:
            # Домен никому не назначен. На отдельной установке это норма —
            # заведение там одно, его и обслуживаем.
            only = Organization.objects.order_by("pk")[:2]
            if len(only) == 1:
                org = only[0]
            elif not is_admin:
                return JsonResponse(
                    {"detail": "Заведение по этому адресу не найдено.",
                     "code": "unknown_tenant"},
                    status=404,
                )

        # Поддержка «Падачи»: суперпользователь может открыть админку от
        # имени любого заведения, не заходя на его домен (действие
        # «Работать от имени» в списке заведений). Только суперпользователь
        # и только для админки: ни API клиента, ни гостевые страницы так
        # подменить нельзя.
        override = request.session.get("tenant_override") if hasattr(request, "session") else None
        if (
            override
            and request.path.startswith("/admin/")
            and getattr(request.user, "is_superuser", False)
        ):
            chosen = Organization.objects.filter(pk=override).first()
            if chosen is not None:
                org = chosen

        if org is None:
            # Админка по служебному адресу: заведение ещё не выбрано.
            # Список заведений откроется, остальные разделы попросят выбрать.
            return self._admin_without_tenant(request)

        if not org.is_active:
            return JsonResponse(
                {"detail": "Заведение отключено.", "code": "tenant_disabled"},
                status=404,
            )

        set_current_organization(org)
        request.organization = org
        if is_admin:
            return self._admin_without_tenant(request)
        return self.get_response(request)

    def _admin_without_tenant(self, request):
        """Пройти запрос админки, мягко обработав «заведение не выбрано».

        Раздел, которому нужно заведение, без выбора падал бы пятисоткой.
        Вместо этого возвращаем на список заведений с понятной подсказкой.
        """
        try:
            return self.get_response(request)
        except NoOrganizationSelected:
            messages.warning(
                request,
                "Сначала выберите заведение: отметьте его и примените "
                "действие «Работать от имени».",
            )
            return redirect("admin:core_organization_changelist")

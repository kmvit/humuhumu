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

from django.http import JsonResponse

from .license import BLOCKED, effective_status

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

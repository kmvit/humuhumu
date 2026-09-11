"""Выдача лицензии внешним установкам.

Общая установка знает подписки всех заведений — значит она и отвечает на
вопрос «оплачено ли», когда его задаёт инстанс, живущий отдельно (кафе на
своём сервере, демо-стенд). Контракт тот же, что был у пульта: ответ
подписан HMAC с ключом лицензии, иначе блокировка обходилась бы подменой
ответа или локальным прокси.

Заведениям ЭТОЙ установки эндпоинт не нужен: их подписка лежит рядом, в
базе, и читается напрямую (core.license.local_subscription).
"""
import hashlib
import hmac
import json

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.models import Organization


def _canonical(data: dict) -> bytes:
    """Канонизация должна совпадать с инстансом до байта."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


@csrf_exempt
def license_view(request: HttpRequest):
    """POST /api/license/  {"key": "...", "version": "..."}"""
    if request.method != "POST":
        return JsonResponse({"detail": "Только POST"}, status=405)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Некорректный JSON"}, status=400)

    key = str(payload.get("key", ""))
    org = (
        Organization.objects.filter(license_key=key).first()
        if key
        else None
    )
    subscription = getattr(org, "subscription", None) if org else None
    if subscription is None:
        # Не различаем «нет ключа», «ключ чужой» и «подписки нет» —
        # подсказывать перебирающему нечего.
        return JsonResponse({"detail": "Ключ не найден"}, status=404)

    org.last_seen_at = timezone.now()
    org.last_version = str(payload.get("version", ""))[:40]
    org.save(update_fields=["last_seen_at", "last_version"])

    data = {
        "plan": subscription.plan,
        "paid_until": subscription.paid_until.isoformat(),
        "grace_days": subscription.grace_days,
        "internal": subscription.is_internal,
        "status": subscription.status(),
        # По issued_at инстанс отличает свежий ответ от сохранённого
        # старого: кэшу старше нескольких дней доверять нельзя.
        "issued_at": timezone.now().isoformat(),
    }
    sign = hmac.new(key.encode(), _canonical(data), hashlib.sha256).hexdigest()
    return JsonResponse({"data": data, "sign": sign})

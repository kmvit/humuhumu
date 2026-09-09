"""Приём уведомлений от банка об оплате.

Отдельная публичная ручка, а не действие на заказе: уведомление приходит
от банка, без сессии сотрудника и без JWT. Подлинность подтверждает сам
провайдер — подписью (Т-Касса) или встречным запросом статуса (Сбер),
см. payments/acquiring.py.
"""
import logging

from django.db import transaction
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .acquiring import get_acquirer
from .models import Payment
from .services import apply_payment_result

logger = logging.getLogger(__name__)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def callback(request, provider: str):
    """POST /api/payments/callback/<provider>/ — уведомление банка.

    Отвечаем 200 почти всегда и намеренно: на любой другой код банк будет
    слать уведомление повторно, часами. Если оно чужое или подделано — мы
    просто ничего не делаем, но подтверждаем получение.
    """
    acquirer = get_acquirer(provider)
    payload = request.data if isinstance(request.data, dict) else {}

    result = acquirer.read_callback(payload, dict(request.headers))
    if result is None:
        logger.warning("Уведомление %s не прошло проверку", provider)
        return Response({"ok": False}, status=200)

    # Платёж ещё в работе: гость открыл страницу банка, но не заплатил.
    # Заказ трогать рано — иначе отменим его на полпути.
    if result.pending:
        return Response({"ok": True})

    payment = Payment.objects.filter(
        external_id=result.external_id, provider=acquirer.name
    ).first()
    if payment is None:
        logger.warning("Уведомление %s: платёж %s не найден", provider, result.external_id)
        return Response({"ok": False}, status=200)

    # Банки повторяют уведомления, и повтор по уже оплаченному заказу
    # переписал бы closed_at и closed_by. Отвечаем «принято» и выходим.
    if payment.status == Payment.Status.SUCCEEDED:
        return Response({"ok": True})

    with transaction.atomic():
        apply_payment_result(
            payment, success=result.success, fiscal_receipt=result.fiscal_receipt
        )
    logger.info(
        "Оплата %s: платёж %s → %s",
        provider, result.external_id, "успех" if result.success else "отказ",
    )
    return Response({"ok": True})

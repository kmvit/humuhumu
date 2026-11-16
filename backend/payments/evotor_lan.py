"""Authenticated order snapshots for direct tablet-to-Evotor draft delivery."""
from decimal import Decimal
from uuid import UUID
from django.db import transaction
from rest_framework.exceptions import ValidationError
from .models import EvotorLanTransfer, Payment
from orders.models import Order


def snapshot(order, tax):
    if tax not in ("NO_VAT", "VAT_0", "VAT_10"):
        raise ValidationError("Выберите ставку НДС для пробного чека: без НДС, 0% или 10%.")
    if order.status not in (Order.Status.OPEN, Order.Status.UNPAID) or order.paid_at:
        raise ValidationError("Можно передать только неоплаченный открытый заказ.")
    if Payment.objects.filter(order=order, status=Payment.Status.PENDING).exists():
        raise ValidationError("По заказу уже ожидается другая оплата. Завершите её перед передачей.")
    if order.bonus_spent:
        raise ValidationError("Передача заказа со списанием бонусов пока не поддерживается.")
    positions = []
    total = Decimal("0")
    for item in order.items.select_related("variant__product").prefetch_related("modifiers"):
        price = item.unit_price + item.modifiers_total
        if price <= 0 or item.quantity < 1:
            raise ValidationError("В пробном чеке нужны позиции с положительной ценой и количеством.")
        name = item.display_name + (" · " + item.options_text if item.options_text else "")
        if len(name) > 128:
            raise ValidationError("Название позиции с опциями длиннее 128 символов.")
        positions.append(dict(name=name, price=str(price), quantity=item.quantity, tax=tax))
        total += price * item.quantity
    if not positions or len(positions) > 100 or total != order.total:
        raise ValidationError("Проверьте позиции и итог заказа перед передачей.")
    return dict(order_id=order.pk, positions=positions, total=str(total), draft_only=True)


@transaction.atomic
def prepare(order_id, tax, device_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    payload = snapshot(order, tax)
    try:
        payload["device_id"] = str(UUID(str(device_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Подключите планшет к кассе заново.")
    existing = EvotorLanTransfer.objects.filter(order=order).first()
    if existing:
        if existing.payload != payload:
            raise ValidationError("Заказ изменён после передачи. Проверьте ранее открытый чек на кассе; повторная отправка заблокирована.")
        return existing
    return EvotorLanTransfer.objects.create(order=order, payload=payload)


def data(transfer):
    return dict(id=str(transfer.id), payload=transfer.payload, result=transfer.result)


@transaction.atomic
def record_result(order_id, body):
    try:
        identifier = UUID(str(body.get("id")))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Некорректный ID передачи.")
    transfer = EvotorLanTransfer.objects.select_for_update().filter(order_id=order_id, pk=identifier).first()
    if not transfer:
        raise ValidationError("Передача заказа не найдена.")
    result = body.get("result")
    if not isinstance(result, dict) or result.get("status") not in ("receipt_opened", "error", "needs_review"):
        raise ValidationError("Это не результат открытия чека.")
    clean = {k: str(result.get(k, ""))[:500] for k in ("status", "message", "receipt_uuid")}
    clean["retryable"] = result.get("retryable") is True and clean["status"] == "error"
    if transfer.result and not transfer.result.get("retryable") and transfer.result != clean:
        raise ValidationError("Результат уже сохранён. Проверьте чек на кассе.")
    transfer.result = clean
    transfer.save(update_fields=["result"])
    return transfer

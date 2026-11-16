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
def prepare(order_id, tax, device_id, payment_tracking=False):
    order = Order.objects.select_for_update().get(pk=order_id)
    payload = snapshot(order, tax)
    try:
        payload["device_id"] = str(UUID(str(device_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Подключите планшет к кассе заново.")
    existing = EvotorLanTransfer.objects.filter(order=order).first()
    # Existing transfers retain their original tracking policy. Never retroactively settle a pilot draft.
    if (existing and existing.payload.get("payment_tracking")) or (not existing and payment_tracking):
        payload["payment_tracking"] = True
    if existing:
        if existing.payload != payload:
            raise ValidationError("Заказ изменён после передачи. Проверьте ранее открытый чек на кассе; повторная отправка заблокирована.")
        return existing
    return EvotorLanTransfer.objects.create(order=order, payload=payload)


def data(transfer):
    return dict(id=str(transfer.id), payload=transfer.payload, result=transfer.result)


@transaction.atomic
def record_result(order_id, body, user=None, allow_payment=False):
    try:
        identifier = UUID(str(body.get("id")))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Некорректный ID передачи.")
    order = Order.objects.select_for_update().get(pk=order_id)
    transfer = EvotorLanTransfer.objects.select_for_update().filter(order_id=order_id, pk=identifier).first()
    if not transfer:
        raise ValidationError("Передача заказа не найдена.")
    result = body.get("result")
    if isinstance(result, dict) and result.get("status") == "receipt_paid":
        return record_paid(transfer, order, result, user, allow_payment)
    if transfer.result.get("status") == "receipt_paid":
        # A delayed opened response must not undo an already committed payment.
        return transfer
    if not isinstance(result, dict) or result.get("status") not in ("receipt_opened", "error", "needs_review"):
        raise ValidationError("Это не результат открытия чека.")
    clean = {k: str(result.get(k, ""))[:500] for k in ("status", "message", "receipt_uuid")}
    clean["retryable"] = result.get("retryable") is True and clean["status"] == "error"
    transition = transfer.result.get("status") == "receipt_opened" and clean["status"] == "needs_review" and transfer.result.get("receipt_uuid") == clean["receipt_uuid"]
    if transfer.result and not transfer.result.get("retryable") and not transition and transfer.result != clean:
        raise ValidationError("Результат уже сохранён. Проверьте чек на кассе.")
    transfer.result = clean
    transfer.save(update_fields=["result"])
    return transfer


def record_paid(transfer, order, result, user, allow_payment):
    """Called inside record_result's transaction with the order and transfer locked.

    An authenticated staff tablet relays the terminal's provider-verified receipt.
    This is a trusted-LAN pilot, not a public payment webhook.
    """
    from decimal import InvalidOperation
    from payments.services import apply_payment_result
    if not allow_payment or not transfer.payload.get("payment_tracking"):
        raise ValidationError("Подтверждение оплаты для этой передачи не включено.")
    try:
        receipt_uuid = str(UUID(str(result.get("receipt_uuid"))))
        device_id = str(UUID(str(result.get("device_id"))))
        amount = Decimal(str(result.get("amount")))
        session_number = int(result.get("session_number"))
        receipt_number = str(result.get("receipt_number", ""))
        reported_order = int(result.get("order_id"))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2 or session_number <= 0 or not receipt_number.isdigit() or int(receipt_number) <= 0 or len(receipt_number) > 32:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError("Некорректные реквизиты завершённого чека.")
    if device_id != transfer.payload.get("device_id") or reported_order != order.pk or amount != Decimal(transfer.payload["total"]):
        raise ValidationError("Чек не соответствует кассе, заказу или сумме передачи.")
    previous_uuid = transfer.result.get("receipt_uuid")
    if previous_uuid and previous_uuid != receipt_uuid:
        raise ValidationError("Касса вернула другой чек.")
    method = result.get("method")
    if method not in (Payment.Method.CASH, Payment.Method.CARD):
        raise ValidationError("Способ оплаты пока не поддерживается.")
    clean = dict(status="receipt_paid", message="Оплачено на кассе", receipt_uuid=receipt_uuid,
                 device_id=device_id, order_id=order.pk, amount=str(amount), method=method,
                 session_number=session_number, receipt_number=receipt_number, retryable=False)
    if transfer.result.get("status") == "receipt_paid":
        if transfer.result != clean:
            raise ValidationError("Подтверждение оплаты отличается от сохранённого.")
        return transfer
    if transfer.result.get("status") not in (None, "receipt_opened"):
        raise ValidationError("Эта передача требует ручной проверки.")
    # Recheck the live order, not the tablet's cached card. Never overwrite another payment.
    current = snapshot(order, transfer.payload["positions"][0]["tax"])
    if any(current[k] != transfer.payload[k] for k in current):
        raise ValidationError("Заказ изменён после передачи: оплату нужно сверить вручную.")
    external_id = f"evotor:{device_id}:{receipt_uuid}"
    if Payment.objects.filter(external_id=external_id).exists():
        raise ValidationError("Этот чек уже связан с другим платежом.")
    payment = Payment.objects.create(order=order, purpose=Payment.Purpose.ORDER,
        status=Payment.Status.PENDING, provider="evotor_lan", external_id=external_id,
        amount=amount, method=method, kassa_receipt_id=receipt_uuid)
    apply_payment_result(payment, success=True, user=user)
    transfer.result = clean
    transfer.save(update_fields=["result"])
    return transfer

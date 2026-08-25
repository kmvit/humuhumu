"""Оплата заказа через кассу-терминал: старт и применение результата.

Единая точка перевода статусов, чтобы и дев-эмуляция, и будущий вебхук
реального провайдера дёргали одну и ту же логику.
"""
from django.db import transaction
from django.utils import timezone

from orders.models import Order

from .models import Payment
from .providers import get_provider


class PaymentError(Exception):
    pass


@transaction.atomic
def record_manual_payment(order: Order, method: str, user=None) -> Order:
    """Ручная оплата на кассе (нал/карта без терминала).

    Создаёт запись платежа и закрывает заказ — чтобы у каждого оплаченного
    заказа был Payment (единый реестр), как и при оплате через терминал.
    """
    pm = method if method in Payment.Method.values else Payment.Method.CASH
    Payment.objects.create(
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.SUCCEEDED,
        amount=order.total,
        order=order,
        method=pm,
        provider="manual",
    )
    order.status = Order.Status.PAID
    order.pay_method = (
        Order.PayMethod.CASH if pm == Payment.Method.CASH else Order.PayMethod.CARD
    )
    order.closed_by = user
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "pay_method", "closed_by", "closed_at"])
    return order


@transaction.atomic
def start_terminal_payment(order: Order, method: str = Payment.Method.CARD) -> Payment:
    """Отправить заказ на терминал: создать платёж и перевести заказ в «к оплате»."""
    if order.status != Order.Status.OPEN:
        raise PaymentError("Отправить на оплату можно только открытый заказ")
    if method not in Payment.Method.values:
        method = Payment.Method.CARD
    provider = get_provider()
    payment = Payment.objects.create(
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.PENDING,
        amount=order.total,
        order=order,
        method=method,
        provider=provider.name,
    )
    provider.start(payment)
    order.status = Order.Status.AWAITING
    order.save(update_fields=["status"])
    return payment


@transaction.atomic
def apply_payment_result(payment: Payment, *, success: bool, fiscal_receipt: str = "", user=None) -> Order:
    """Применить результат оплаты (успех/отказ) к платежу и заказу.

    Успех: заказ → оплачен, фиксируем способ и фискальный чек.
    Отказ/отмена: заказ возвращается в «открыт» — можно повторить или взять нал.
    Эту функцию вызывает и дев-эмуляция, и будущий вебхук провайдера.
    """
    order = payment.order
    if success:
        payment.status = Payment.Status.SUCCEEDED
        payment.fiscal_receipt = fiscal_receipt or payment.fiscal_receipt
        payment.save(update_fields=["status", "fiscal_receipt", "updated_at"])
        if order:
            order.status = Order.Status.PAID
            order.pay_method = (
                Order.PayMethod.CASH
                if payment.method == Payment.Method.CASH
                else Order.PayMethod.CARD
            )
            order.fiscal_receipt = fiscal_receipt or order.fiscal_receipt
            order.closed_by = user
            order.closed_at = timezone.now()
            order.save(update_fields=["status", "pay_method", "fiscal_receipt", "closed_by", "closed_at"])
    else:
        payment.status = Payment.Status.CANCELLED
        payment.save(update_fields=["status", "updated_at"])
        if order and order.status == Order.Status.AWAITING:
            order.status = Order.Status.OPEN
            order.save(update_fields=["status"])
    return order

"""Оплата заказа: наличными на кассе, через терминал или картой онлайн.

Три способа стартуют по-разному, но сходятся в одной точке —
apply_payment_result. Она и переводит статусы, чтобы дев-эмуляция,
уведомление банка и ручное подтверждение кассиром не разъезжались
в трёх разных реализациях.
"""
from django.db import transaction
from django.utils import timezone

from orders.models import Order

from .acquiring import AcquiringError, get_acquirer
from .models import Payment
from .providers import get_provider


class PaymentError(Exception):
    pass


def accrue_bonuses(order: Order) -> None:
    """Начислить бонусы за оплаченный заказ, если программа включена.

    Импорт внутри функции: loyalty знает про orders, и связь в обе стороны
    на уровне модулей замкнула бы их друг на друга.
    """
    from loyalty.services import earn_for_order

    earn_for_order(order)


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
        # деньгами берём чек за вычетом списанных бонусов
        amount=order.payable,
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
    accrue_bonuses(order)
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
        amount=order.payable,
        order=order,
        method=method,
        provider=provider.name,
    )
    provider.start(payment)
    order.status = Order.Status.AWAITING
    order.save(update_fields=["status"])
    return payment


@transaction.atomic
def start_online_payment(order: Order, *, return_url: str) -> tuple[Payment, str]:
    """Оплата картой онлайн: создать платёж у банка и вернуть ссылку для гостя.

    Заказ в «к оплате» здесь НЕ переводим, в отличие от терминала: гость
    может закрыть страницу банка и вернуться платить наличными, а заказ,
    зависший в «к оплате», официант закрыть не сможет. Статус меняется
    только по факту оплаты — в apply_payment_result.
    """
    if order.status not in (Order.Status.OPEN, Order.Status.REQUESTED):
        raise PaymentError("Оплатить можно только незакрытый заказ")

    acquirer = get_acquirer()
    if not acquirer.configured():
        raise PaymentError("Онлайн-оплата у заведения не подключена")

    payment = Payment.objects.create(
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.PENDING,
        amount=order.payable,
        order=order,
        method=Payment.Method.CARD,
        provider=acquirer.name,
    )
    try:
        url = acquirer.create(payment, return_url=return_url)
    except AcquiringError as e:
        # Платёж-пустышку не оставляем: он бы висел в реестре как
        # «создан» и портил сверку с банком.
        payment.delete()
        raise PaymentError(str(e)) from e
    return payment, url


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
            accrue_bonuses(order)
    else:
        payment.status = Payment.Status.CANCELLED
        payment.save(update_fields=["status", "updated_at"])
        if order and order.status == Order.Status.AWAITING:
            order.status = Order.Status.OPEN
            order.save(update_fields=["status"])
    return order

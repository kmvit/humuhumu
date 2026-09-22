"""Оплата заказа: наличными на кассе, через терминал или картой онлайн.

Три способа стартуют по-разному, но сходятся в одной точке —
apply_payment_result. Она и переводит статусы, чтобы дев-эмуляция,
уведомление банка и ручное подтверждение кассиром не разъезжались
в трёх разных реализациях.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from orders.models import Order

from .acquiring import AcquiringError, get_acquirer
from .models import Payment
from .providers import get_provider


logger = logging.getLogger(__name__)


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

    # Выключатель владельца проверяем и здесь, а не только прячем кнопку:
    # ручка публичная, и старая вкладка гостя дошла бы до банка.
    from core.models import SiteSettings

    if not SiteSettings.load().online_payment_on:
        raise PaymentError("Оплата картой сейчас недоступна")

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


# Как часто позволено спрашивать банк об одном и том же платеже. Гость
# опрашивает свой заказ каждые пять секунд, и без этой паузы каждый его
# опрос превращался бы в запрос к банку.
SETTLE_EVERY = timedelta(seconds=15)

# Насколько старые платежи ещё имеет смысл доводить. Гость, не заплативший
# за сутки, не заплатит уже никогда, а вот повторно закрыть по ошибке заказ,
# который официант давно провёл наличными, — вполне реальная беда.
SETTLE_WINDOW = timedelta(days=1)


def settle_payment(payment: Payment) -> bool:
    """Спросить банк о незавершённом платеже и применить ответ.

    Зачем это нужно, если есть уведомление. Уведомление может не прийти
    вовсе: у ЮKassa адрес уведомлений прописывается в кабинете банка
    руками, и пока он не прописан, оплаченный заказ висит открытым —
    деньги у заведения, а гость смотрит на кнопку «оплатить». Опрос
    закрывает и это, и потерянное по дороге уведомление.

    Возвращает True, если статус платежа изменился.
    """
    if payment.status != Payment.Status.PENDING or not payment.external_id:
        return False

    acquirer = get_acquirer(payment.provider)
    try:
        result = acquirer.status(payment.external_id)
    except AcquiringError as e:
        # Банк недоступен — это не повод ронять экран гостя. Следующий
        # опрос (его же, или фоновой задачи) попробует снова.
        logger.warning("Платёж %s: банк не ответил (%s)", payment.pk, e)
        return False

    if result is None or result.pending:
        # Отметку времени двигаем в любом случае: иначе платёж, брошенный
        # гостем на странице банка, опрашивался бы без остановки.
        payment.save(update_fields=["updated_at"])
        return False

    with transaction.atomic():
        apply_payment_result(
            payment, success=result.success, fiscal_receipt=result.fiscal_receipt
        )
    logger.info(
        "Платёж %s доведён опросом банка: %s",
        payment.pk, "успех" if result.success else "отказ",
    )
    return True


def pending_online_payments(order: Order | None = None):
    """Незавершённые онлайн-платежи, которые пора переспросить у банка."""
    since = timezone.now() - SETTLE_WINDOW
    stale = timezone.now() - SETTLE_EVERY
    qs = Payment.objects.filter(
        status=Payment.Status.PENDING,
        purpose=Payment.Purpose.ORDER,
        created_at__gte=since,
        updated_at__lte=stale,
        external_id__isnull=False,
    ).exclude(external_id="")
    if order is not None:
        qs = qs.filter(order=order)
    return qs


def settle_order(order: Order) -> None:
    """Довести оплату заказа, пока гость смотрит на его статус.

    Вызывается из публичной ручки track, поэтому не падает никогда:
    заказ гостю нужно показать, даже если банк сейчас недоступен.
    """
    if order.status not in (Order.Status.OPEN, Order.Status.REQUESTED, Order.Status.AWAITING):
        return
    for payment in pending_online_payments(order):
        try:
            settle_payment(payment)
        except Exception:  # ни одна ошибка банка не стоит экрана гостя
            logger.exception("Не удалось довести платёж %s", payment.pk)

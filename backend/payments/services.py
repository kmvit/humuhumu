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
from orders.services import start_order

from .acquiring import AcquiringError, get_acquirer
from .models import Payment
from .providers import KassaError, get_provider, is_kassa, kassa_only


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
    # Предоплаченный заказ закрывается без второго платежа: деньги уже
    # в реестре. Иначе выручка дня удвоилась бы на каждом таком заказе.
    if order.paid_at is None:
        if kassa_only():
            # «Выдал, взял картой» без кассы — это деньги без чека. На
            # стойке с кассой неоплаченный заказ отправляют на кассу.
            raise PaymentError("Оплата принимается только через кассу — отправьте заказ на кассу")
        drop_kassa_orders(order)
        Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.SUCCEEDED,
            # деньгами берём чек за вычетом списанных бонусов
            amount=order.payable,
            order=order,
            method=pm,
            provider="manual",
        )
        order.paid_at = timezone.now()
    else:
        # Способ оплаты у такого заказа уже записан — тот, которым
        # заплатили вперёд, а не тот, что нажали при выдаче.
        pm = Payment.Method.CASH if order.pay_method == Order.PayMethod.CASH else Payment.Method.CARD
    order.status = Order.Status.PAID
    order.pay_method = (
        Order.PayMethod.CASH if pm == Payment.Method.CASH else Order.PayMethod.CARD
    )
    order.closed_by = user
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "paid_at", "pay_method", "closed_by", "closed_at"])
    accrue_bonuses(order)
    return order


@transaction.atomic
def refund_order(order: Order, *, user=None, return_to_stock: bool = False) -> Order:
    """Вернуть гостю деньги за заказ целиком.

    Возврат — не отмена. Отменённый заказ это тот, которого не было;
    возвращённый был продан, приготовлен и попал в выручку дня, а потом
    деньги ушли обратно. Поэтому исходный платёж остаётся в реестре, а
    рядом появляется запись возврата своим днём: иначе не сойдётся ни
    касса, ни выручка смены, по которой считают людям зарплату.

    Карту возвращает банк, наличные — кассир из ящика: нам остаётся
    записать, что деньги отданы, иначе в кассе будет недостача.
    """
    if order.paid_at is None:
        raise PaymentError("Заказ не оплачен — возвращать нечего")
    if order.status == Order.Status.REFUNDED:
        raise PaymentError("Деньги по этому заказу уже вернули")
    if kassa_refunds(order).filter(status=Payment.Status.PENDING).exists():
        raise PaymentError("Возврат уже идёт на кассе — дождитесь, пока касса закончит")

    paid = list(
        Payment.objects.filter(
            order=order, purpose=Payment.Purpose.ORDER, status=Payment.Status.SUCCEEDED
        )
    )
    if not paid:
        raise PaymentError("По заказу нет успешного платежа")

    # Оплату на кассе, о которой касса сказала нам чек и слип, возвращает
    # сама касса по нашей команде. Это несколько операций, и идут они не
    # мгновенно (касса может попросить карту), поэтому здесь возврат
    # только начинается, а доводит его опрос — settle_kassa_refund.
    via_kassa = [p for p in paid if _refund_via_kassa(p)]
    if via_kassa:
        _start_kassa_refund(order, via_kassa, user=user, return_to_stock=return_to_stock)
        return order

    for payment in paid:
        external_id = ""
        # Оплату на кассе возвращает сама касса — чеком возврата, который
        # бариста пробивает на ней же. Нам остаётся записать возврат.
        if payment.provider != "manual" and not is_kassa(payment.provider):
            # Онлайн-оплату возвращает банк. Ошибку наружу не глушим:
            # сказать сотруднику «готово», когда банк отказал, значит
            # отпустить гостя без денег.
            acquirer = get_acquirer(payment.provider)
            try:
                external_id = acquirer.refund(payment)
            except AcquiringError as e:
                raise PaymentError(str(e)) from e
            # Деньги банк уже отправил — запись возврата обязана лечь.
            # Номер исходного платежа занят в реестре (номер у банка
            # уникален), и с ним вставка упала бы, откатив учёт возврата:
            # гость с деньгами, а у нас заказ оплачен. Так было с Т-Банком.
            if external_id == payment.external_id:
                external_id = ""

        Payment.objects.create(
            purpose=Payment.Purpose.REFUND,
            status=Payment.Status.SUCCEEDED,
            amount=payment.amount,
            order=order,
            method=payment.method,
            provider=payment.provider,
            external_id=external_id or None,
        )
        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=["status", "updated_at"])

    _close_refunded(order, user=user, return_to_stock=return_to_stock)
    return order


def _close_refunded(order: Order, *, user=None, return_to_stock: bool = False) -> None:
    """Заказ — в «возврат»: бонусы назад, продукты на склад по галочке.

    Ровно один раз и только когда деньги действительно ушли гостю: при
    возврате через кассу — когда она его подтвердила, а не когда приняла
    команду. Иначе при отказе кассы бонусы вернулись бы, а деньги нет.
    """
    order.status = Order.Status.REFUNDED
    order.refunded_at = timezone.now()
    order.closed_by = user or order.closed_by
    order.save(update_fields=["status", "refunded_at", "closed_by"])

    _undo_bonuses(order)
    if return_to_stock:
        _return_to_stock(order, user)

    logger.info(
        "Возврат: заказ %s, %s ₽, вернул %s%s",
        order.pk, order.payable, user, ", продукты на склад" if return_to_stock else "",
    )


# ── Возврат через кассу ─────────────────────────────────────────────────
# Шаги: «slip» — касса возвращает деньги на карту по слипу оплаты;
# «receipt» — касса пробивает чек возврата. Наличные — сразу «receipt»:
# деньги бариста отдаёт из ящика, касса только печатает чек. Каждый шаг —
# операция на кассе; её номер лежит в external_id записи возврата, ход —
# в kassa_meta.

def kassa_refunds(order: Order | None = None):
    """Записи возврата через кассу (любого статуса)."""
    from .providers import _PROVIDERS

    qs = Payment.objects.filter(purpose=Payment.Purpose.REFUND, provider__in=list(_PROVIDERS))
    return qs.filter(order=order) if order is not None else qs


def _refund_via_kassa(payment: Payment) -> bool:
    if not is_kassa(payment.provider) or not payment.kassa_receipt_id:
        return False
    return get_provider(payment.provider).can_refund


def _start_kassa_refund(order: Order, originals: list, *, user=None, return_to_stock=False) -> None:
    for original in originals:
        provider = get_provider(original.provider)
        # Прошлая попытка, где деньги на карту уже вернули, а чек не
        # пробился: продолжаем с чека, а не возвращаем деньги второй раз.
        resume = (
            kassa_refunds(order)
            .filter(status=Payment.Status.FAILED, kassa_meta__original=original.pk)
            .order_by("-created_at")
            .first()
        )
        slip_back = (resume.kassa_meta or {}).get("slip") if resume else None
        try:
            receipt = provider.receipt(original.kassa_receipt_id)
            if slip_back:
                stage, op = "receipt", provider.start_return_receipt(receipt, slip_back)
            elif original.kassa_slip_id:
                stage, op = "slip", provider.start_slip_refund(receipt, original.kassa_slip_id)
            elif any(p.get("type") == 1 for p in receipt.get("payments") or []):
                raise KassaError("в чеке есть оплата картой, а её слипа у нас нет")
            else:
                stage, op = "receipt", provider.start_return_receipt(receipt, None)
        except KassaError as e:
            raise PaymentError(f"Касса не начала возврат: {e}") from e
        Payment.objects.create(
            purpose=Payment.Purpose.REFUND,
            status=Payment.Status.PENDING,
            amount=original.amount,
            order=order,
            method=original.method,
            provider=original.provider,
            external_id=op,
            kassa_meta={
                "stage": stage,
                "original": original.pk,
                "receipt": receipt,
                "slip": slip_back,
                "return_to_stock": bool(return_to_stock),
                "user": getattr(user, "pk", None),
            },
        )
        logger.info(
            "Возврат через кассу начат: заказ %s, платёж %s, шаг %s, операция %s",
            order.pk, original.pk, stage, op,
        )


def _fiscal_of(receipt: dict | None) -> str:
    doc = ((receipt or {}).get("info") or {}).get("docInfo") or {}
    number, fp = doc.get("docNumber"), doc.get("docFiscalAttributeInt") or doc.get("docFiscalAttribute")
    return f"ФД {number or '—'}, ФП {fp or '—'}"[:64] if (number or fp) else ""


def settle_kassa_refund(refund: Payment) -> bool:
    """Спросить кассу, как идёт возврат, и довести его на шаг.

    Возвращает True, если что-то изменилось. Под блокировкой строки: опрос
    приходит и с доски, и из фоновой задачи, и без неё один шаг мог бы
    запуститься дважды — второй чек возврата или второй возврат на карту.
    """
    if refund.status != Payment.Status.PENDING or not refund.external_id:
        return False
    provider = get_provider(refund.provider)
    try:
        op = provider.operation(refund.external_id)
    except KassaError as e:
        logger.warning("Возврат %s: касса не ответила (%s)", refund.pk, e)
        return False
    if op.running:
        refund.save(update_fields=["updated_at"])
        return False

    from users.models import User

    with transaction.atomic():
        fresh = Payment.objects.select_for_update().filter(pk=refund.pk).first()
        if (
            fresh is None
            or fresh.status != Payment.Status.PENDING
            or fresh.external_id != refund.external_id
        ):
            return False
        meta = dict(fresh.kassa_meta or {})

        if not op.done:
            # Касса отказала. С шага «чек» это значит: деньги на карту уже
            # ушли — слип в meta остаётся, и повтор начнёт с чека.
            meta["error"] = (
                f"Деньги на карту вернули, а чек возврата не пробился: {op.message}"
                if meta.get("stage") == "receipt" and meta.get("slip")
                else f"Касса не вернула деньги: {op.message}"
            )
            fresh.kassa_meta = meta
            fresh.status = Payment.Status.FAILED
            fresh.save(update_fields=["kassa_meta", "status", "updated_at"])
            logger.warning("Возврат %s (заказ %s) не прошёл: %s", fresh.pk, fresh.order_id, meta["error"])
            return True

        if meta.get("stage") == "slip":
            meta["slip"] = op.result
            meta["stage"] = "receipt"
            try:
                next_op = provider.start_return_receipt(meta["receipt"], op.result)
            except KassaError as e:
                meta["error"] = f"Деньги на карту вернули, а чек возврата не пробился: {e}"
                fresh.kassa_meta = meta
                fresh.status = Payment.Status.FAILED
                fresh.save(update_fields=["kassa_meta", "status", "updated_at"])
                return True
            fresh.external_id = next_op
            fresh.kassa_meta = meta
            fresh.save(update_fields=["external_id", "kassa_meta", "updated_at"])
            return True

        # Чек возврата пробит — деньги у гостя, документ есть.
        meta.pop("error", None)
        meta["return_receipt_id"] = str((op.result or {}).get("id") or "")
        fresh.kassa_meta = meta
        fresh.fiscal_receipt = _fiscal_of(op.result)
        fresh.status = Payment.Status.SUCCEEDED
        fresh.save(update_fields=["kassa_meta", "fiscal_receipt", "status", "updated_at"])

        original = Payment.objects.select_for_update().filter(pk=meta.get("original")).first()
        if original is not None and original.status == Payment.Status.SUCCEEDED:
            original.status = Payment.Status.REFUNDED
            original.save(update_fields=["status", "updated_at"])

        order = Order.objects.select_for_update().get(pk=fresh.order_id)
        still = kassa_refunds(order).filter(status=Payment.Status.PENDING).exists()
        if order.status != Order.Status.REFUNDED and not still:
            user = User.objects.filter(pk=meta.get("user")).first() if meta.get("user") else None
            _close_refunded(order, user=user, return_to_stock=bool(meta.get("return_to_stock")))
    return True


def pending_kassa_refunds(*, every: timedelta | None = None):
    """Возвраты, которые касса ещё делает и которые пора переспросить."""
    from .providers import MockProvider, _PROVIDERS

    qs = Payment.objects.filter(
        purpose=Payment.Purpose.REFUND,
        status=Payment.Status.PENDING,
        provider__in=[n for n in _PROVIDERS if n != MockProvider.name],
        created_at__gte=timezone.now() - SETTLE_WINDOW,
    ).exclude(external_id=None)
    if every is not None:
        qs = qs.filter(updated_at__lte=timezone.now() - every)
    return qs


def _undo_bonuses(order: Order) -> None:
    """Откатить бонусы возвращённого заказа: списанные вернуть, начисленные снять.

    Иначе гость получает бонусы за покупку, которой в итоге не было, —
    и может списать их ещё раз.
    """
    from loyalty.services import cancel_earned_for_order, return_for_order

    return_for_order(order, refund=True)
    cancel_earned_for_order(order)


def _return_to_stock(order: Order, user=None) -> None:
    """Вернуть на склад то, что списали за позиции заказа.

    Решает сотрудник при возврате: не успели приготовить — продукты
    целы; вылили готовый кофе — возвращать нечего, и склад бы соврал.
    """
    from inventory.services import return_order_item

    for item in order.items.all():
        return_order_item(item, user=user)


@transaction.atomic
def record_prepayment(order: Order, method: str, user=None) -> Order:
    """Гость заплатил на кассе за заказ, который ждал оплаты.

    Не то же самое, что закрытие: деньги получены, но заказ только уходит
    в работу — его ещё готовить и выдавать. Нужна эта ручка ради гостя с
    наличными: без неё на стойке с предоплатой он не смог бы заказать
    вовсе, а бариста — принять у него деньги.
    """
    if order.status != Order.Status.UNPAID:
        raise PaymentError("Этот заказ не ждёт оплаты")
    # Заведение с подключённой кассой принимает деньги только через неё:
    # касса не работает — ждут, пока заработает. Ручная отметка здесь
    # означала бы деньги без чека и заказ, который касса не видела.
    if kassa_only():
        raise PaymentError("Оплата принимается только через кассу — отправьте заказ на кассу")

    pm = method if method in Payment.Method.values else Payment.Method.CASH
    Payment.objects.create(
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.SUCCEEDED,
        amount=order.payable,
        order=order,
        method=pm,
        provider="manual",
    )
    order.paid_at = timezone.now()
    order.pay_method = (
        Order.PayMethod.CASH if pm == Payment.Method.CASH else Order.PayMethod.CARD
    )
    order.save(update_fields=["paid_at", "pay_method"])
    start_order(order)
    accrue_bonuses(order)
    logger.info(
        "Оплата на кассе: заказ %s, %s ₽, %s — пущен в работу",
        order.pk, order.payable, pm,
    )
    return order


def start_terminal_payment(order: Order, method: str = Payment.Method.CARD) -> Payment:
    """Отправить заказ на кассу: создать платёж и положить заказ на кассу.

    Два случая. Официант в зале отправляет открытый счёт — заказ уходит
    в «к оплате», стол занят, пока не придёт результат. Гость на стойке
    с предоплатой выбирает «Оплатить на кассе» — заказ остаётся «ждёт
    оплаты»: готовить его рано, а на доски станций «к оплате» попадает.

    Повторная отправка не плодит заказов на кассе: если заказ уже там и
    ждёт, возвращаем тот же платёж (гость нажал кнопку дважды, бариста
    переспросил).
    """
    if order.status not in (Order.Status.OPEN, Order.Status.UNPAID):
        raise PaymentError("Отправить на кассу можно только неоплаченный заказ")
    if order.paid_at is not None:
        raise PaymentError("Заказ уже оплачен")
    if method not in Payment.Method.values:
        method = Payment.Method.CARD

    provider = get_provider()
    waiting = pending_kassa_payments(order).filter(provider=provider.name).first()
    if waiting is not None and waiting.amount == order.payable:
        return waiting

    # Сумма изменилась (списали бонусы, дописали позицию) — старый заказ
    # с кассы снимаем, иначе кассир возьмёт по нему прежнюю сумму.
    drop_kassa_orders(order)
    payment = Payment.objects.create(
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.PENDING,
        amount=order.payable,
        order=order,
        method=method,
        provider=provider.name,
    )
    try:
        provider.start(payment)
    except KassaError as e:
        # Пустышку в реестре не оставляем: она висела бы «создан» и
        # опрашивалась бы у кассы, которая о ней не знает.
        payment.delete()
        logger.warning(
            "Заказ %s не ушёл на кассу %s: %s", order.pk, provider.name, e
        )
        # Владельцу — в панель «Касса»: бариста ошибку видит, а гость,
        # нажавший «Оплатить на кассе», — только общую фразу.
        note_payment_error(provider.name, str(e), order)
        raise ProviderRefused(provider.name, str(e)) from e
    note_payment_ok(provider.name)
    if order.status == Order.Status.OPEN:
        order.status = Order.Status.AWAITING
        order.save(update_fields=["status"])
    logger.info(
        "Заказ %s отправлен на кассу %s: платёж %s, %s ₽",
        order.pk, provider.name, payment.pk, payment.amount,
    )
    return payment


def pending_kassa_payments(order: Order):
    """Платежи заказа, которые сейчас лежат на кассе и ждут оплаты."""
    from .providers import _PROVIDERS

    return Payment.objects.filter(
        order=order,
        purpose=Payment.Purpose.ORDER,
        status=Payment.Status.PENDING,
        provider__in=list(_PROVIDERS),
    )


def drop_kassa_orders(order: Order, *, keep: Payment | None = None) -> None:
    """Снять с кассы заказы, которые больше не нужно оплачивать.

    Заказ оплатили иначе (онлайн, наличными мимо кассы) или отменили, а на
    кассе он так и лежит в «отложенных» — и кассир может взять за него
    деньги ещё раз. Касса недоступна — это не повод не закрыть заказ у
    нас: платёж всё равно гасим, а в лог пишем, что снять не удалось.
    """
    qs = pending_kassa_payments(order)
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    for payment in qs:
        try:
            get_provider(payment.provider).cancel(payment)
        except KassaError as e:
            logger.warning(
                "Заказ %s: не удалось снять с кассы платёж %s (%s) — снимите вручную",
                order.pk, payment.pk, e,
            )
        payment.status = Payment.Status.CANCELLED
        payment.save(update_fields=["status", "updated_at"])


def withdraw_from_kassa(order: Order) -> None:
    """Снять заказ с кассы, чтобы поменять сумму (бонусы вспомнили у окна).

    В отличие от drop_kassa_orders, здесь сбой — это отказ: если заказ
    остался на кассе, гость оплатит его по старой цене, а мы уже спишем
    бонусы. Поэтому сперва спрашиваем кассу — вдруг уже оплатили, — и
    гасим платёж у себя только когда касса подтвердила удаление.
    Оплаченный заказ aQsi удалить не даёт — это тоже отказ.
    """
    for payment in list(pending_kassa_payments(order)):
        settle_kassa_payment(payment)
        payment.refresh_from_db()
        if payment.status != Payment.Status.PENDING:
            order.refresh_from_db()
            if order.paid_at:
                raise PaymentError("Заказ уже оплачен на кассе — бонусы к нему не применить")
            continue
        try:
            get_provider(payment.provider).cancel(payment)
        except KassaError as e:
            raise PaymentError(
                f"Не удалось снять заказ с кассы ({e}) — сумму не меняем, попробуйте ещё раз"
            ) from e
        payment.status = Payment.Status.CANCELLED
        payment.save(update_fields=["status", "updated_at"])


def settle_kassa_payment(payment: Payment) -> bool:
    """Спросить кассу, оплачен ли лежащий на ней заказ, и применить ответ.

    Возвращает True, если статус платежа изменился.
    """
    if payment.status != Payment.Status.PENDING or not payment.external_id:
        return False
    try:
        result = get_provider(payment.provider).status(payment)
    except KassaError as e:
        logger.warning("Платёж %s: касса не ответила (%s)", payment.pk, e)
        return False

    if result is None or result.pending:
        payment.save(update_fields=["updated_at"])
        return False

    if result.success:
        # Чем заплатили, знает только касса: гость мог передумать у окна
        # и отдать наличные вместо карты. Чек и слип — для возврата через
        # кассу: потом их взять неоткуда. Пишем только пока платёж ждёт,
        # чтобы не перетереть то, что уже записал соседний опрос.
        fields = {
            "kassa_receipt_id": result.receipt_id,
            "kassa_slip_id": result.slip_id,
            "kassa_rrn": result.rrn,
        }
        if result.method in Payment.Method.values:
            fields["method"] = result.method
        Payment.objects.filter(pk=payment.pk, status=Payment.Status.PENDING).update(**fields)
    if not apply_bank_result(payment, result):
        return False
    logger.info(
        "Платёж %s доведён кассой: %s (заказ %s)",
        payment.pk, "оплачен" if result.success else "снят с кассы", payment.order_id,
    )
    return True


def settle_kassa(order: Order) -> None:
    """Спросить кассу обо всех ждущих платежах заказа. Не падает никогда."""
    for payment in pending_kassa_payments(order):
        try:
            settle_kassa_payment(payment)
        except Exception:  # ни одна ошибка кассы не стоит экрана сотрудника
            logger.exception("Не удалось довести платёж %s", payment.pk)


class ProviderRefused(PaymentError):
    """Банк или касса не дали перейти к оплате.

    Текст — причина провайдера: сотруднику он нужен, гостю — нет. Гостевые
    ручки ловят это исключение отдельно и отвечают своей фразой.
    """

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(reason)
        self.provider = provider


def start_online_payment(order: Order, *, return_url: str) -> tuple[Payment, str]:
    """Оплата картой онлайн для гостя — с ошибками, понятными гостю.

    Отказ банка запоминается для владельца уже ПОСЛЕ отката транзакции:
    внутри неё запись откатилась бы вместе с платежом.
    """
    try:
        return _start_online_payment(order, return_url=return_url)
    except ProviderRefused as e:
        logger.warning(
            "Онлайн-оплата не началась: заказ %s, %s ₽, банк %s — %s",
            order.pk, order.payable, e.provider, e,
        )
        note_payment_error(e.provider, str(e), order)
        # Причину банка гостю не показываем: «Неверные параметры» или
        # ошибка TLS ему ничего не скажут, а выдают внутренности. Она в
        # логе и в панели владельца, гостю — что делать дальше.
        raise PaymentError(GUEST_PAYMENT_FAILED) from e


@transaction.atomic
def _start_online_payment(order: Order, *, return_url: str) -> tuple[Payment, str]:
    """Создать платёж у банка и вернуть ссылку для гостя.

    Заказ в «к оплате» здесь НЕ переводим, в отличие от терминала: гость
    может закрыть страницу банка и вернуться платить наличными, а заказ,
    зависший в «к оплате», официант закрыть не сможет. Статус меняется
    только по факту оплаты — в apply_payment_result.
    """
    if order.status not in (Order.Status.OPEN, Order.Status.REQUESTED, Order.Status.UNPAID):
        raise PaymentError("Оплатить можно только незакрытый заказ")

    # Выключатель владельца проверяем и здесь, а не только прячем кнопку:
    # ручка публичная, и старая вкладка гостя дошла бы до банка.
    from core.models import SiteSettings

    if not SiteSettings.load().online_payment_on:
        raise PaymentError("Оплата картой сейчас недоступна")

    acquirer = get_acquirer()
    if not acquirer.configured():
        raise ProviderRefused(acquirer.name, "Не заданы доступы к банку")

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
        logger.info(
            "Онлайн-оплата начата: платёж %s, заказ %s, %s ₽, банк %s",
            payment.pk, order.pk, payment.amount, acquirer.name,
        )
    except AcquiringError as e:
        # Платёж-пустышку не оставляем: он бы висел в реестре как
        # «создан» и портил сверку с банком.
        payment.delete()
        raise ProviderRefused(acquirer.name, str(e)) from e
    note_payment_ok(acquirer.name)
    return payment, url


#: Что видит гость, если банк не дал перейти к оплате.
GUEST_PAYMENT_FAILED = (
    "Не получилось перейти к оплате. Попробуйте ещё раз или оплатите на месте."
)

#: Что видит гость, если заказ не ушёл на кассу. Заказ при этом записан и
#: ждёт в «Ждут оплаты» — бариста отправит его сам.
GUEST_KASSA_FAILED = (
    "Не получилось отправить заказ на кассу. Подойдите к баристе и назовите номер заказа."
)


def note_payment_error(provider: str, error: str, order: Order | None = None) -> None:
    """Запомнить отказ банка или кассы для панели владельца (см. PaymentHealth).

    Сбой записи не должен заслонять сам отказ: гость всё равно получит
    свою фразу, а причина останется в логе.
    """
    from .models import PaymentHealth

    try:
        PaymentHealth.objects.update_or_create(
            provider=provider,
            defaults={
                "error": error[:300],
                "error_at": timezone.now(),
                "error_order": order.pk if order else None,
            },
        )
    except Exception:
        logger.exception("Не удалось записать ошибку онлайн-оплаты")


def note_payment_ok(provider: str) -> None:
    """Банк или касса приняли платёж — прежняя ошибка, если была, позади."""
    from .models import PaymentHealth

    try:
        PaymentHealth.objects.update_or_create(
            provider=provider, defaults={"ok_at": timezone.now()}
        )
    except Exception:
        logger.exception("Не удалось записать удачную онлайн-оплату")


def payment_health(provider: str) -> dict | None:
    """Последний отказ банка или кассы, если после него оплата не проходила.

    Ошибку, за которой уже были удачные переходы к оплате, не показываем:
    это был разовый сбой, и владельцу чинить нечего.
    """
    from .models import PaymentHealth

    row = PaymentHealth.objects.filter(provider=provider).first()
    if row is None or not row.error_at:
        return None
    if row.ok_at and row.ok_at > row.error_at:
        return None
    return {"error": row.error, "at": row.error_at, "order": row.error_order}


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
            order.paid_at = timezone.now()
            order.pay_method = (
                Order.PayMethod.CASH
                if payment.method == Payment.Method.CASH
                else Order.PayMethod.CARD
            )
            order.fiscal_receipt = fiscal_receipt or order.fiscal_receipt
            if order.status == Order.Status.UNPAID:
                # Предоплата на стойке: деньги получены, но заказ только
                # начинается. Закрыть его сейчас значило бы записать
                # выручку за то, что ещё не приготовлено и не выдано, —
                # и убрать карточку с доски, по которой бариста работает.
                order.save(update_fields=["paid_at", "pay_method", "fiscal_receipt"])
                start_order(order)
            else:
                order.status = Order.Status.PAID
                order.closed_by = user
                order.closed_at = timezone.now()
                order.save(update_fields=[
                    "status", "paid_at", "pay_method", "fiscal_receipt",
                    "closed_by", "closed_at",
                ])
            accrue_bonuses(order)
            # Заплатили одним путём — заказ, отправленный на кассу другим,
            # снимаем, чтобы кассир не взял деньги второй раз. После
            # коммита: снятие ходит в сеть, строку платежа держать незачем.
            transaction.on_commit(lambda: drop_kassa_orders(order, keep=payment))
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

# Кассу спрашиваем чаще банка: гость стоит у окна, бариста ждёт, когда
# заказ появится в «Новых», и полминуты там — это очередь. Запрос к кассе
# дешёвый, а ждущих заказов на ней — единицы.
KASSA_EVERY = timedelta(seconds=3)

# Насколько старые платежи ещё имеет смысл доводить. Гость, не заплативший
# за сутки, не заплатит уже никогда, а вот повторно закрыть по ошибке заказ,
# который официант давно провёл наличными, — вполне реальная беда.
SETTLE_WINDOW = timedelta(days=1)


def apply_bank_result(payment: Payment, result) -> bool:
    """Применить ответ банка ровно один раз, кто бы ни пришёл первым.

    Путей теперь два — уведомление банка и наш опрос, — и они вполне
    могут сойтись на одном платеже в одну секунду. Без блокировки строки
    обе транзакции увидели бы «ещё не оплачен» и закрыли заказ дважды:
    вторая переписала бы время закрытия, а гостю дважды начислились бы
    бонусы. Проверка статуса делается уже под блокировкой — поэтому
    второй приходящий просто ничего не делает.

    Возвращает True, если статус платежа изменился именно этим вызовом.
    """
    with transaction.atomic():
        fresh = Payment.objects.select_for_update().filter(pk=payment.pk).first()
        if fresh is None or fresh.status != Payment.Status.PENDING:
            return False
        apply_payment_result(
            fresh, success=result.success, fiscal_receipt=result.fiscal_receipt
        )
    return True


def confirm_kassa_paid(order: Order, method: str | None, user) -> str:
    """Бариста отмечает «Оплачено» за заказ, который лежит на кассе.

    Подстраховка на случай, когда касса оплату не подтвердила: не
    ответила или назвала оплаченный заказ не так, как мы ждём. Без неё
    стойка встала бы — заказ не идёт в работу, пока касса не скажет.

    Сначала спрашиваем кассу сами: подтвердит — дальше обычным путём, и
    ручная отметка не нужна. Нет — подтверждаем вручную тот самый платёж,
    что лежит на кассе, под блокировкой, как apply_bank_result: фоновый
    опрос мог увидеть оплату в ту же секунду, и заказ закрылся бы дважды.
    С кассы заказ не снимаем — там он, скорее всего, уже оплачен.

    Возвращает "kassa" (подтвердила касса), "manual" (подтвердили руками)
    или "need_method" (касса молчит — нужен способ оплаты от баристы).
    """
    payment = pending_kassa_payments(order).order_by("-created_at").first()
    if payment is None:
        raise PaymentError("Заказ не на кассе — сначала отправьте его на кассу")
    settle_kassa_payment(payment)
    order.refresh_from_db()
    if order.paid_at:
        return "kassa"
    if method is None:
        return "need_method"
    if method not in (Payment.Method.CASH, Payment.Method.CARD):
        raise PaymentError("Укажите, чем заплатили: наличными или картой")
    with transaction.atomic():
        fresh = Payment.objects.select_for_update().filter(pk=payment.pk).first()
        if fresh is None or fresh.status != Payment.Status.PENDING:
            order.refresh_from_db()
            if order.paid_at:
                return "kassa"
            raise PaymentError(
                "Касса не знает этот заказ — отправьте его на кассу заново"
            )
        fresh.method = method
        fresh.confirmed_by = user
        fresh.save(update_fields=["method", "confirmed_by", "updated_at"])
        apply_payment_result(fresh, success=True, user=user)
    logger.warning(
        "Заказ %s: оплату на кассе подтвердил вручную %s (платёж %s, %s ₽)",
        order.pk, user, payment.pk, payment.amount,
    )
    return "manual"


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
    if is_kassa(payment.provider):
        return settle_kassa_payment(payment)

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

    if not apply_bank_result(payment, result):
        return False
    logger.info(
        "Платёж %s доведён опросом банка: %s (заказ %s)",
        payment.pk, "успех" if result.success else "отказ", payment.order_id,
    )
    return True


def pending_online_payments(order: Order | None = None, *, every: timedelta = SETTLE_EVERY):
    """Незавершённые платежи, которые пора переспросить у банка или кассы."""
    since = timezone.now() - SETTLE_WINDOW
    stale = timezone.now() - every
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


def settle_waiting_kassa() -> None:
    """Спросить кассу о заказах, которые на ней ждут оплаты.

    Зовётся с доски баристы, которую планшет перечитывает каждые несколько
    секунд, поэтому каждый платёж спрашиваем не чаще SETTLE_EVERY. Эмуляцию
    не трогаем: ей результат подаёт сотрудник.
    """
    from .providers import MockProvider, _PROVIDERS

    kassas = [name for name in _PROVIDERS if name != MockProvider.name]
    for payment in pending_online_payments(every=KASSA_EVERY).filter(provider__in=kassas):
        try:
            settle_kassa_payment(payment)
        except Exception:  # касса не должна ронять доску
            logger.exception("Не удалось довести платёж %s", payment.pk)
    for refund in pending_kassa_refunds(every=KASSA_EVERY):
        try:
            settle_kassa_refund(refund)
        except Exception:
            logger.exception("Не удалось довести возврат %s", refund.pk)


def settle_order(order: Order) -> None:
    """Довести оплату заказа, пока гость смотрит на его статус.

    Вызывается из публичной ручки track, поэтому не падает никогда:
    заказ гостю нужно показать, даже если банк сейчас недоступен.
    """
    if order.status not in (
        Order.Status.OPEN,
        Order.Status.REQUESTED,
        Order.Status.AWAITING,
        # Заказ стойки, ждущий предоплаты: для него опрос как раз главный —
        # именно оплата пускает его в работу.
        Order.Status.UNPAID,
    ):
        return
    for payment in pending_online_payments(order):
        try:
            settle_payment(payment)
        except Exception:  # ни одна ошибка банка не стоит экрана гостя
            logger.exception("Не удалось довести платёж %s", payment.pk)

"""Синхронизация терминала кассы с её облаком — через личный кабинет.

Беда, ради которой это написано (Монти, 01.10.2026). Терминал aQsi теряет
связь с облаком: отложенный заказ мы положили, в кабинете aQsi он лежит
«Отложен», а на терминал не приходит и по номеру не ищется. Гость стоит у
окна. Лечится кнопкой «Синхронизировать» у кассы в кабинете — после неё
висевшие заказы прилетают сразу. В открытом API такой команды нет, и
статуса синхронизации тоже, поэтому жмём ту же кнопку, что и человек в
кабинете, — своим входом владельца (логин и пароль — в доступах кассы,
сессия — в KassaSync).

Только по кнопке баристы: заказа нет на кассе — нажал. Сами по себе не
синхронизируем (решение владельца 02.10.2026): синхронизация посреди чужой
оплаты на кассе — неизвестно чем кончится.

Главная грабля. После синхронизации на терминал приходит всё, что в
кабинете лежит «Отложен», — в том числе заказы, которые у нас уже закрыты:
бариста подтвердил «Оплачено» руками, гость заплатил онлайн, заказ
отменили, а снять его с кассы тогда не вышло. Кассир возьмёт за них деньги
второй раз. Поэтому перед синхронизацией такие заказы снимаем с кассы, и
если хоть один снять не удалось — не синхронизируем вовсе.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from .models import KassaSync, Payment
from .providers import BaseProvider, KassaError, KassaSessionExpired, get_provider
from .secrets import decrypt, encrypt

logger = logging.getLogger(__name__)

#: Пауза между нажатиями: нажал дважды — второй раз не нужен, а дёргать
#: кабинет каждые секунды — верный путь к блокировке входа.
RESYNC_EVERY = timedelta(seconds=20)
#: Насколько старые закрытые заказы проверяем перед синхронизацией. Сутки:
#: дольше отложенный заказ на кассе у нас не живёт (SETTLE_WINDOW).
CLEAR_WINDOW = timedelta(days=1)

#: Отметка в kassa_meta: заказа на кассе точно нет, проверять больше незачем.
OFF_KASSA = "off_kassa"


def clear_closed_orders(provider: BaseProvider) -> None:
    """Снять с кассы заказы, закрытые у нас, — до синхронизации.

    Кандидаты: платежи кассы, отменённые у нас (оплатили иначе, отменили,
    кассир удалил), и подтверждённые руками «Оплачено» — их мы с кассы
    сознательно не снимали. Каждый сперва спрашиваем: оплачен на кассе или
    уже удалён — снимать нечего; лежит «Отложен» — удаляем. Проверенные
    помечаем, чтобы не спрашивать о них при каждой синхронизации.

    Не снялся хоть один — KassaError: синхронизировать нельзя.
    """
    since = timezone.now() - CLEAR_WINDOW
    candidates = (
        Payment.objects.filter(
            purpose=Payment.Purpose.ORDER,
            provider=provider.name,
            created_at__gte=since,
            status__in=(Payment.Status.CANCELLED, Payment.Status.SUCCEEDED),
        )
        .exclude(external_id__isnull=True)
        .exclude(external_id="")
    )
    failed = []
    for payment in candidates:
        if (payment.kassa_meta or {}).get(OFF_KASSA):
            continue
        if payment.status == Payment.Status.SUCCEEDED and payment.confirmed_by_id is None:
            continue  # оплату подтвердила сама касса — на ней он оплачен
        try:
            result = provider.status(payment)
            if result is not None and result.pending:
                provider.cancel(payment)
                logger.warning(
                    "Платёж %s (заказ %s) закрыт у нас, а лежал на кассе — снят перед синхронизацией",
                    payment.pk, payment.order_id,
                )
        except KassaError as e:
            failed.append(f"№{payment.order_id}: {e}")
            continue
        meta = dict(payment.kassa_meta or {})
        meta[OFF_KASSA] = True
        Payment.objects.filter(pk=payment.pk).update(kassa_meta=meta)
    if failed:
        raise KassaError(
            "Не синхронизировали: на кассе остались закрытые у нас заказы, снять их не вышло ("
            + "; ".join(failed)[:200]
            + ") — удалите их в кабинете aQsi, иначе гость заплатит дважды"
        )


def _session(row: KassaSync, provider, *, fresh: bool = False) -> str:
    """Cookie сессии кабинета: сохранённая, пока жива, иначе — новый вход."""
    if not fresh and row.session:
        alive = row.session_until is None or row.session_until > timezone.now() + timedelta(minutes=1)
        sid = decrypt(row.session).get("sid") if alive else None
        if sid:
            return sid
    sid, expires = provider.lk_login()
    row.session = encrypt({"sid": sid})
    row.session_until = datetime.fromtimestamp(expires, tz=dt_timezone.utc) if expires else None
    row.save(update_fields=["session", "session_until"])
    return sid


def _claim(provider: str, pause: timedelta) -> KassaSync | None:
    """Занять синхронизацию: никто не синхронизирует сейчас и пауза прошла.

    Бариста и владелец могут нажать одновременно — синхронизировать должен
    ровно один, второй тихо уходит.
    """
    KassaSync.objects.get_or_create(provider=provider)
    now = timezone.now()
    with transaction.atomic():
        row = KassaSync.objects.select_for_update(skip_locked=True).filter(provider=provider).first()
        if row is None:
            return None
        if row.attempted_at and row.attempted_at > now - pause:
            return None
        row.attempted_at = now
        row.save(update_fields=["attempted_at"])
    return row


def resync_kassa() -> bool:
    """Синхронизировать терминал с облаком кассы.

    Возвращает False, если синхронизация шла только что (или идёт сейчас).
    Не вышло — KassaError, итог записан для владельца.
    """
    provider = get_provider()
    if not provider.can_resync():
        raise KassaError(
            "Синхронизация кассы не настроена: владелец вводит логин, пароль и код "
            "кассы кабинета aQsi в панели, раздел «Касса»"
        )
    row = _claim(provider.name, RESYNC_EVERY)
    if row is None:
        return False
    try:
        clear_closed_orders(provider)
        try:
            provider.lk_resync(_session(row, provider))
        except KassaSessionExpired:
            provider.lk_resync(_session(row, provider, fresh=True))
    except KassaError as e:
        row.error = str(e)[:300]
        row.save(update_fields=["error"])
        logger.warning("Синхронизация кассы %s не удалась: %s", provider.name, e)
        raise
    row.error = ""
    row.synced_at = timezone.now()
    row.save(update_fields=["error", "synced_at"])
    logger.info("Касса %s синхронизирована", provider.name)
    return True


def sync_state() -> dict | None:
    """Итог последней синхронизации — для панели владельца."""
    provider = get_provider()
    if not provider.can_resync():
        return None
    row = KassaSync.objects.filter(provider=provider.name).first()
    return {
        "synced_at": row.synced_at if row else None,
        "attempted_at": row.attempted_at if row else None,
        "error": row.error if row else "",
    }

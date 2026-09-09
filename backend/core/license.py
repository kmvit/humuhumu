"""Лицензионный клиент: сверка с пультом «Падачи» и лестница блокировки.

Как устроено:
- раз в сутки celery beat зовёт sync_license() — POST на пульт с ключом;
- ответ проверяется по HMAC-подписи (секрет — сам ключ лицензии) и
  кэшируется в LicenseState; тариф из лицензии записывается в
  SiteSettings.plan — дальше работает обычный гейт по тарифу;
- effective_status() считает лестницу от текущей даты по закэшированным
  фактам, а LicenseMiddleware исполняет блокировку.

Фейл-опен — принцип, а не случайность: недоступность пульта не должна
останавливать чужую кухню. Блокировке верим только при свежей сверке
(STALE_DAYS); протухший кэш смягчает «заблокировано» до грейса.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import date, timedelta

import httpx
from django.conf import settings
from django.utils import timezone

from .models import LicenseState, SiteSettings

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
#: за сколько дней до конца оплаты показывать «заканчивается»
EXPIRING_DAYS = 5
#: старше этого кэш не даёт права блокировать (фейл-опен)
STALE_DAYS = 10

ACTIVE = "active"
EXPIRING = "expiring"
GRACE = "grace"
BLOCKED = "blocked"


def _canonical(data: dict) -> bytes:
    """Та же канонизация, что на пульте, — иначе подпись не сойдётся."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _verify(data: dict, sign: str, key: str) -> bool:
    expected = hmac.new(key.encode(), _canonical(data), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sign)


def sync_license() -> LicenseState:
    """Сходить на пульт и обновить кэш. Ошибка сети не роняет вызывающего."""
    state = LicenseState.load()
    key = settings.LICENSE_KEY
    if not key:
        return state
    try:
        res = httpx.post(
            settings.LICENSE_URL,
            json={"key": key, "version": ""},
            timeout=TIMEOUT,
        )
        res.raise_for_status()
        body = res.json()
        data, sign = body.get("data") or {}, str(body.get("sign", ""))
        if not _verify(data, sign, key):
            raise ValueError("подпись лицензии не сошлась")
        state.plan = data["plan"]
        state.paid_until = date.fromisoformat(data["paid_until"])
        state.grace_days = int(data["grace_days"])
        state.issued_at = timezone.now()
        state.checked_at = timezone.now()
        state.last_error = ""
        state.save()
        # Тариф — из лицензии. Пишем в SiteSettings, чтобы весь гейт
        # (core/plans.py, фронт) работал без единого изменения.
        site = SiteSettings.load()
        if site.plan != state.plan:
            site.plan = state.plan
            site.save(update_fields=["plan"])
    except Exception as exc:  # сеть, JSON, подпись — причина в кэше
        logger.warning("Сверка лицензии не удалась: %s", exc)
        state.last_error = str(exc)[:500]
        state.save(update_fields=["last_error"])
    return state


def effective_status(today: date | None = None) -> str:
    """Статус подписки, который исполняет middleware.

    Без ключа лицензии (автономная установка) всегда active. Пока не было
    ни одной успешной сверки — тоже active: новая точка не должна стоять
    из-за того, что beat ещё не добежал.
    """
    if not settings.LICENSE_KEY:
        return ACTIVE
    state = LicenseState.load()
    if state.paid_until is None:
        return ACTIVE
    if today is None:
        today = timezone.localdate()

    if today > state.paid_until + timedelta(days=state.grace_days):
        # Блокируем только по свежим данным: если пульт давно молчит,
        # владелец мог оплатить, а мы не узнали. Держим грейс.
        fresh = state.checked_at and (
            timezone.now() - state.checked_at <= timedelta(days=STALE_DAYS)
        )
        return BLOCKED if fresh else GRACE
    if today > state.paid_until:
        return GRACE
    if today > state.paid_until - timedelta(days=EXPIRING_DAYS):
        return EXPIRING
    return ACTIVE


def status_payload() -> dict:
    """Что видит персонал в баннере и на экране блокировки."""
    state = LicenseState.load()
    return {
        "enabled": bool(settings.LICENSE_KEY),
        "status": effective_status(),
        "plan": state.plan or SiteSettings.load().plan,
        "paid_until": state.paid_until.isoformat() if state.paid_until else None,
        "grace_days": state.grace_days,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
    }

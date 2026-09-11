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


def license_key() -> str:
    """Ключ текущего заведения.

    В общей установке ключ свой у каждого заведения (Organization), в
    отдельной — один на инстанс в .env. Поле заведения главнее: оно
    появилось позже и заполняется при подключении точки.
    """
    from .tenancy import current_organization

    org = current_organization()
    if org is not None and org.license_key:
        return org.license_key
    return settings.LICENSE_KEY


def sync_license() -> LicenseState:
    """Сходить на пульт и обновить кэш. Ошибка сети не роняет вызывающего."""
    state = LicenseState.load()
    key = license_key()
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
        # Старый пульт признака не присылает — тогда обычная точка.
        state.internal = bool(data.get("internal", False))
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


def local_subscription():
    """Подписка заведения в этой же установке, если она тут заведена.

    В общей установке подписки лежат рядом с данными (приложение billing),
    и спрашивать их по сети у самого себя незачем. Внешняя установка
    подписок не хранит — там работает сверка по HTTP.
    """
    from .tenancy import current_organization

    org = current_organization()
    if org is None:
        return None
    return getattr(org, "subscription", None)


def effective_status(today: date | None = None) -> str:
    """Статус подписки, который исполняет middleware.

    Без ключа лицензии (автономная установка) всегда active. Пока не было
    ни одной успешной сверки — тоже active: новая точка не должна стоять
    из-за того, что beat ещё не добежал.
    """
    subscription = local_subscription()
    if subscription is not None:
        return subscription.status(today)
    if not license_key():
        return ACTIVE
    state = LicenseState.load()
    # Своя точка не биллится: дата в пульте у неё техническая (триал), и
    # считать по ней лестницу нельзя — заблокировала бы сама себя.
    if state.internal:
        return ACTIVE
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
    subscription = local_subscription()
    if subscription is not None:
        return {
            "enabled": True,
            "status": subscription.status(),
            "internal": subscription.is_internal,
            "plan": subscription.plan,
            "paid_until": subscription.paid_until.isoformat(),
            "grace_days": subscription.grace_days,
            "checked_at": None,  # сверяться не с кем: подписка здесь же
        }
    state = LicenseState.load()
    return {
        "enabled": bool(license_key()),
        "status": effective_status(),
        "internal": state.internal,
        "plan": state.plan or SiteSettings.load().plan,
        "paid_until": state.paid_until.isoformat() if state.paid_until else None,
        "grace_days": state.grace_days,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
    }

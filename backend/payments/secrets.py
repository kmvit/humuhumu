"""Шифрование доступов к банку перед записью в базу.

Почему вообще шифруем, если ключ лежит на том же сервере. База уезжает
дальше, чем окружение: ночной дамп, копия для разбора бага, реплика,
чей-то доступ «только посмотреть отчёт». Ключ шифрования живёт в
переменной окружения и ни в один дамп не попадает — значит, унесённый
дамп сам по себе не даёт чужому эквайрингу.

Ключ берём из ACQUIRING_ENCRYPTION_KEY, а если его не задали — выводим
из DJANGO_SECRET_KEY. Отдельная переменная лучше (секрет Django меняют
чаще), но требовать её на каждой установке значит сломать обновление у
тех, кто просто сделает git pull.

Смена ключа делает старые доступы нечитаемыми. Это не авария: decrypt()
вернёт пустое, эквайринг станет «нет доступов», владелец введёт ключи
заново. Молча пускать платежи мимо банка нельзя, а падать по всему
сайту из-за одной настройки — тем более.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    raw = os.getenv("ACQUIRING_ENCRYPTION_KEY", "").strip()
    if raw:
        return Fernet(raw.encode())
    # Fernet ждёт 32 байта в urlsafe-base64 — секрет Django произвольной
    # длины к этому виду приводим хешом, а не обрезкой.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(values: dict) -> str:
    """Доступы → строка для хранения. Пустой набор не шифруем вовсе."""
    if not values:
        return ""
    return _fernet().encrypt(json.dumps(values, ensure_ascii=False).encode()).decode()


def decrypt(token: str) -> dict:
    """Строка из базы → доступы. Нечитаемое — пустой набор и запись в лог."""
    if not token:
        return {}
    try:
        data = json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        logger.warning(
            "Доступы к банку не расшифровываются: сменился ключ шифрования "
            "или запись повреждена. Эквайринг считаем ненастроенным."
        )
        return {}
    return data if isinstance(data, dict) else {}

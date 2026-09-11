"""Фоновые задачи склада: распознавание фото чеков."""
from __future__ import annotations

import logging
import mimetypes

from celery import shared_task

from core.tenancy import organization_context

from .models import ReceiptScan
from .receipt_ai import build_draft, recognize_receipt

logger = logging.getLogger(__name__)


@shared_task
def process_receipt_scan(scan_id: int) -> None:
    """Распознать фото чека и сохранить черновик в ReceiptScan.parsed.

    Идемпотентна: работает только со сканами в статусе PENDING.
    """
    # Задача выполняется в воркере, где заведение никто не выбирал: HTTP-
    # запроса нет, домена нет. Поэтому скан ищем в обход фильтра (по id),
    # а дальше работаем строго от имени ЕГО заведения — иначе распознавание
    # полезло бы в номенклатуру соседнего кафе.
    scan = ReceiptScan.all_objects.filter(pk=scan_id).first()
    if scan is None or scan.status != ReceiptScan.Status.PENDING:
        return

    with organization_context(scan.organization):
        _process(scan, scan_id)


def _process(scan, scan_id: int) -> None:
    try:
        scan.image.open("rb")
        try:
            image_bytes = scan.image.read()
        finally:
            scan.image.close()
        mime = mimetypes.guess_type(scan.image.name)[0] or "image/jpeg"

        raw = recognize_receipt(image_bytes, mime)
        scan.parsed = build_draft(raw)
        scan.status = ReceiptScan.Status.PARSED
        scan.error = ""
    except Exception as exc:  # noqa: BLE001 — любая ошибка = скан в статус FAILED
        logger.exception("Не удалось распознать чек scan_id=%s", scan_id)
        scan.status = ReceiptScan.Status.FAILED
        scan.error = str(exc)[:500]

    scan.save(update_fields=["parsed", "status", "error", "updated_at"])

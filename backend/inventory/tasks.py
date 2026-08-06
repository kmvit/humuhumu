"""Фоновые задачи склада: распознавание фото чеков."""
from __future__ import annotations

import logging
import mimetypes

from celery import shared_task

from .models import ReceiptScan
from .receipt_ai import build_draft, recognize_receipt

logger = logging.getLogger(__name__)


@shared_task
def process_receipt_scan(scan_id: int) -> None:
    """Распознать фото чека и сохранить черновик в ReceiptScan.parsed.

    Идемпотентна: работает только со сканами в статусе PENDING.
    """
    scan = ReceiptScan.objects.filter(pk=scan_id).first()
    if scan is None or scan.status != ReceiptScan.Status.PENDING:
        return

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

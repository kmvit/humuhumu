"""Распознавание фото чека vision-моделью и подготовка черновика прихода.

Поток: фото → LLM (structured output: список позиций) → нормализация единиц к
базовым (г/мл/шт) → fuzzy-сопоставление с номенклатурой StockItem. Результат —
черновик, который кладовщик правит и подтверждает вручную (остатки не трогаются).
"""
from __future__ import annotations

import base64
import json
import logging
from difflib import SequenceMatcher

from django.conf import settings

from core.llm import LLMError, create_client

from .models import StockItem

logger = logging.getLogger(__name__)

# Порог уверенности fuzzy-сопоставления: ниже — позицию показываем как «не найдено».
MATCH_THRESHOLD = 0.62

# Приведение единиц из чека к базовой единице позиции.
# Ключ — нормализованный токен единицы из чека, значение — (семейство, множитель).
# Семейство сверяется с StockItem.unit: g / ml / pcs.
_UNIT_MAP = {
    "кг": ("g", 1000), "kg": ("g", 1000), "килограмм": ("g", 1000),
    "г": ("g", 1), "гр": ("g", 1), "g": ("g", 1), "грамм": ("g", 1),
    "л": ("ml", 1000), "l": ("ml", 1000), "литр": ("ml", 1000),
    "мл": ("ml", 1), "ml": ("ml", 1), "миллилитр": ("ml", 1),
    "шт": ("pcs", 1), "штук": ("pcs", 1), "pcs": ("pcs", 1), "ед": ("pcs", 1),
    "уп": ("pcs", 1), "упак": ("pcs", 1), "пач": ("pcs", 1), "бут": ("pcs", 1),
}

_SYSTEM_PROMPT = (
    "Ты распознаёшь товарные чеки/накладные поставщиков для склада кафе. "
    "На фото — чек. Извлеки КАЖДУЮ товарную позицию строкой. "
    "Для каждой позиции верни: name (наименование как в чеке), quantity (число), "
    "unit (единица как в чеке: кг, г, л, мл, шт, уп и т.п.), unit_cost (цена за "
    "единицу в рублях, число, или null если не видно). Также supplier (поставщик/"
    "магазин), date (дата чека в ISO или null), total (итог по чеку или null). "
    "Не выдумывай позиции, которых нет. Числа — с точкой, без пробелов и валюты."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "total": {"type": ["number", "null"]},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "unit_cost": {"type": ["number", "null"]},
                },
                "required": ["name", "quantity", "unit", "unit_cost"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["supplier", "date", "total", "lines"],
    "additionalProperties": False,
}


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _parse_json(content: str) -> dict:
    """Достать JSON из ответа модели, срезая возможные markdown-ограждения."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.lstrip("json").lstrip("\n").rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Невалидный JSON от модели: {exc}")


def recognize_receipt(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    """Один vision-вызов: фото → {supplier, date, total, lines:[...]}.

    Поднимает LLMError при недоступности модели или невалидном ответе.
    """
    client = create_client()
    model = settings.OPENAI_RECEIPT_MODEL
    b64 = base64.b64encode(image_bytes).decode("ascii")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Распознай позиции этого чека."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "receipt", "schema": _SCHEMA, "strict": True},
        },
        temperature=0.1,
    )
    content = response.choices[0].message.content or ""
    logger.info("Receipt scan | model=%s | answer=%r", model, content[:300])
    return _parse_json(content)


def _convert_to_base(quantity, unit: str, item_unit: str):
    """Привести количество из чека к базовой единице позиции.

    Возвращает (base_quantity, ok): ok=False, если семейство единиц не совпало
    (например в чеке «кг», а позиция штучная) — тогда base_quantity=сырое число
    и строку нужно проверить вручную.
    """
    fam_factor = _UNIT_MAP.get(_normalize(unit))
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        return None, False
    if not fam_factor:
        return qty, False
    family, factor = fam_factor
    if family != item_unit:
        return qty, False
    return qty * factor, True


def _best_match(name: str, items):
    """Найти ближайшую позицию номенклатуры по названию. -> (item|None, score)."""
    target = _normalize(name)
    best, best_score = None, 0.0
    for it in items:
        score = SequenceMatcher(None, target, _normalize(it.name)).ratio()
        # бонус за вхождение подстроки (сокращения поставщика)
        cand = _normalize(it.name)
        if target and (target in cand or cand in target):
            score = max(score, 0.85)
        if score > best_score:
            best, best_score = it, score
    return best, best_score


def build_draft(raw: dict) -> dict:
    """Из сырого ответа модели собрать черновик с сопоставлением и единицами."""
    items = list(StockItem.objects.filter(is_active=True).select_related("category"))
    lines = []
    for row in raw.get("lines") or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        match, score = _best_match(name, items)
        matched = match if score >= MATCH_THRESHOLD else None

        base_qty, unit_ok = (None, False)
        if matched is not None:
            base_qty, unit_ok = _convert_to_base(
                row.get("quantity"), row.get("unit") or "", matched.unit
            )

        lines.append({
            "raw_name": name,
            "raw_quantity": row.get("quantity"),
            "raw_unit": (row.get("unit") or "").strip(),
            "unit_cost": row.get("unit_cost"),
            "matched_item_id": matched.id if matched else None,
            "matched_item_name": matched.name if matched else None,
            "matched_item_unit": matched.unit if matched else None,
            "base_quantity": base_qty,
            "unit_ok": unit_ok,
            "confidence": round(score, 2),
        })

    return {
        "supplier": (raw.get("supplier") or "").strip(),
        "date": raw.get("date"),
        "total": raw.get("total"),
        "lines": lines,
    }

"""Распознавание фото чека vision-моделью и подготовка черновика прихода.

Поток: фото → LLM (structured output: список позиций) → нормализация единиц к
базовым (г/мл/шт) → сопоставление с вариантами товаров (StockItem): сначала точно
по запомненным названиям из чеков (StockItemAlias), потом fuzzy по названию товара
и варианта. Результат — черновик, который кладовщик правит и подтверждает вручную
(остатки не трогаются).
"""
from __future__ import annotations

import base64
import json
import logging
from difflib import SequenceMatcher

from django.conf import settings

from core.llm import LLMError, create_client

from .models import StockItem, normalize_name

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
    "единицу в рублях, число, или null если не видно), line_total (сумма по "
    "строке в рублях — итоговая, со скидкой, или null если не видно). "
    "Также supplier (поставщик/"
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
                    "line_total": {"type": ["number", "null"]},
                },
                "required": ["name", "quantity", "unit", "unit_cost", "line_total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["supplier", "date", "total", "lines"],
    "additionalProperties": False,
}


_normalize = normalize_name


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

    Возвращает (base_quantity, ok, factor): ok=False, если семейство единиц не
    совпало (например в чеке «кг», а позиция штучная) — тогда
    base_quantity=сырое число и строку нужно проверить вручную.
    factor нужен ещё и для цены: «180 ₽ за кг» — это 0.18 ₽ за грамм.
    """
    fam_factor = _UNIT_MAP.get(_normalize(unit))
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        return None, False, 1.0
    if not fam_factor:
        return qty, False, 1.0
    family, factor = fam_factor
    if family != item_unit:
        return qty, False, 1.0
    return qty * factor, True, factor


def _line_cost(line_total, unit_cost, base_qty, factor: float):
    """Цена за базовую единицу: из суммы по строке или из цены за единицу чека."""
    try:
        if line_total is not None and base_qty:
            return round(float(line_total) / float(base_qty), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        if unit_cost is not None and factor:
            return round(float(unit_cost) / float(factor), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _item_names(item) -> list[str]:
    """Названия, под которыми товар может встретиться в чеке: своё и варианты."""
    return [item.name, *(a.name for a in item.aliases.all())]


def _best_match(name: str, items, aliases: dict):
    """Найти ближайший товар склада по названию из чека. -> (item|None, score).

    Запомненное название из прошлых чеков (вариант) бьёт любое fuzzy-сравнение.
    """
    target = _normalize(name)
    exact = aliases.get(target)
    if exact is not None:
        return exact, 1.0

    best, best_score = None, 0.0
    for it in items:
        for cand in (_normalize(n) for n in _item_names(it)):
            score = SequenceMatcher(None, target, cand).ratio()
            # бонус за вхождение подстроки (сокращения поставщика)
            if target and (target in cand or cand in target):
                score = max(score, 0.85)
            if score > best_score:
                best, best_score = it, score
    return best, best_score


def build_draft(raw: dict) -> dict:
    """Из сырого ответа модели собрать черновик с сопоставлением и единицами."""
    items = list(
        StockItem.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("aliases")
    )
    aliases = {a.norm: it for it in items for a in it.aliases.all()}
    lines = []
    for row in raw.get("lines") or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        match, score = _best_match(name, items, aliases)
        matched = match if score >= MATCH_THRESHOLD else None

        base_qty, unit_ok, factor = (None, False, 1.0)
        if matched is not None:
            base_qty, unit_ok, factor = _convert_to_base(
                row.get("quantity"), row.get("unit") or "", matched.unit
            )

        # Цена — за базовую единицу. Сумма по строке надёжнее цены за единицу:
        # она есть в любом чеке и уже учитывает скидку. Если суммы нет, берём
        # цену из чека и делим на тот же коэффициент, что и количество, иначе
        # «180 ₽ за кг» превратились бы в 180 ₽ за грамм.
        base_cost = _line_cost(
            row.get("line_total"), row.get("unit_cost"), base_qty, factor
        )

        lines.append({
            "raw_name": name,
            "raw_quantity": row.get("quantity"),
            "raw_unit": (row.get("unit") or "").strip(),
            "line_total": row.get("line_total"),
            "unit_cost": base_cost,
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

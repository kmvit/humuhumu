"""Фото блюда, нарисованное нейросетью: сборка запроса и его выполнение.

Поток: владелец выбирает блюда и посуду → здесь собирается текст запроса →
картинка рисуется в фоне (catalog.tasks) → попадает в галерею черновиков.
В меню ничего не подставляется само: выбирает владелец.

Смысл образцов посуды — в том, что модель рисует напиток В НАШЕМ стакане.
Без них получается стоковая картинка, которая к заведению отношения не
имеет, и гость на выдаче увидит не то, что выбирал.
"""
from __future__ import annotations

import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image

from core.images import generate_image

logger = logging.getLogger(__name__)

#: Сторона, до которой ужимаем образец перед отправкой. Фото стакана с
#: телефона — это 4 мегабайта, а в base64 ещё треть сверху: через туннель
#: такой запрос идёт мучительно долго и стоит дороже (входные токены).
REFERENCE_SIDE = 1024

#: Сторона готовой картинки и качество WebP. Модель отдаёт ~1,5 МБ PNG на
#: каждое блюдо; меню от этого не выиграет ничего, а диск съест.
RESULT_SIDE = 1024
RESULT_QUALITY = 82

STYLE_PROMPTS = {
    "studio": (
        "студийная предметная съёмка на однотонном светлом фоне, мягкий "
        "рассеянный свет, аккуратная тень под посудой"
    ),
    "counter": (
        "снято на барной стойке кофейни, тёплый свет, размытый фон с "
        "кофемашиной, ракурс три четверти"
    ),
    "wood": (
        "стоит на деревянном столе, дневной свет из окна, естественные "
        "тени, вид три четверти сверху"
    ),
    "dark": (
        "тёмный графитовый фон, контровой свет, глубокие тени, крупный "
        "план, настроение премиального меню"
    ),
}

#: Что не должно попасть на фото меню ни при каких настройках.
_FORBIDDEN = (
    "Без текста, надписей, ценников, логотипов чужих брендов и водяных "
    "знаков. Без людей и рук в кадре. Естественные цвета, реалистичная "
    "порция — столько, сколько правда помещается в эту посуду."
)


def build_prompt(product, dishware=(), *, style=None, extra="", variant_label="") -> str:
    """Собрать текст запроса к модели по блюду, посуде и стилю заведения."""
    parts = [
        f"Фотореалистичное фото для меню кафе: «{product.name}».",
    ]
    description = (product.description or "").strip()
    if description:
        parts.append(f"Описание блюда: {description}")
    category = getattr(product.category, "name", "")
    if category:
        parts.append(f"Категория меню: {category}.")
    if variant_label:
        parts.append(f"Порция: {variant_label}.")

    samples = [d for d in dishware if d is not None]
    if samples:
        names = "; ".join(
            f"{d.name}{' — ' + d.note if d.note else ''}" for d in samples
        )
        parts.append(
            "Подача строго в нашей посуде с приложенных фото "
            f"({names}): та же форма, цвет, материал и логотип, ничего не "
            "меняй и не дорисовывай."
        )
    else:
        parts.append("Посуда — простая и нейтральная, под стиль кофейни.")

    parts.append(STYLE_PROMPTS.get(style or "", STYLE_PROMPTS["studio"]) + ".")
    if extra.strip():
        parts.append(extra.strip())
    parts.append(_FORBIDDEN)
    return " ".join(parts)


def _reference(field) -> tuple[bytes, str] | None:
    """Файл-образец, ужатый до разумного размера. None, если не читается."""
    if not field:
        return None
    try:
        with field.open("rb") as f:
            img = Image.open(f)
            img.load()
    except Exception:  # noqa: BLE001 — битый образец не должен ронять генерацию
        logger.warning("Не удалось прочитать образец %s", getattr(field, "name", "?"))
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((REFERENCE_SIDE, REFERENCE_SIDE))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def collect_references(dishware, background=None) -> list[tuple[bytes, str]]:
    """Образцы для запроса: посуда, затем фон заведения."""
    refs = []
    for sample in dishware:
        ref = _reference(sample.image)
        if ref:
            refs.append(ref)
    if background:
        ref = _reference(background)
        if ref:
            refs.append(ref)
    return refs


def _to_webp(data: bytes) -> ContentFile:
    """Ужать ответ модели до WebP — в меню разницы не видно, на диске видно."""
    img = Image.open(BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((RESULT_SIDE, RESULT_SIDE))
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=RESULT_QUALITY, method=6)
    return ContentFile(buf.getvalue())


def run_generation(generation) -> None:
    """Нарисовать картинку для генерации и сохранить результат.

    Ошибку не поднимает: она ложится в саму запись, чтобы владелец увидел
    в галерее, что именно не вышло, и мог перезапустить одну позицию.
    """
    from .models import ImageGeneration, MenuImageSettings

    if generation.status != ImageGeneration.Status.PENDING:
        return

    site = MenuImageSettings.current()
    try:
        refs = collect_references(list(generation.dishware.all()), site.background)
        image, cost = generate_image(
            generation.prompt,
            references=refs,
            aspect_ratio=site.aspect_ratio or "1:1",
        )
        generation.image.save(
            f"p{generation.product_id}-g{generation.pk}.webp",
            _to_webp(image.data),
            save=False,
        )
        generation.model = settings.OPENAI_IMAGE_MODEL
        generation.cost_usd = round(cost, 4)
        generation.status = ImageGeneration.Status.READY
        generation.error = ""
    except Exception as exc:  # noqa: BLE001 — любая осечка = статус «ошибка»
        logger.exception("Не удалось сгенерировать фото id=%s", generation.pk)
        generation.status = ImageGeneration.Status.FAILED
        generation.error = str(exc)[:500]

    generation.save(
        update_fields=["image", "model", "cost_usd", "status", "error", "updated_at"]
    )

"""Генерация изображений через OpenRouter Image API.

Почему не через openai SDK, как распознавание чеков (core/llm.py): у
OpenRouter картинки живут на собственном эндпоинте `POST /images`, которого
в OpenAI-совместимом протоколе нет. Ключ, базовый адрес и туннель — общие,
поэтому здесь только транспорт, а настройки читаются те же.

Ответ приходит base64-строкой и, что важнее, несёт `usage.cost` — реальную
стоимость запроса в долларах. Её сохраняем: лимит лимитом, но владелец
должен видеть деньги, а не абстрактный счётчик.
"""
from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass

import httpx
from django.conf import settings

from .llm import LLMError

logger = logging.getLogger(__name__)

#: Расширение файла по типу картинки — имя файла собираем сами.
_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class GeneratedImage:
    """Готовая картинка: байты и тип."""

    data: bytes
    mime: str

    @property
    def extension(self) -> str:
        return _EXTENSIONS.get(self.mime, "png")


def data_url(image: bytes, mime: str) -> str:
    """Картинка как data:-ссылка — так референсы уезжают в запросе.

    Ссылкой на наш /media обойтись нельзя: media лежит за прокси заведения
    и снаружи не открывается, а OpenRouter ходит за референсом сам.
    """
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def _error_text(response: httpx.Response) -> str:
    """Понятный текст ошибки из ответа OpenRouter."""
    try:
        body = response.json()
    except ValueError:
        return f"OpenRouter ответил {response.status_code}"
    message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    return f"OpenRouter ответил {response.status_code}: {message or response.text[:200]}"


def generate_image(
    prompt: str,
    *,
    references: list[tuple[bytes, str]] | None = None,
    aspect_ratio: str = "1:1",
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[GeneratedImage, float]:
    """Нарисовать одну картинку по описанию. Вернуть её и стоимость в $.

    `references` — образцы (наша посуда, фон): модель повторяет их на
    результате. Одна картинка за вызов, а не n штук: пачка у нас и так
    разложена по отдельным задачам, и ошибка одной не роняет остальные.
    """
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise LLMError("OPENAI_API_KEY не задан")

    base_url = (settings.OPENAI_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        base_url = "https://openrouter.ai/api/v1"

    payload: dict = {
        "model": (model or settings.OPENAI_IMAGE_MODEL).strip(),
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": 1,
    }
    if references:
        payload["input_references"] = [
            {"type": "image_url", "image_url": {"url": data_url(data, mime)}}
            for data, mime in references
        ]
    provider = (settings.OPENAI_IMAGE_PROVIDER or "").strip()
    if provider:
        # allow_fallbacks не выключаем: если дешёвый flex прилёг, лучше
        # нарисовать дороже, чем отдать владельцу ошибку.
        payload["provider"] = {"only": [provider], "allow_fallbacks": True}

    headers = {"Authorization": f"Bearer {api_key}"}
    site_url = (settings.OPENROUTER_SITE_URL or "").strip()
    if "openrouter" in base_url and site_url:
        headers["HTTP-Referer"] = site_url
        headers["X-Title"] = (settings.OPENROUTER_APP_NAME or "humu").strip()

    client_kwargs: dict = {"timeout": timeout or settings.OPENAI_IMAGE_TIMEOUT}
    # Прокси/туннель только для этого запроса: OpenRouter недоступен из РФ.
    proxy_url = (settings.OPENAI_PROXY_URL or "").strip()
    if proxy_url:
        if "://" not in proxy_url:
            proxy_url = f"http://{proxy_url}"
        client_kwargs["proxy"] = proxy_url

    with httpx.Client(**client_kwargs) as client:
        try:
            response = client.post(f"{base_url}/images", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"Не достучались до OpenRouter: {exc}") from exc

    if response.status_code >= 400:
        raise LLMError(_error_text(response))

    try:
        body = response.json()
    except ValueError as exc:
        raise LLMError("OpenRouter вернул не JSON") from exc

    items = body.get("data") or []
    if not items:
        raise LLMError("Модель не вернула изображение")

    raw = items[0].get("b64_json")
    if not raw:
        raise LLMError("В ответе нет картинки")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LLMError("Картинку не удалось раскодировать") from exc

    mime = items[0].get("media_type") or "image/png"
    cost = float((body.get("usage") or {}).get("cost") or 0)
    return GeneratedImage(data=data, mime=mime), cost

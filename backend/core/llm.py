"""Клиент OpenAI-совместимого API (OpenRouter) для humu.

Повторяет схему проекта tourplanner: официальный openai SDK, направленный на
OpenRouter через OPENAI_BASE_URL. Прокси (OPENAI_PROXY_URL) применяется ТОЛЬКО к
LLM-трафику — это туннель на зарубежный сервер, т.к. OpenRouter недоступен из РФ.
Остальной исходящий трафик (БД, S3) идёт напрямую.
"""
from __future__ import annotations

from django.conf import settings
from openai import OpenAI


class LLMError(RuntimeError):
    """Проблема при обращении к LLM (нет ключа, сеть, невалидный ответ)."""


def create_client(timeout: float | None = None) -> OpenAI:
    """Собрать openai-клиент из настроек. Поднимает LLMError, если нет ключа."""
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise LLMError("OPENAI_API_KEY не задан")

    client_kwargs: dict = {
        "api_key": api_key,
        # Без ретраев: при невалидном ответе SDK молча ретраит до 2 раз и
        # держит воркер/задачу лишние минуты. Ошибку обрабатываем сами.
        "max_retries": 0,
        "timeout": timeout if timeout is not None else settings.OPENAI_RECEIPT_TIMEOUT,
    }

    base_url = (settings.OPENAI_BASE_URL or "").strip()
    if base_url:
        client_kwargs["base_url"] = base_url

    # OpenRouter принимает необязательные заголовки аналитики/лимитов.
    site_url = (settings.OPENROUTER_SITE_URL or "").strip()
    site_name = (settings.OPENROUTER_APP_NAME or "humu").strip()
    if base_url and "openrouter" in base_url and site_url:
        client_kwargs["default_headers"] = {
            "HTTP-Referer": site_url,
            "X-Title": site_name,
        }

    # Прокси/туннель только для LLM. socks5:// требует пакет httpx[socks].
    proxy_url = (settings.OPENAI_PROXY_URL or "").strip()
    if proxy_url:
        import httpx

        if "://" not in proxy_url:
            proxy_url = f"http://{proxy_url}"
        client_kwargs["http_client"] = httpx.Client(
            proxy=proxy_url, timeout=client_kwargs["timeout"]
        )

    return OpenAI(**client_kwargs)

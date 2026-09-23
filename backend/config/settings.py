"""Настройки Django для сервиса кафе."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
if DEBUG:
    # в разработке разрешаем любой хост (фронт ходит на backend:8000 внутри Docker)
    ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # сторонние
    "rest_framework",
    "corsheaders",
    # приложения проекта
    "core",
    "users",
    "catalog",
    "wallet",
    "loyalty",
    "orders",
    "payments",
    "inventory",
    "shifts",
    "finance",
    # коммерческая часть: подписки заведений (бывший пульт)
    "billing",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # порядок важен: сначала узнаём заведение, потом проверяем его подписку
    "core.middleware.TenantMiddleware",
    "core.middleware.LicenseMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "humu"),
        "USER": os.getenv("POSTGRES_USER", "humu"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "humu"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = os.getenv("DJANGO_LANGUAGE_CODE", "ru-ru")
# Часовой пояс заведения: от него зависят границы смены и выручка дня.
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

# ─────────── Логи ───────────
# Django по умолчанию отдаёт наружу только предупреждения и ошибки, и
# движение денег — кто заплатил, чем и по какому платежу — в прод-логи
# не попадало вовсе: разбор случая «гость заплатил, а заказ открыт»
# упирался в то, что доводку платежа в логах просто не видно.
# Поэтому свои приложения пишем на INFO, а чужую болтовню (django, httpx,
# celery на каждый запрос) оставляем на WARNING.
LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    # Чужие логгеры — только то, что действительно сломалось.
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        name: {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False}
        for name in (
            "payments", "orders", "billing", "wallet",
            "core", "catalog", "inventory", "loyalty", "shifts", "users",
        )
    },
}

# Отказ клиенту (400, 403, 404) — обычное дело, а не происшествие: их и
# так видно в логе nginx с адресом и временем. В логе приложения нужны
# только пятисотки, иначе важное тонет.
LOGGING["loggers"]["django.request"] = {
    "handlers": ["console"], "level": "ERROR", "propagate": False,
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# auth.W004 — «username не уникален». Так и задумано: логин уникален
# внутри заведения, а вход умеет это учитывать (users/auth.py).
SILENCED_SYSTEM_CHECKS = ["auth.W004"]

# Вход и токены — в границах заведения: логины уникальны внутри кафе,
# а не глобально (см. users/auth.py).
AUTHENTICATION_BACKENDS = ["users.auth.OrganizationBackend"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "users.auth.OrganizationJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    # access живёт недолго, но фронт тихо продлевает его по refresh —
    # персонал не разлогинивается до истечения refresh (планшеты кухни/бара)
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
}

CORS_ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

# На проде фронт и API на одном домене за nginx: нужен доверенный origin для админки
# и понимание, что снаружи может быть https.
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://redis:6379/0")

# ─────────── Лицензия «Падачи» ───────────
# Ключ выдаёт пульт (padacha.ru/pult/) при подключении точки. Пустой ключ —
# лицензирование выключено: автономная установка (дев, свой сервер), тариф
# правится руками в админке. С ключом инстанс раз в сутки сверяется с
# пультом (см. core/license.py): забирает тариф и «оплачено до», а
# блокировку за неоплату исполняет сам.
LICENSE_KEY = os.getenv("LICENSE_KEY", "")
LICENSE_URL = os.getenv("LICENSE_URL", "https://padacha.ru/api/license/")

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # Доводка онлайн-оплат: уведомление банка может не прийти вовсе (в
    # кабинете не прописан адрес) или потеряться, и тогда опрос —
    # единственный способ узнать, что гость заплатил. Пять минут — это
    # предел, сколько заказ может простоять оплаченным, но открытым.
    "settle-payments": {
        "task": "payments.tasks.settle_pending_payments_task",
        "schedule": crontab(minute="*/5"),
    },
    # Неоплаченные заказы стойки: гость передумал — заказ уходит сам,
    # иначе за смену их накопятся десятки (orders/tasks.py).
    "cancel-stale-unpaid": {
        "task": "orders.tasks.cancel_stale_unpaid_orders_task",
        "schedule": crontab(minute="*/5"),
    },
}

if LICENSE_KEY:
    CELERY_BEAT_SCHEDULE["sync-license"] = {
        "task": "core.tasks.sync_license_task",
        # ночью, в неровное время — чтобы точки не стучались хором
        "schedule": crontab(hour=4, minute=17),
    }

# ─────────── LLM (OpenRouter) для распознавания чеков ───────────
# Подключение как в проекте tourplanner: openai SDK, направленный на OpenRouter.
# OPENAI_PROXY_URL — туннель на зарубежный сервер (OpenRouter недоступен из РФ),
# применяется ТОЛЬКО к LLM-трафику. Формат: http://user:pass@host:port или socks5://...
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
OPENAI_PROXY_URL = os.getenv("OPENAI_PROXY_URL", "")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "humu")

# Демо-стенд: витрина продукта с выдуманным кафе. Включается только на
# отдельной установке — команда наполнения демо-данными стирает базу и
# без этого флага работать отказывается.
DEMO_STAND = os.getenv("DEMO_STAND", "") == "1"
# Vision-модель для распознавания чеков (меняется без деплоя).
OPENAI_RECEIPT_MODEL = os.getenv("OPENAI_RECEIPT_MODEL", "google/gemini-2.5-flash")
OPENAI_RECEIPT_TIMEOUT = float(os.getenv("OPENAI_RECEIPT_TIMEOUT", "60"))
# Распознавание чека через celery-воркер (сервис `worker` в compose). Если воркера
# нет — поставь "0", тогда распознавание пойдёт синхронно прямо в запросе.
RECEIPT_SCAN_ASYNC = os.getenv("RECEIPT_SCAN_ASYNC", "1") == "1"

# ─────────── Генерация фото блюд (OpenRouter Image API) ───────────
# Картинки у OpenRouter живут на своём эндпоинте POST /images, а не в
# chat completions, поэтому ходим туда напрямую httpx — но через тот же
# ключ, базовый адрес и туннель, что и распознавание чеков.
# Нанобанана: лучше всех повторяет нашу посуду с приложенного фото.
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "google/gemini-2.5-flash-image")
# Эндпоинт провайдера: flex вдвое дешевле обычного (≈1,7 ₽ против ≈3,5 ₽
# за картинку) ценой скорости — а генерим мы в фоне, торопиться некуда.
# Пустая строка — пусть OpenRouter выбирает сам.
OPENAI_IMAGE_PROVIDER = os.getenv("OPENAI_IMAGE_PROVIDER", "google-ai-studio/flex")
# flex-тариф думает дольше обычного: минута с запасом.
OPENAI_IMAGE_TIMEOUT = float(os.getenv("OPENAI_IMAGE_TIMEOUT", "180"))
# Генерация через celery-воркер. Нет воркера — поставь "0", тогда картинка
# будет рисоваться прямо в запросе (и держать его те самые полминуты).
IMAGE_GEN_ASYNC = os.getenv("IMAGE_GEN_ASYNC", "1") == "1"

"""Абстракция провайдера оплаты.

Каркас под приём оплаты через кассу-терминал (напр. смарт-касса Т-Банка/aQsi).
Реальный провайдер вызывает API устройства/облака в start(); результат приходит
асинхронно (вебхук/колбэк) и применяется через services.apply_payment_result.
Сейчас активен MockProvider — эмулирует терминал, результат подаёт фронт/дев-ручка.
"""
from django.conf import settings


class BaseProvider:
    """Интерфейс провайдера. Наследники реализуют start()."""

    name = "base"

    def start(self, payment):
        """Инициировать оплату (отправить сумму/чек на терминал).

        Должен вернуть payment (при необходимости заполнив external_id).
        Результат оплаты приходит отдельно и применяется apply_payment_result.
        """
        raise NotImplementedError


class MockProvider(BaseProvider):
    """Заглушка кассы: «отправляет» на терминал, ждёт внешнего подтверждения."""

    name = "mock"

    def start(self, payment):
        payment.external_id = f"mock-{payment.pk}"
        payment.save(update_fields=["external_id"])
        return payment


# Реестр провайдеров. Реальный терминал добавится сюда как ещё один класс.
_PROVIDERS = {MockProvider.name: MockProvider}


def get_provider() -> BaseProvider:
    name = getattr(settings, "PAYMENT_PROVIDER", "mock")
    return _PROVIDERS.get(name, MockProvider)()

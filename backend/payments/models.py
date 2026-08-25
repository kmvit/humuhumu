from django.db import models


class Payment(models.Model):
    """Платёж через провайдера (ЮKassa/CloudPayments). Обслуживает оплату заказа и пополнение токенов."""

    class Purpose(models.TextChoices):
        ORDER = "order", "Оплата заказа"
        TOPUP = "topup", "Пополнение токенов"

    class Status(models.TextChoices):
        PENDING = "pending", "Создан"
        SUCCEEDED = "succeeded", "Оплачен"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Отменён"

    class Method(models.TextChoices):
        CASH = "cash", "Наличные"
        CARD = "card", "Карта"
        QR = "qr", "QR / СБП"

    purpose = models.CharField("Назначение", max_length=16, choices=Purpose.choices)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PENDING
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=12, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Заказ",
    )
    token_package = models.ForeignKey(
        "wallet.TokenPackage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Пакет токенов",
    )
    method = models.CharField(
        "Способ", max_length=8, choices=Method.choices, blank=True, default=""
    )
    provider = models.CharField("Провайдер", max_length=32, default="yookassa")
    external_id = models.CharField(
        "ID у провайдера", max_length=128, unique=True, null=True, blank=True
    )
    fiscal_receipt = models.CharField("Фискальный чек", max_length=64, blank=True, default="")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Платёж №{self.pk} — {self.amount} ₽"

from django.conf import settings
from django.db import models


class Order(models.Model):
    """Заказ клиента."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает оплаты"
        PAID = "paid", "Оплачен"
        PREPARING = "preparing", "Готовится"
        READY = "ready", "Готов"
        DONE = "done", "Выдан"
        CANCELLED = "cancelled", "Отменён"

    class PayMethod(models.TextChoices):
        CARD = "card", "Карта"
        TOKENS = "tokens", "Токены"

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Клиент",
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_orders",
        verbose_name="Кассир",
    )
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PENDING
    )
    pay_method = models.CharField("Способ оплаты", max_length=16, choices=PayMethod.choices)
    total = models.DecimalField("Сумма", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заказ №{self.pk}"

    def recalc_total(self):
        """Пересчитать сумму по позициям."""
        self.total = sum((item.subtotal for item in self.items.all()), start=0)
        return self.total


class OrderItem(models.Model):
    """Позиция заказа. Цена фиксируется на момент покупки."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items", verbose_name="Заказ"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, verbose_name="Товар"
    )
    quantity = models.PositiveIntegerField("Количество", default=1)
    unit_price = models.DecimalField("Цена за единицу", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.product} × {self.quantity}"

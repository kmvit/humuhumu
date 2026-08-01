from django.conf import settings
from django.db import models


class Order(models.Model):
    """Заказ. Создаёт официант, готовит повар, оплату фиксирует кассир-бармен."""

    class Status(models.TextChoices):
        PREPARING = "preparing", "На кухне"
        READY = "ready", "Готов"
        PAID = "paid", "Оплачен"
        CANCELLED = "cancelled", "Отменён"

    class PayMethod(models.TextChoices):
        CASH = "cash", "Наличные"
        CARD = "card", "Карта"

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Клиент",
    )
    waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waiter_orders",
        verbose_name="Официант",
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_orders",
        verbose_name="Кассир-бармен",
    )
    table = models.CharField("Стол", max_length=32, blank=True)
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.PREPARING
    )
    pay_method = models.CharField(
        "Способ оплаты", max_length=16, choices=PayMethod.choices,
        blank=True, default=PayMethod.CASH,
    )
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

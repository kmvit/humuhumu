from django.conf import settings
from django.db import models


class Wallet(models.Model):
    """Токен-кошелёк клиента. Баланс — кэш суммы транзакций (источник правды — журнал)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
        verbose_name="Пользователь",
    )
    balance = models.DecimalField(
        "Баланс токенов", max_digits=12, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = "Кошелёк"
        verbose_name_plural = "Кошельки"

    def __str__(self):
        return f"{self.user} — {self.balance} ток."


class TokenTransaction(models.Model):
    """Журнал движений токенов. Положительная сумма — начисление, отрицательная — списание."""

    class Type(models.TextChoices):
        TOPUP = "topup", "Пополнение (живые деньги)"
        BONUS = "bonus", "Бонусные токены"
        PURCHASE = "purchase", "Списание за заказ"
        REFUND = "refund", "Возврат"
        ADJUSTMENT = "adjustment", "Ручная корректировка"

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name="Кошелёк",
    )
    type = models.CharField("Тип", max_length=16, choices=Type.choices)
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)
    balance_after = models.DecimalField("Баланс после", max_digits=12, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Заказ",
    )
    comment = models.CharField("Комментарий", max_length=255, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Транзакция токенов"
        verbose_name_plural = "Транзакции токенов"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()}: {self.amount}"


class TokenPackage(models.Model):
    """Пакет токенов: платит pay_amount ₽ → получает pay_amount + bonus_amount токенов."""

    pay_amount = models.DecimalField("Стоимость, ₽", max_digits=10, decimal_places=2)
    bonus_amount = models.DecimalField(
        "Бонусные токены", max_digits=10, decimal_places=2, default=0
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Пакет токенов"
        verbose_name_plural = "Пакеты токенов"
        ordering = ["pay_amount"]

    @property
    def total_tokens(self):
        return self.pay_amount + self.bonus_amount

    def __str__(self):
        return f"{self.pay_amount} ₽ → {self.total_tokens} ток."

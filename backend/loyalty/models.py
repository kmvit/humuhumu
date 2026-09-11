from django.conf import settings
from django.db import models

from core.tenancy import TenantModel


class LoyaltyMember(TenantModel):
    """Участник бонусной программы.

    Отдельно от wallet.Wallet намеренно: там предоплаченные токены — живые
    деньги гостя, которые он внёс вперёд. Бонусы деньгами не являются, их
    начисляет заведение, и смешивать два баланса в одном нельзя — иначе не
    отличить обязательство перед гостем от маркетинговой скидки.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loyalty",
        verbose_name="Гость",
    )
    birth_date = models.DateField("Дата рождения", null=True, blank=True)
    # кэш суммы транзакций; источник правды — журнал BonusTransaction
    balance = models.DecimalField("Баланс бонусов", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("В программе с", auto_now_add=True)

    class Meta:
        verbose_name = "Участник бонусной программы"
        verbose_name_plural = "Участники бонусной программы"
        ordering = ["-created_at"]

    @property
    def name(self) -> str:
        return self.user.first_name or self.user.username

    @property
    def phone(self) -> str:
        return self.user.phone or ""

    def __str__(self):
        return f"{self.name} ({self.phone}) — {self.balance} б."


class BonusTransaction(TenantModel):
    """Журнал бонусов. Плюс — начисление, минус — списание."""

    class Type(models.TextChoices):
        WELCOME = "welcome", "Приветственные"
        EARN = "earn", "Начисление за заказ"
        REDEEM = "redeem", "Списание за заказ"
        RETURN = "return", "Возврат списанных"
        ADJUST = "adjust", "Ручная корректировка"

    member = models.ForeignKey(
        LoyaltyMember,
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name="Участник",
    )
    type = models.CharField("Тип", max_length=12, choices=Type.choices)
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2)
    balance_after = models.DecimalField("Баланс после", max_digits=12, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bonus_transactions",
        verbose_name="Заказ",
    )
    comment = models.CharField("Комментарий", max_length=255, blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Бонусная операция"
        verbose_name_plural = "Бонусные операции"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()}: {self.amount}"

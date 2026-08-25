"""Смены персонала и расчёт дневной оплаты.

Смена — это рабочий день заведения. Состав смены ставит менеджер (роль «Склад»)
или админ; сам персонал себя в смену не отмечает, только смотрит. По составу
смены и выручке дня считается зарплата: ставка за день + доля бонуса (процент от
выручки) − доля списаний со штрафного стола, поделённые поровну на всех, кто был
в этой смене.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from users.models import User


class ShiftSettings(models.Model):
    """Параметры расчёта зарплаты — одна запись (singleton). Правится в админке."""

    daily_rate = models.DecimalField(
        "Оплата за смену", max_digits=10, decimal_places=2, default=Decimal("2000"),
        help_text="Сколько получает каждый работник за отработанный день.",
    )
    bonus_percent = models.DecimalField(
        "Бонус, % от выручки", max_digits=5, decimal_places=2, default=Decimal("9"),
        help_text="Процент от выручки дня. Делится поровну на всех в смене.",
    )
    penalty_table = models.ForeignKey(
        "orders.Table",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Штрафной стол",
        help_text=(
            "Стол, на который официант оформляет подарки гостям за косяки персонала. "
            "Сумма его заказов за день делится на всех в смене и вычитается из оплаты. "
            "В выручку дня эти заказы не идут."
        ),
    )

    class Meta:
        verbose_name = "Настройки смен и оплаты"
        verbose_name_plural = "Настройки смен и оплаты"

    def __str__(self):
        return "Настройки смен и оплаты"

    def save(self, *args, **kwargs):
        self.pk = 1  # всегда одна запись
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Shift(models.Model):
    """Рабочий день. Параметры оплаты фиксируются на момент открытия смены,
    чтобы правка ставки в админке не переписывала историю выплат."""

    date = models.DateField("Дата", unique=True)
    daily_rate = models.DecimalField(
        "Оплата за смену", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    bonus_percent = models.DecimalField(
        "Бонус, % от выручки", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    penalty_table = models.CharField(
        "Штрафной стол", max_length=32, blank=True,
        help_text="Название стола на момент открытия смены.",
    )
    manual_penalty = models.DecimalField(
        "Доп. штраф за смену", max_digits=10, decimal_places=2, default=Decimal("0"),
        help_text=(
            "Ручное списание с персонала за косяки — делится поровну на всех в "
            "смене и вычитается из выплаты, помимо штрафного стола."
        ),
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        ordering = ["-date"]

    def __str__(self):
        return f"Смена {self.date:%d.%m.%Y}"


class ShiftMember(models.Model):
    """Работник, поставленный в смену на этот день."""

    shift = models.ForeignKey(
        Shift, on_delete=models.CASCADE, related_name="members", verbose_name="Смена"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shift_members",
        verbose_name="Работник",
    )
    role = models.CharField(
        "Роль в смене", max_length=16, choices=User.Role.choices, blank=True,
        help_text="Роль на момент постановки в смену — роль в профиле может поменяться.",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_assignments",
        verbose_name="Поставил в смену",
    )
    added_at = models.DateTimeField("Поставлен", auto_now_add=True)

    class Meta:
        verbose_name = "Работник в смене"
        verbose_name_plural = "Работники в смене"
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "user"], name="unique_shift_member"
            )
        ]
        ordering = ["added_at"]

    def __str__(self):
        return f"{self.user} — {self.shift}"

"""Финансы: факты выплат персоналу.

Начисления считают смены (shifts/services.py) — здесь только то, чего там
нет: кому и сколько уже отдали. Выплат за месяц может быть несколько
(аванс и окончательный расчёт), поэтому это журнал, а не одна отметка.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class PayrollPayout(models.Model):
    """Выплата работнику за месяц. Начисление не хранится — оно считается
    из смен, иначе цифры разъедутся при правке состава смены задним числом."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payouts",
        verbose_name="Работник",
    )
    period = models.DateField(
        "Месяц", help_text="Первое число месяца, за который платим",
    )
    amount = models.DecimalField(
        "Сумма", max_digits=10, decimal_places=2, default=Decimal("0"),
    )
    paid_on = models.DateField("Дата выплаты")
    comment = models.CharField("Комментарий", max_length=200, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="payouts_created",
        verbose_name="Кто отметил",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Выплата"
        verbose_name_plural = "Выплаты"
        ordering = ("-paid_on", "-id")
        indexes = [models.Index(fields=["period", "user"])]

    def __str__(self):
        return f"{self.user} · {self.period:%m.%Y} · {self.amount} ₽"

"""Подписки заведений — коммерческая часть «Падачи».

Раньше жила отдельным сервисом (пульт на padacha.ru). Владелец решил
свести управление в одно место: заведение, его данные и его подписка
теперь в одной админке, лэндинг остаётся просто лэндингом.

Эти модели — НЕ тенантные: они смотрят поверх заведений, а не изнутри.
Поэтому обычный TenantModel им не подходит и они перечислены в
исключениях core.tests.TenancyGuardTests.

Кто здесь кто:
- Client — заказчик (кафе или сеть), у него может быть несколько точек;
- Subscription — подписка ОДНОЙ точки: тариф, оплачено до, грейс;
- Payment — поступление денег, само продлевает «оплачено до».
"""
import calendar
from datetime import date, timedelta

from django.db import models
from django.utils import timezone

from core.models import Organization, SiteSettings


def trial_end() -> date:
    """Новая точка получает бесплатную неделю — как обещает лэндинг."""
    return timezone.localdate() + timedelta(days=7)


def add_months(d: date, n: int) -> date:
    """d + n месяцев; 31-е укорачивается до конца короткого месяца."""
    m = d.month - 1 + n
    year = d.year + m // 12
    month = m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


class Client(models.Model):
    """Заказчик. Точки — через Organization.client."""

    name = models.CharField("Название", max_length=160)
    contact_person = models.CharField("Контактное лицо", max_length=160, blank=True)
    phone = models.CharField("Телефон", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    notes = models.TextField("Заметки", blank=True)
    created_at = models.DateTimeField("Появился", auto_now_add=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    """Подписка одной точки.

    Лестницу статусов считает здесь же — от текущей даты, а не хранит
    готовый статус: между сверками он протух бы.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Оплачена"
        EXPIRING = "expiring", "Заканчивается"
        GRACE = "grace", "Просрочена (грейс)"
        BLOCKED = "blocked", "Заблокирована"

    #: за сколько дней до конца оплаты показывать «заканчивается»
    EXPIRING_DAYS = 5

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="Заведение",
    )
    # Тарифы те же, что в настройках заведения: подписка их назначает,
    # а SiteSettings.plan получает при сверке.
    plan = models.CharField(
        "Тариф", max_length=8,
        choices=SiteSettings.Plan.choices,
        default=SiteSettings.Plan.START,
    )
    paid_until = models.DateField(
        "Оплачено до", default=trial_end,
        help_text="Новая точка получает неделю бесплатно",
    )
    grace_days = models.PositiveSmallIntegerField(
        "Грейс, дней", default=7,
        help_text="Сколько дней после «оплачено до» точка ещё работает",
    )
    is_internal = models.BooleanField(
        "Своя точка", default=False,
        help_text="Наше заведение: не биллится и никогда не блокируется",
    )
    notes = models.TextField("Заметки", blank=True)

    class Meta:
        verbose_name = "Подписка"
        verbose_name_plural = "Подписки"

    def __str__(self):
        return f"{self.organization} — до {self.paid_until}"

    def status(self, today: date | None = None) -> str:
        if self.is_internal:
            return self.Status.ACTIVE
        if today is None:
            today = timezone.localdate()
        if today > self.paid_until + timedelta(days=self.grace_days):
            return self.Status.BLOCKED
        if today > self.paid_until:
            return self.Status.GRACE
        if today > self.paid_until - timedelta(days=self.EXPIRING_DAYS):
            return self.Status.EXPIRING
        return self.Status.ACTIVE


class Payment(models.Model):
    """Поступление денег за точку.

    Создание платежа само продлевает «оплачено до»: отметил оплату — точка
    продлилась. Отсчёт от большего из (сегодня, старое «оплачено до»):
    ранняя оплата не сгорает, а после простоя не оплачиваются задним
    числом пустые месяцы.
    """

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments",
        verbose_name="Подписка",
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=10, decimal_places=2)
    months = models.PositiveSmallIntegerField("Месяцев", default=1)
    paid_at = models.DateField("Дата оплаты", default=timezone.localdate)
    comment = models.CharField("Комментарий", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-paid_at", "-id"]

    def __str__(self):
        return f"{self.subscription.organization} — {self.amount} ₽ ({self.paid_at})"

    def save(self, *args, **kwargs):
        extend = self.pk is None  # продлеваем только при создании
        super().save(*args, **kwargs)
        if extend and self.months:
            sub = self.subscription
            base = max(sub.paid_until, timezone.localdate())
            sub.paid_until = add_months(base, self.months)
            sub.save(update_fields=["paid_until"])

"""Смены персонала и расчёт дневной оплаты.

Смена — это рабочий день заведения. Состав смены ставит менеджер (роль «Склад»)
или админ; сам персонал себя в смену не отмечает, только смотрит. Каждого
ставят с типом смены («08:00–20:00») и ролью — от них зависит ставка.
Как считается выплата, задаёт схема оплаты (Scheme), см. shifts/services.py.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.tenancy import TenantModel

from users.models import User


#: Роли, которые ставятся в смену и получают ставку.
STAFF_ROLES = [
    User.Role.BAR,
    User.Role.WAITER,
    User.Role.COOK,
    User.Role.WAREHOUSE,
    User.Role.ADMIN,
]


def default_kpi_roles() -> list[str]:
    """Бариста и официант делят выручку, повар — нет (как у Монти)."""
    return [User.Role.BAR.value, User.Role.WAITER.value]


class Scheme(models.TextChoices):
    """Как считается выплата за смену."""

    # Ставка за день одна на всех + процент от выручки поровну. Так платит
    # хуму; уйдёт, когда хуму перейдёт на оплату за результат.
    EVEN = "even", "Ставка + % от выручки поровну"
    # Ставка по типу смены и роли + бонусы за результат (схема Монти).
    RESULT = "result", "Ставка + бонусы за результат"


class ShiftSettings(TenantModel):
    """Параметры расчёта зарплаты — одна запись (singleton). Правится в админке."""

    scheme = models.CharField(
        "Схема оплаты", max_length=8, choices=Scheme.choices, default=Scheme.EVEN
    )
    senior_bonus = models.DecimalField(
        "Надбавка старшему", max_digits=10, decimal_places=2, default=Decimal("0"),
        help_text="Сверх ставки тому, кто отмечен старшим смены. Только в оплате за результат.",
    )
    kpi_roles = models.JSONField(
        "Роли в бонусе за КПД", default=default_kpi_roles, blank=True,
        help_text="Кто делит выручку и получает бонус за КПД. Остальные — только ставку.",
    )
    kpi_grid = models.JSONField(
        "Сетка бонуса за КПД", default=list, blank=True,
        help_text=(
            "Ступени [{\"from\": \"2500\", \"bonus\": \"500\"}, …]: от скольких "
            "₽ личной выручки в час (включительно) — какая надбавка за смену."
        ),
    )
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
        constraints = [
            # одна запись настроек на заведение
            models.UniqueConstraint(
                fields=["organization"], name="unique_shiftsettings_per_org"
            ),
        ]

    def __str__(self):
        return "Настройки смен и оплаты"

    @classmethod
    def load(cls):
        """Настройки текущего заведения; заводятся при первом обращении.

        Раньше здесь была одна запись на базу (pk=1). В общей установке
        заведений много, и у каждого свои настройки — поэтому запись
        ищется по заведению, а не по единице.
        """
        from core.tenancy import current_organization

        obj, _ = cls.objects.get_or_create(organization=current_organization())
        return obj


class ShiftType(TenantModel):
    """Вид смены: «Полная 08:00–20:00», «Короткая 14:00–20:00».

    Нужно время, а не только число часов: по нему видно, кто с кем
    работал одновременно, — от этого зависит, как делится выручка часа.
    """

    name = models.CharField("Название", max_length=40)
    starts_at = models.TimeField("Начало")
    ends_at = models.TimeField(
        "Конец", help_text="Если раньше начала — смена заканчивается на следующий день."
    )
    sort_order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Тип смены"
        verbose_name_plural = "Типы смен"
        ordering = ["sort_order", "starts_at", "id"]

    def __str__(self):
        return f"{self.name} {self.starts_at:%H:%M}–{self.ends_at:%H:%M}"

    @property
    def hours(self) -> Decimal:
        return hours_between(self.starts_at, self.ends_at)

    def clean(self):
        # равные отметки считались бы сутками работы — это опечатка
        from django.core.exceptions import ValidationError

        if self.starts_at is not None and self.starts_at == self.ends_at:
            raise ValidationError("Начало и конец смены совпадают")


def hours_between(start, end) -> Decimal:
    """Часы между двумя отметками времени; конец раньше начала — через полночь."""
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if minutes <= 0:
        minutes += 24 * 60
    return (Decimal(minutes) / 60).quantize(Decimal("0.01"))


class ShiftRate(TenantModel):
    """Ставка за смену: у каждой пары «тип смены × роль» своя."""

    shift_type = models.ForeignKey(
        ShiftType, on_delete=models.CASCADE, related_name="rates", verbose_name="Тип смены"
    )
    role = models.CharField("Роль", max_length=16, choices=User.Role.choices)
    rate = models.DecimalField("Ставка", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Ставка за смену"
        verbose_name_plural = "Ставки за смену"
        constraints = [
            models.UniqueConstraint(
                fields=["shift_type", "role"], name="uniq_rate_per_type_role"
            )
        ]

    def __str__(self):
        return f"{self.shift_type.name} · {self.get_role_display()}: {self.rate}"


class Shift(TenantModel):
    """Рабочий день. Параметры оплаты фиксируются на момент открытия смены,
    чтобы правка ставки в админке не переписывала историю выплат."""

    date = models.DateField("Дата")
    scheme = models.CharField(
        "Схема оплаты", max_length=8, choices=Scheme.choices, default=Scheme.EVEN
    )
    senior_bonus = models.DecimalField(
        "Надбавка старшему", max_digits=10, decimal_places=2, default=Decimal("0")
    )
    kpi_grid = models.JSONField("Сетка бонуса за КПД", default=list, blank=True)
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
        # Уникально внутри заведения, а не на всю базу:
        # у каждого кафе своё, и совпадения — норма.
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "date"], name="uniq_shift_per_org"
            ),
        ]

    def __str__(self):
        return f"Смена {self.date:%d.%m.%Y}"


class ShiftMember(TenantModel):
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
    # Тип смены и время — снимком, как и ставка: правка типа или ставки
    # не должна переписывать прошлые выплаты.
    shift_type = models.ForeignKey(
        ShiftType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="members", verbose_name="Тип смены",
    )
    shift_type_name = models.CharField("Название типа", max_length=40, blank=True)
    starts_at = models.TimeField("Пришёл", null=True, blank=True)
    ends_at = models.TimeField("Ушёл", null=True, blank=True)
    rate = models.DecimalField(
        "Ставка", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Ставка по типу смены и роли. Пусто — общая ставка смены.",
    )
    in_kpi = models.BooleanField("Участвует в КПД", default=False)
    is_senior = models.BooleanField("Старший смены", default=False)
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

    @property
    def hours(self) -> Decimal | None:
        if self.starts_at is None or self.ends_at is None:
            return None
        return hours_between(self.starts_at, self.ends_at)


class FocusItem(TenantModel):
    """Фокусная позиция: за каждую проданную штуку — надбавка исполнителю.

    Надбавку меняют хоть каждый день, а продажа засчитывается по сумме,
    действовавшей в момент продажи. Поэтому запись живёт отрезком
    [starts_at, ends_at): правка суммы посреди отрезка закрывает эту
    запись «сейчас» и открывает новую — прошлые продажи не пересчитываются.
    """

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="focus_items",
        verbose_name="Товар",
    )
    # Пусто — любой объём товара; указан — только этот («Латте 0,4»).
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.PROTECT, null=True, blank=True,
        related_name="focus_items", verbose_name="Вариант",
    )
    bonus = models.DecimalField("Надбавка за штуку", max_digits=10, decimal_places=2)
    starts_at = models.DateTimeField("Действует с")
    ends_at = models.DateTimeField("Действует до", help_text="Не включительно.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Завёл",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Фокусная позиция"
        verbose_name_plural = "Фокусные позиции"
        ordering = ["starts_at", "id"]

    def __str__(self):
        name = self.variant.full_name if self.variant_id else self.product.name
        return f"{name}: +{self.bonus}"

    @property
    def title(self) -> str:
        return self.variant.full_name if self.variant_id else self.product.name

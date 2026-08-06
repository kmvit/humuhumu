from decimal import Decimal

from django.conf import settings
from django.db import models, transaction


class StockCategory(models.Model):
    """Назначение складской позиции: для блюд, для напитков, для уборки, для сервиса и т.д."""

    name = models.CharField("Название", max_length=100, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Категория склада"
        verbose_name_plural = "Категории склада"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class StockItem(models.Model):
    """Складская номенклатура: сырьё/расходник с единицей измерения и текущим остатком."""

    class Unit(models.TextChoices):
        GRAM = "g", "г"
        MILLILITER = "ml", "мл"
        PIECE = "pcs", "шт"

    category = models.ForeignKey(
        StockCategory,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name="Категория",
    )
    name = models.CharField("Название", max_length=200)
    unit = models.CharField(
        "Единица", max_length=4, choices=Unit.choices, default=Unit.PIECE
    )
    # Текущий остаток в базовой единице. Кэш суммы движений (StockMovement),
    # изменяется только через apply_movement — под блокировкой строки.
    quantity = models.DecimalField(
        "Остаток", max_digits=12, decimal_places=3, default=Decimal("0")
    )
    min_quantity = models.DecimalField(
        "Порог «заканчивается»",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Если остаток опустится до этого значения — позиция подсветится",
    )
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Складская позиция"
        verbose_name_plural = "Складские позиции"
        ordering = ["category__sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"], name="uniq_stockitem_per_category"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"

    @property
    def is_low(self) -> bool:
        return self.min_quantity is not None and self.quantity <= self.min_quantity

    @transaction.atomic
    def apply_movement(self, delta, kind, *, user=None, receipt=None, comment=""):
        """Изменить остаток на delta (со знаком) и записать движение в журнал.

        Строка блокируется select_for_update, чтобы параллельные приходы/корректировки
        не затирали остаток друг друга.
        """
        delta = Decimal(delta)
        locked = StockItem.objects.select_for_update().get(pk=self.pk)
        locked.quantity = (locked.quantity or Decimal("0")) + delta
        locked.save(update_fields=["quantity"])
        self.quantity = locked.quantity
        StockMovement.objects.create(
            item=self,
            delta=delta,
            kind=kind,
            receipt=receipt,
            created_by=user,
            comment=comment,
        )
        return locked.quantity


class Receipt(models.Model):
    """Приход — документ поступления товаров от поставщика."""

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipts",
        verbose_name="Оприходовал",
    )
    supplier = models.CharField("Поставщик", max_length=200, blank=True)
    comment = models.CharField("Комментарий", max_length=300, blank=True)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Приход"
        verbose_name_plural = "Приходы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Приход №{self.pk}"

    @property
    def total_cost(self):
        """Сумма прихода по позициям, у которых указана цена (иначе не учитывается)."""
        return sum(
            (i.subtotal for i in self.items.all() if i.subtotal is not None),
            start=Decimal("0"),
        )


class ReceiptItem(models.Model):
    """Позиция прихода: номенклатура + количество (+ цена закупки опционально)."""

    receipt = models.ForeignKey(
        Receipt, on_delete=models.CASCADE, related_name="items", verbose_name="Приход"
    )
    item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="receipt_items",
        verbose_name="Позиция",
    )
    quantity = models.DecimalField("Количество", max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(
        "Цена за единицу, ₽",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Позиция прихода"
        verbose_name_plural = "Позиции прихода"

    @property
    def subtotal(self):
        if self.unit_cost is None:
            return None
        return self.unit_cost * self.quantity

    def __str__(self):
        return f"{self.item} × {self.quantity}"


class ReceiptScan(models.Model):
    """Фото чека и результат его распознавания — черновик будущего прихода.

    Живёт отдельно от Receipt: распознавание и сопоставление номенклатуры
    ошибаются, поэтому остатки НЕ меняются, пока кладовщик не подтвердит черновик.
    На подтверждении собирается payload и создаётся Receipt штатным путём.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Распознаётся"
        PARSED = "parsed", "Распознан"
        FAILED = "failed", "Ошибка"
        CONFIRMED = "confirmed", "Оприходован"

    image = models.ImageField("Фото чека", upload_to="receipt_scans/%Y/%m/")
    status = models.CharField(
        "Статус", max_length=12, choices=Status.choices, default=Status.PENDING
    )
    # Результат распознавания: {supplier, date, total, lines: [...]}.
    # Каждая строка — сырые данные из чека + предложенное сопоставление к StockItem.
    parsed = models.JSONField("Распознанное", null=True, blank=True)
    error = models.CharField("Ошибка", max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipt_scans",
        verbose_name="Загрузил",
    )
    receipt = models.OneToOneField(
        Receipt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan",
        verbose_name="Приход",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Скан чека"
        verbose_name_plural = "Сканы чеков"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Скан чека №{self.pk} ({self.get_status_display()})"


class StockMovement(models.Model):
    """Журнал движений остатка: приход и корректировка/инвентаризация."""

    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Приход"
        ADJUST = "adjust", "Корректировка"

    item = models.ForeignKey(
        StockItem,
        on_delete=models.CASCADE,
        related_name="movements",
        verbose_name="Позиция",
    )
    delta = models.DecimalField("Изменение", max_digits=12, decimal_places=3)
    kind = models.CharField("Тип", max_length=12, choices=Kind.choices)
    receipt = models.ForeignKey(
        Receipt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movements",
        verbose_name="Приход",
    )
    comment = models.CharField("Комментарий", max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кто",
    )
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Движение остатка"
        verbose_name_plural = "Движения остатка"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.item} {self.delta:+}"

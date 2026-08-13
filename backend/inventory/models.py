from decimal import Decimal

from django.conf import settings
from django.db import models, transaction


def normalize_name(text: str) -> str:
    """Свести название к виду, по которому сравниваем товары (чеки, алиасы)."""
    return " ".join((text or "").lower().replace("ё", "е").split())


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
    """Товар склада: «Креветки», «Кола», «Молоко» — с единицей и одним остатком.

    Марки и фасовки («мелкие 70/90», «Pepsi 1 л») в остатках не разделяются:
    500 г мелких + 500 г крупных = 1000 г креветок. Названия, под которыми товар
    покупают и пишут в чеках, живут отдельно — в StockItemAlias.
    """

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
        help_text="Если остаток опустится до этого значения — товар попадёт в закуп",
    )
    target_quantity = models.DecimalField(
        "Сколько держать",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="До какого остатка закупаем. Пусто — берём два порога",
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Товар склада"
        verbose_name_plural = "Товары склада"
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

    @property
    def purchase_target(self):
        """До какого остатка пополняем. Без явной цели — два порога."""
        if self.target_quantity is not None:
            return self.target_quantity
        if self.min_quantity is not None:
            return self.min_quantity * 2
        return None

    @property
    def shortage(self) -> Decimal:
        """Сколько не хватает до целевого остатка (0 — хватает)."""
        target = self.purchase_target
        if target is None:
            return Decimal("0")
        return max(Decimal("0"), Decimal(target) - self.quantity)

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


class StockItemAlias(models.Model):
    """Вариант товара — как его покупают и пишут в чеках.

    «Креветки» покупают как «КРЕВЕТКА В/М 16/20 VICI» и «креветка мелкая», «Колу» —
    как «Добрый Кола 0,5» и «Pepsi 1 л». Остаток у товара один, а эти названия
    нужны, чтобы сопоставлять строки чеков. Заполняется и само: подтвердили приход
    по фото — название из чека запомнилось за товаром.
    """

    item = models.ForeignKey(
        StockItem,
        on_delete=models.CASCADE,
        related_name="aliases",
        verbose_name="Товар",
    )
    name = models.CharField("Название в чеке", max_length=200)
    norm = models.CharField("Нормализованное", max_length=200, unique=True)
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Вариант / название в чеке"
        verbose_name_plural = "Варианты и названия в чеках"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.norm = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} → {self.item}"


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
    """Журнал движений остатка: приход, корректировка, продажа блюда."""

    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Приход"
        ADJUST = "adjust", "Корректировка"
        SALE = "sale", "Списание по тех карте"
        RETURN = "return", "Возврат отменённого"

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


class RecipeItem(models.Model):
    """Строка тех карты: сколько товара склада уходит на одну порцию блюда.

    Тех карта блюда — это все его строки. Количество в базовой единице товара
    (г/мл/шт): «Боул с креветкой» → «Креветки 80 г», «Рис 150 г».
    """

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="recipe",
        verbose_name="Блюдо",
    )
    item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="recipe_items",
        verbose_name="Товар склада",
    )
    quantity = models.DecimalField(
        "Расход на порцию", max_digits=12, decimal_places=3
    )
    comment = models.CharField("Примечание", max_length=200, blank=True)

    class Meta:
        verbose_name = "Строка тех карты"
        verbose_name_plural = "Тех карты блюд"
        ordering = ["product__name", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "item"], name="uniq_recipeitem_per_product"
            )
        ]

    def __str__(self):
        return f"{self.product}: {self.item} × {self.quantity}"


class PurchaseList(models.Model):
    """Закуп на конкретный день: что и сколько нужно купить.

    Строки появляются сами (товары, которых мало) и правятся руками. Список на
    день создаётся при первом открытии и потом дополняется новыми нехватками —
    уже купленные и вручную поправленные строки при этом не трогаются.
    """

    date = models.DateField("Дата", unique=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Закуп"
        verbose_name_plural = "Закуп"
        ordering = ["-date"]

    def __str__(self):
        return f"Закуп на {self.date:%d.%m.%Y}"


class PurchaseLine(models.Model):
    """Строка закупа: товар и сколько его взять."""

    purchase = models.ForeignKey(
        PurchaseList,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="Закуп",
    )
    item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="purchase_lines",
        verbose_name="Товар",
    )
    quantity = models.DecimalField("Количество", max_digits=12, decimal_places=3)
    is_auto = models.BooleanField(
        "Добавлено автоматически",
        default=True,
        help_text="Строку предложила программа; ручные строки не пересчитываются",
    )
    is_done = models.BooleanField("Куплено", default=False)
    comment = models.CharField("Комментарий", max_length=200, blank=True)

    class Meta:
        verbose_name = "Строка закупа"
        verbose_name_plural = "Строки закупа"
        ordering = ["item__category__sort_order", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase", "item"], name="uniq_purchaseline_per_list"
            )
        ]

    def __str__(self):
        return f"{self.item} × {self.quantity}"

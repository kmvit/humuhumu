from django.conf import settings
from django.db import models

from catalog.models import Category


class Table(models.Model):
    """Стол зала. Реестр столов; в заказе стол хранится строкой (name)."""

    name = models.CharField("Название / номер", max_length=32, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Стол"
        verbose_name_plural = "Столы"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Order(models.Model):
    """Заказ. Создаёт официант, готовит повар, оплату фиксирует кассир-бармен."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Ждёт официанта"
        OPEN = "open", "Открыт"
        AWAITING = "awaiting", "К оплате"  # отправлен на терминал, ждём результат
        PAID = "paid", "Закрыт"
        CANCELLED = "cancelled", "Отменён"

    class PayMethod(models.TextChoices):
        CASH = "cash", "Наличные"
        CARD = "card", "Карта"

    class StationStatus(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В процессе"
        READY = "ready", "Готов"

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
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_orders",
        verbose_name="Закрыл счёт",
    )
    table = models.CharField("Стол", max_length=32, blank=True)
    # Номер для выдачи: сквозной id уже четырёхзначный, его не выкрикнешь
    # в окно. Считается при создании и обнуляется каждый день.
    daily_number = models.PositiveIntegerField("Номер за день", null=True, blank=True)
    comment = models.CharField(
        "Комментарий официанта", max_length=300, blank=True,
        help_text="Пожелания к заказу: «без лука», «аллергия», «стол у окна» и т.п.",
    )
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.OPEN
    )
    # клиентская заявка без авторизации: имя и токен для отслеживания статуса
    customer_name = models.CharField("Имя клиента", max_length=120, blank=True)
    public_token = models.UUIDField(
        "Токен отслеживания", null=True, blank=True, unique=True, editable=False
    )
    pay_method = models.CharField(
        "Способ оплаты", max_length=16, choices=PayMethod.choices,
        blank=True, default=PayMethod.CASH,
    )
    total = models.DecimalField("Сумма", max_digits=12, decimal_places=2, default=0)
    # номер/признак фискального чека (заполняется при оплате через кассу-терминал)
    fiscal_receipt = models.CharField("Фискальный чек", max_length=64, blank=True, default="")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    # учёт времени по этапам техпроцесса (проставляются при смене статуса станции)
    food_started_at = models.DateTimeField("Кухня взяла", null=True, blank=True)
    food_ready_at = models.DateTimeField("Кухня готова", null=True, blank=True)
    drinks_started_at = models.DateTimeField("Бар взял", null=True, blank=True)
    drinks_ready_at = models.DateTimeField("Бар готов", null=True, blank=True)
    # официант отнёс готовое гостю (отдельно по кухне и бару)
    food_served_at = models.DateTimeField("Еда подана", null=True, blank=True)
    drinks_served_at = models.DateTimeField("Напитки поданы", null=True, blank=True)
    closed_at = models.DateTimeField("Закрыт", null=True, blank=True)

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

    @staticmethod
    def aggregate_status(statuses):
        """Агрегат статуса станции из статусов её позиций."""
        statuses = list(statuses)
        if not statuses:
            return None
        if all(s == Order.StationStatus.READY for s in statuses):
            return Order.StationStatus.READY
        if any(s != Order.StationStatus.NEW for s in statuses):
            return Order.StationStatus.IN_PROGRESS
        return Order.StationStatus.NEW

    def _station_items(self, station):
        return [i for i in self.items.all() if i.station == station]

    @property
    def has_food(self) -> bool:
        return bool(self._station_items(Category.Station.KITCHEN))

    @property
    def has_drinks(self) -> bool:
        return bool(self._station_items(Category.Station.BAR))

    @property
    def food_status(self):
        agg = self.aggregate_status(i.status for i in self._station_items(Category.Station.KITCHEN))
        return agg or self.StationStatus.NEW

    @property
    def drinks_status(self):
        agg = self.aggregate_status(i.status for i in self._station_items(Category.Station.BAR))
        return agg or self.StationStatus.NEW

    @property
    def is_ready(self) -> bool:
        """Заказ готов, когда все его позиции готовы."""
        items = list(self.items.all())
        return bool(items) and all(i.status == self.StationStatus.READY for i in items)


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
    status = models.CharField(
        "Статус", max_length=12,
        choices=Order.StationStatus.choices, default=Order.StationStatus.NEW,
    )
    # номер гостя для раздельного счёта; пусто = общий заказ
    guest = models.PositiveSmallIntegerField("Гость", null=True, blank=True)
    # Когда по тех карте блюда списали ингредиенты со склада. Проставляется
    # один раз — при первом переводе позиции в «готово» (см. inventory.services).
    stock_written_off_at = models.DateTimeField(
        "Склад списан", null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"
        # стабильный порядок по добавлению — иначе после правки количества
        # строка «переезжает» в выборке БД и позиции прыгают в интерфейсе
        ordering = ["id"]

    @property
    def subtotal(self):
        # None-safe: у незаполненной позиции (пустая форма-шаблон в админке)
        # unit_price ещё не задан — считаем сумму нулём, а не падаем.
        return (self.unit_price or 0) * (self.quantity or 0)

    @property
    def station(self):
        """Куда идёт позиция — определяется станцией её категории."""
        return self.product.category.station

    def __str__(self):
        return f"{self.product} × {self.quantity}"

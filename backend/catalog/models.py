import os
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models

from core.tenancy import TenantModel, tenant_upload_to
from PIL import Image


class Category(TenantModel):
    """Категория товаров (кофе, сэндвичи, боулы, мороженое и т.д.)."""

    class Station(models.TextChoices):
        KITCHEN = "kitchen", "Кухня"
        BAR = "bar", "Бар"

    name = models.CharField("Название", max_length=100)
    icon = models.ImageField(
        "Иконка", upload_to=tenant_upload_to("categories"), null=True, blank=True,
        max_length=200
    )
    station = models.CharField(
        "Станция", max_length=8, choices=Station.choices, default=Station.BAR,
        help_text="Куда уходят позиции этой категории: на кухню (еда) или в бар (напитки)",
    )
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Product(TenantModel):
    """Карточка меню: название, фото, описание.

    Цена, вес и стоп живут в вариантах (ProductVariant) — у «0,33» и «0,7»
    они свои. Продаётся всегда вариант, товар — то, что видит гость.
    """

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Категория",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    image = models.ImageField(
        "Изображение", upload_to=tenant_upload_to("products"), null=True, blank=True,
        max_length=200
    )
    # лёгкое превью (WebP ~256px) — генерируется автоматически, отдаётся в списке
    thumbnail = models.ImageField(
        "Превью", upload_to=tenant_upload_to("products/thumbs"), null=True, blank=True,
        editable=False, max_length=200
    )
    is_available = models.BooleanField("В наличии", default=True)
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)
    # Фото нарисовано нейросетью, а не снято. Меню показывает по нему
    # подпись «иллюстрация»: гость вправе знать, что перед ним не снимок
    # его порции, — иначе это недостоверная информация о товаре.
    image_is_generated = models.BooleanField("Фото сгенерировано", default=False)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["sort_order", "name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._orig_image = self.image.name if self.image else None

    def __str__(self):
        return self.name

    def make_thumbnail(self):
        """Собрать лёгкое превью из основного изображения (WebP, до 256px)."""
        if not self.image:
            return
        try:
            with self.image.open("rb") as f:
                img = Image.open(f)
                img.load()
        except Exception:
            return
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.thumbnail((256, 256))
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=70, method=6)
        base = os.path.splitext(os.path.basename(self.image.name))[0]
        # старое превью — производный файл, его не жалко: иначе при каждой
        # смене картинки в products/thumbs/ копится мусор
        if self.thumbnail:
            self.thumbnail.delete(save=False)
        self.thumbnail.save(f"{base}.webp", ContentFile(buf.getvalue()), save=False)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        current = self.image.name if self.image else None
        if self.image and (current != self._orig_image or not self.thumbnail):
            self.make_thumbnail()
            super().save(update_fields=["thumbnail"])
        elif not self.image and self.thumbnail:
            self.thumbnail.delete(save=False)
            super().save(update_fields=["thumbnail"])
        self._orig_image = current


class ProductVariant(TenantModel):
    """Вариант товара — то, что реально кладут в заказ: объём или размер.

    У товара всегда есть хотя бы один вариант; единственный вариант без
    метки интерфейс не показывает — карточка выглядит как обычное блюдо.
    Позиции заказов и тех карты ссылаются на вариант, поэтому цена, вес,
    стоп и состав у каждого объёма свои. Решение и сравнение с рынком —
    в памяти проекта (humu-variants-modifiers).
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
        verbose_name="Товар",
    )
    #: «0,33 л», «большая» — пусто у единственного варианта
    label = models.CharField("Вариант", max_length=40, blank=True)
    price = models.DecimalField("Цена, ₽", max_digits=10, decimal_places=2)
    weight_grams = models.PositiveIntegerField("Вес, г", null=True, blank=True)
    prep_minutes = models.PositiveIntegerField(
        "Время приготовления, мин", null=True, blank=True
    )
    # на стопе: вариант виден в меню, но временно нельзя заказать (кончилось)
    is_stopped = models.BooleanField("На стопе (временно)", default=False)
    # Снят с продажи насовсем. Не удаление: на проданный вариант ссылается
    # история заказов (PROTECT), и вместо ошибки владельцу он прячется.
    is_active = models.BooleanField("Продаётся", default=True)
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)

    class Meta:
        verbose_name = "Вариант товара"
        verbose_name_plural = "Варианты товаров"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "label"], name="uniq_variant_label_per_product"
            )
        ]

    @property
    def full_name(self) -> str:
        """Название для чека, кухни и отчётов: «Кис-кис 0,33 л»."""
        return f"{self.product.name} {self.label}".strip()

    def __str__(self):
        return self.full_name


class ProductLike(TenantModel):
    """Лайк блюда анонимным гостем. device — id устройства из localStorage."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="likes", verbose_name="Товар"
    )
    device = models.CharField("Устройство", max_length=64)
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Лайк"
        verbose_name_plural = "Лайки"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "device"], name="uniq_like_per_device"
            )
        ]

    def __str__(self):
        return f"♥ {self.product_id} · {self.device[:8]}"

class ModifierGroup(TenantModel):
    """Набор опций к блюду: «Молоко», «Добавки», «Сироп».

    Набор общий, а не свой у каждого блюда: «Молоко» заводится один раз и
    цепляется ко всем кофе сразу — иначе у кофейни с тринадцатью напитками
    его пришлось бы заполнять тринадцать раз и править потом тоже везде.
    Так же устроены modifier sets у Square и Toast.

    Опции — НЕ варианты. Вариант меняет напиток целиком (своя цена, свой
    состав) и выбирается ровно один; опции набираются поверх выбранного
    объёма и складываются. Если их смешать, «три объёма × три молока»
    превратятся в девять позиций меню — ровно та комбинаторика, из-за
    которой всё и затевалось.
    """

    name = models.CharField("Название", max_length=100)
    products = models.ManyToManyField(
        Product, related_name="modifier_groups", blank=True, verbose_name="Блюда"
    )
    # 1 — гость обязан выбрать (молоко в латте), 0 — по желанию (сироп)
    min_choices = models.PositiveSmallIntegerField("Минимум выбрать", default=0)
    # 1 — ровно одно из набора, больше — можно набрать несколько
    max_choices = models.PositiveSmallIntegerField("Максимум выбрать", default=1)
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Набор опций"
        verbose_name_plural = "Наборы опций"
        ordering = ["sort_order", "name"]

    @property
    def is_required(self) -> bool:
        return self.min_choices > 0

    def __str__(self):
        return self.name


class Modifier(TenantModel):
    """Опция внутри набора: «Овсяное», «+ шот эспрессо», «Без сиропа».

    Цена — надбавка к цене варианта (может быть нулевой и отрицательной).
    Что опция делает со складом, описывают ModifierEffect.
    """

    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="modifiers",
        verbose_name="Набор",
    )
    name = models.CharField("Название", max_length=100)
    price_delta = models.DecimalField(
        "Надбавка, ₽", max_digits=10, decimal_places=2, default=0
    )
    is_stopped = models.BooleanField("На стопе (временно)", default=False)
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)

    class Meta:
        verbose_name = "Опция"
        verbose_name_plural = "Опции"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ModifierEffect(TenantModel):
    """Что опция делает со складом. Считается поверх тех карты варианта.

    Три вида, и это не прихоть — дельтами количества обошлись бы только
    добавки:

    - ДОБАВИТЬ: «+ шот эспрессо» — плюс 18 г зерна, количество фиксированное;
    - УБРАТЬ: «без сиропа» — сколько сиропа не класть, знает тех карта
      проданного объёма, а не опция: в 0,33 его 20 мл, в 0,7 — 35;
    - ЗАМЕНИТЬ: «на овсяном» — коровье на овсяное В ТОМ ЖЕ количестве,
      какое стоит в карте проданного объёма.

    Поэтому «убрать» и «заменить» хранят ТОВАР (и чем заменить), а не
    количество: одна опция остаётся верной для всех объёмов и переживает
    правку рецепта. Конкуренты здесь считают дельтами и вынуждены заводить
    «овсяное для 0,33», «овсяное для 0,5» — см. память проекта.
    """

    class Kind(models.TextChoices):
        ADD = "add", "Добавить"
        REMOVE = "remove", "Убрать"
        SWAP = "swap", "Заменить"

    modifier = models.ForeignKey(
        Modifier,
        on_delete=models.CASCADE,
        related_name="effects",
        verbose_name="Опция",
    )
    kind = models.CharField("Действие", max_length=8, choices=Kind.choices)
    item = models.ForeignKey(
        "inventory.StockItem",
        on_delete=models.PROTECT,
        related_name="modifier_effects",
        verbose_name="Товар склада",
    )
    #: чем заменить — только для «заменить»
    replacement = models.ForeignKey(
        "inventory.StockItem",
        on_delete=models.PROTECT,
        related_name="modifier_replacements",
        null=True,
        blank=True,
        verbose_name="Заменить на",
    )
    #: сколько добавить — только для «добавить»; у остальных берётся из карты
    quantity = models.DecimalField(
        "Расход на порцию", max_digits=12, decimal_places=3, null=True, blank=True
    )

    class Meta:
        verbose_name = "Действие опции"
        verbose_name_plural = "Действия опций"
        ordering = ["id"]
        constraints = [
            # Схема сторожит смысл сама: «добавить» без количества и
            # «заменить» без замены — молчаливо ничего не спишут.
            models.CheckConstraint(
                name="modifier_effect_fields_match_kind",
                check=(
                    models.Q(kind="add", quantity__isnull=False, replacement__isnull=True)
                    | models.Q(kind="remove", quantity__isnull=True, replacement__isnull=True)
                    | models.Q(kind="swap", quantity__isnull=True, replacement__isnull=False)
                ),
            )
        ]

    def __str__(self):
        if self.kind == self.Kind.SWAP:
            return f"{self.item} → {self.replacement}"
        if self.kind == self.Kind.REMOVE:
            return f"без {self.item}"
        return f"+{self.quantity} {self.item}"



class DishwareSample(TenantModel):
    """Фото нашей посуды — образец, в котором нейросеть рисует блюдо.

    Отдельная сущность, а не поле у блюда: стакан 0,4 один на все тринадцать
    напитков, и загружать его в каждую карточку заново владелец не станет.
    Привязка к категории нужна только для подстановки по умолчанию: открыл
    генерацию у кофе — предложили стакан, а не тарелку.
    """

    name = models.CharField("Название", max_length=100)
    image = models.ImageField(
        "Фото", upload_to=tenant_upload_to("dishware"), max_length=200
    )
    #: «прозрачный стакан 0,4 с крышкой», «белая керамическая тарелка» —
    #: подсказка словами: по одному фото модель не всегда понимает материал.
    note = models.CharField("Что это", max_length=200, blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dishware",
        verbose_name="Категория по умолчанию",
    )
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)
    is_active = models.BooleanField("Используется", default=True)
    created_at = models.DateTimeField("Добавлено", auto_now_add=True)

    class Meta:
        verbose_name = "Образец посуды"
        verbose_name_plural = "Образцы посуды"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class MenuImageSettings(TenantModel):
    """Как должны выглядеть фото блюд этого заведения — один стиль на всех.

    Ценность фичи не в одной картинке, а в том, что сорок карточек меню
    выглядят как одна фотосессия. Поэтому стиль живёт у заведения, а не
    выбирается заново при каждой генерации.
    """

    class Style(models.TextChoices):
        STUDIO = "studio", "Студийное фото на светлом фоне"
        COUNTER = "counter", "На барной стойке"
        WOOD = "wood", "На деревянном столе"
        DARK = "dark", "Тёмный фон, контровой свет"

    style = models.CharField(
        "Стиль", max_length=10, choices=Style.choices, default=Style.STUDIO
    )
    #: дописка владельца: «наши фирменные зелёные салфетки», «вид сверху»
    extra_prompt = models.TextField("Дописка к запросу", blank=True)
    aspect_ratio = models.CharField(
        "Пропорции", max_length=8, default="1:1",
        help_text="1:1 для карточек меню, 4:3 — для широких",
    )
    background = models.ImageField(
        "Фото фона", upload_to=tenant_upload_to("dishware"), null=True, blank=True,
        max_length=200,
        help_text="Своя стойка или стол — попадёт на все фото как фон",
    )

    class Meta:
        verbose_name = "Настройки фото меню"
        verbose_name_plural = "Настройки фото меню"

    def __str__(self):
        return self.get_style_display()

    @classmethod
    def current(cls) -> "MenuImageSettings":
        """Настройки текущего заведения, заводя их при первом обращении."""
        row = cls.objects.first()
        if row is None:
            row = cls.objects.create()
        return row


class ImageQuota(TenantModel):
    """Сколько картинок заведение сгенерировало за месяц.

    Счётчик отдельный от самих генераций — по той же причине, что и у
    распознавания чеков (inventory.ScanQuota): неудачные картинки владелец
    удаляет, и уборка в галерее молча возвращала бы лимит.
    """

    #: Сколько фото в месяц входит в тариф. Меню на сорок позиций с выбором
    #: из двух вариантов заводится за один месяц и ещё остаётся запас.
    MONTHLY_LIMIT = 100

    month = models.DateField("Месяц", help_text="Первое число месяца")
    used = models.PositiveIntegerField("Израсходовано", default=0)
    extra = models.PositiveIntegerField(
        "Докуплено", default=0,
        help_text="Оплаченный пакет сверх тарифа — прибавляется к лимиту месяца",
    )

    @property
    def limit(self) -> int:
        return self.MONTHLY_LIMIT + self.extra

    class Meta:
        verbose_name = "Лимит генераций фото"
        verbose_name_plural = "Лимиты генераций фото"
        ordering = ["-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "month"], name="uniq_image_quota_per_month"
            ),
        ]

    def __str__(self):
        return f"{self.month:%m.%Y} — {self.used} из {self.limit}"


class ImageBatch(TenantModel):
    """Пачка генераций — одно нажатие владельца.

    Нужна ради категории целиком: «нарисуй фото всем блюдам без фото».
    Внутри — обычные ImageGeneration, по одной на картинку, каждая со своей
    задачей: одна упавшая не роняет остальные.
    """

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_batches",
        verbose_name="Категория",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_batches",
        verbose_name="Запустил",
    )
    created_at = models.DateTimeField("Запущена", auto_now_add=True)

    class Meta:
        verbose_name = "Пачка генераций"
        verbose_name_plural = "Пачки генераций"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Пачка №{self.pk} от {self.created_at:%d.%m.%Y}"


class ImageGeneration(TenantModel):
    """Одна картинка: запрос, результат и то, во что он нам обошёлся.

    Сгенерированное НЕ попадает в меню само — владелец смотрит и выбирает.
    Нейросеть иногда рисует не тот напиток, и молча подменить фото в меню —
    худшее, что можно сделать: гость увидит это раньше владельца.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Рисуется"
        READY = "ready", "Готово"
        FAILED = "failed", "Ошибка"

    batch = models.ForeignKey(
        ImageBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="generations",
        verbose_name="Пачка",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="image_generations",
        verbose_name="Блюдо",
    )
    prompt = models.TextField("Запрос")
    #: Посуда, приложенная к запросу образцом. Хранится связью, а не только
    #: словами в промпте: задача выполняется в воркере минутой позже и
    #: файлы образцов ей нужно взять заново.
    dishware = models.ManyToManyField(
        DishwareSample, blank=True, related_name="generations", verbose_name="Посуда"
    )
    model = models.CharField("Модель", max_length=100, blank=True)
    image = models.ImageField(
        "Результат", upload_to=tenant_upload_to("products/ai"), null=True, blank=True,
        max_length=200
    )
    status = models.CharField(
        "Статус", max_length=8, choices=Status.choices, default=Status.PENDING
    )
    error = models.CharField("Ошибка", max_length=500, blank=True)
    cost_usd = models.DecimalField(
        "Стоимость, $", max_digits=8, decimal_places=4, default=0,
        help_text="Сколько запрос стоил нам — приходит от OpenRouter",
    )
    #: когда эту картинку поставили в меню; у блюда такая всегда одна
    applied_at = models.DateTimeField("Поставлено в меню", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_generations",
        verbose_name="Запустил",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        verbose_name = "Генерация фото"
        verbose_name_plural = "Генерации фото"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.product_id} — {self.get_status_display()}"

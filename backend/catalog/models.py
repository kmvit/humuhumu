import os
from io import BytesIO

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

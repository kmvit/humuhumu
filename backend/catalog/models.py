import os
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import models
from PIL import Image


class Category(models.Model):
    """Категория товаров (кофе, сэндвичи, боулы, мороженое и т.д.)."""

    class Station(models.TextChoices):
        KITCHEN = "kitchen", "Кухня"
        BAR = "bar", "Бар"

    name = models.CharField("Название", max_length=100)
    icon = models.ImageField("Иконка", upload_to="categories/", null=True, blank=True)
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


class Product(models.Model):
    """Товар. Цена в рублях; при курсе 1 токен = 1 ₽ она же — цена в токенах."""

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Категория",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    image = models.ImageField("Изображение", upload_to="products/", null=True, blank=True)
    # лёгкое превью (WebP ~256px) — генерируется автоматически, отдаётся в списке
    thumbnail = models.ImageField(
        "Превью", upload_to="products/thumbs/", null=True, blank=True, editable=False
    )
    price = models.DecimalField("Цена, ₽", max_digits=10, decimal_places=2)
    weight_grams = models.PositiveIntegerField("Вес, г", null=True, blank=True)
    prep_minutes = models.PositiveIntegerField(
        "Время приготовления, мин", null=True, blank=True
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

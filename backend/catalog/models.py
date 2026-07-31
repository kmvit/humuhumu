from django.db import models


class Category(models.Model):
    """Категория товаров (кофе, сэндвичи, боулы, мороженое и т.д.)."""

    name = models.CharField("Название", max_length=100)
    icon = models.ImageField("Иконка", upload_to="categories/", null=True, blank=True)
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
    price = models.DecimalField("Цена, ₽", max_digits=10, decimal_places=2)
    weight_grams = models.PositiveIntegerField("Вес, г", null=True, blank=True)
    is_available = models.BooleanField("В наличии", default=True)
    sort_order = models.PositiveIntegerField("Порядок сортировки", default=0)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

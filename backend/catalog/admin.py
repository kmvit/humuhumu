from django.contrib import admin
from django.db.models import Count

from inventory.admin import RecipeItemInline

from .models import Category, Product, ProductLike, ProductVariant


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "sort_order", "is_active")
    list_editable = ("station", "sort_order", "is_active")
    list_filter = ("station",)


class ProductVariantInline(admin.TabularInline):
    """Варианты (объёмы) с ценами — цена товара живёт здесь."""

    model = ProductVariant
    extra = 0
    min_num = 1
    fields = (
        "label", "price", "weight_grams", "prep_minutes",
        "is_stopped", "is_active", "sort_order",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "prices", "likes_total", "is_available")
    list_filter = ("category", "is_available")
    list_editable = ("is_available",)
    search_fields = ("name", "description")
    inlines = [ProductVariantInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_likes=Count("likes", distinct=True))
            .prefetch_related("variants")
        )

    @admin.display(description="Цены")
    def prices(self, obj):
        return " / ".join(
            f"{v.label + ' ' if v.label else ''}{v.price:g} ₽"
            for v in obj.variants.all()
        ) or "—"

    @admin.display(description="Лайки", ordering="_likes")
    def likes_total(self, obj):
        return obj._likes


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    """Вариант отдельно: сюда прикреплена тех карта — состав объёма."""

    list_display = ("__str__", "price", "is_stopped", "is_active")
    list_filter = ("product__category", "is_stopped", "is_active")
    list_editable = ("price", "is_stopped", "is_active")
    search_fields = ("product__name", "label")
    # Тех карта — состав, по которому списывается склад.
    inlines = [RecipeItemInline]


@admin.register(ProductLike)
class ProductLikeAdmin(admin.ModelAdmin):
    list_display = ("product", "device", "created_at")
    search_fields = ("product__name", "device")
    list_filter = ("created_at",)

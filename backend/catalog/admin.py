from django.contrib import admin
from django.db.models import Count

from inventory.admin import RecipeItemInline

from .models import (
    Category,
    DishwareSample,
    ImageBatch,
    ImageGeneration,
    ImageQuota,
    MenuImageSettings,
    Modifier,
    ModifierEffect,
    ModifierGroup,
    Product,
    ProductLike,
    ProductVariant,
)


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

class ModifierEffectInline(admin.TabularInline):
    """Что опция делает со складом. «Добавить» требует количества,
    «заменить» — товара на замену, «убрать» не требует ничего."""

    model = ModifierEffect
    extra = 0
    fields = ("kind", "item", "replacement", "quantity")


@admin.register(Modifier)
class ModifierAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "price_delta", "is_stopped", "sort_order", "picks")
    list_filter = ("group", "is_stopped")
    list_editable = ("price_delta", "is_stopped", "sort_order")
    search_fields = ("name", "group__name")
    # Счётчик выбора — только показываем: его считает ночная задача по
    # заказам, и правка руками врала бы владельцу же (catalog/tasks.py).
    readonly_fields = ("picks",)
    inlines = [ModifierEffectInline]


class ModifierInline(admin.TabularInline):
    model = Modifier
    extra = 0
    fields = ("name", "price_delta", "is_stopped", "sort_order")
    show_change_link = True  # к действиям со складом — в карточку опции


@admin.register(ModifierGroup)
class ModifierGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "choices", "dishes", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name",)
    filter_horizontal = ("products",)
    inlines = [ModifierInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("products")

    @admin.display(description="Выбор")
    def choices(self, obj):
        if obj.min_choices and obj.max_choices == 1:
            return "ровно один"
        if obj.max_choices == 1:
            return "не больше одного"
        return f"от {obj.min_choices} до {obj.max_choices or '∞'}"

    @admin.display(description="Блюд")
    def dishes(self, obj):
        return obj.products.count()


@admin.register(ModifierEffect)
class ModifierEffectAdmin(admin.ModelAdmin):
    list_display = ("modifier", "kind", "item", "replacement", "quantity")
    list_filter = ("kind", "modifier__group")
    search_fields = ("modifier__name", "item__name")



@admin.register(DishwareSample)
class DishwareSampleAdmin(admin.ModelAdmin):
    """Образцы посуды — в них нейросеть рисует блюда."""

    list_display = ("name", "note", "category", "is_active", "sort_order")
    list_filter = ("category", "is_active")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "note")


@admin.register(MenuImageSettings)
class MenuImageSettingsAdmin(admin.ModelAdmin):
    list_display = ("style", "aspect_ratio")


@admin.register(ImageQuota)
class ImageQuotaAdmin(admin.ModelAdmin):
    """Лимит генераций. «Докуплено» — оплаченный пакет сверх тарифа."""

    list_display = ("month", "used", "limit", "extra")
    list_editable = ("extra",)


@admin.register(ImageBatch)
class ImageBatchAdmin(admin.ModelAdmin):
    list_display = ("__str__", "category", "created_by", "created_at")
    list_filter = ("category",)


@admin.register(ImageGeneration)
class ImageGenerationAdmin(admin.ModelAdmin):
    list_display = ("product", "status", "cost_usd", "applied_at", "created_at")
    list_filter = ("status", "product__category")
    search_fields = ("product__name",)
    readonly_fields = ("prompt", "model", "cost_usd", "error")

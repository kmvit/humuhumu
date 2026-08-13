from django.contrib import admin
from django.db.models import Count

from inventory.admin import RecipeItemInline

from .models import Category, Product, ProductLike


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "sort_order", "is_active")
    list_editable = ("station", "sort_order", "is_active")
    list_filter = ("station",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "likes_total", "prep_minutes", "weight_grams", "is_available", "is_stopped")
    list_filter = ("category", "is_available", "is_stopped")
    list_editable = ("price", "prep_minutes", "is_available", "is_stopped")
    search_fields = ("name", "description")
    # Тех карта блюда — состав, по которому списывается склад.
    inlines = [RecipeItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_likes=Count("likes"))

    @admin.display(description="Лайки", ordering="_likes")
    def likes_total(self, obj):
        return obj._likes


@admin.register(ProductLike)
class ProductLikeAdmin(admin.ModelAdmin):
    list_display = ("product", "device", "created_at")
    search_fields = ("product__name", "device")
    list_filter = ("created_at",)

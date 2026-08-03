from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "sort_order", "is_active")
    list_editable = ("station", "sort_order", "is_active")
    list_filter = ("station",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "prep_minutes", "weight_grams", "is_available")
    list_filter = ("category", "is_available")
    list_editable = ("price", "prep_minutes", "is_available")
    search_fields = ("name", "description")

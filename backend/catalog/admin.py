from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "weight_grams", "is_available")
    list_filter = ("category", "is_available")
    list_editable = ("price", "is_available")
    search_fields = ("name", "description")

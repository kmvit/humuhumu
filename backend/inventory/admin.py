from django.contrib import admin

from .models import Receipt, ReceiptItem, StockCategory, StockItem, StockMovement


@admin.register(StockCategory)
class StockCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "unit", "quantity", "min_quantity", "is_active")
    list_filter = ("category", "unit", "is_active")
    search_fields = ("name",)


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 0


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "supplier", "received_by", "total_cost")
    inlines = [ReceiptItemInline]
    readonly_fields = ("created_at",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("item", "delta", "kind", "receipt", "created_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("item__name",)

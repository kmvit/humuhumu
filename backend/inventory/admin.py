from django.contrib import admin

from .models import (
    PurchaseLine,
    PurchaseList,
    Receipt,
    ReceiptItem,
    ReceiptScan,
    RecipeItem,
    StockCategory,
    StockItem,
    StockItemAlias,
    StockMovement,
)


@admin.register(StockCategory)
class StockCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


class StockItemAliasInline(admin.TabularInline):
    model = StockItemAlias
    extra = 0
    fields = ("name",)


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = (
        "name", "category", "unit", "quantity",
        "min_quantity", "target_quantity", "is_active",
    )
    list_filter = ("category", "unit", "is_active")
    search_fields = ("name", "aliases__name")
    inlines = [StockItemAliasInline]


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 0
    fields = ("item", "quantity", "comment")


@admin.register(RecipeItem)
class RecipeItemAdmin(admin.ModelAdmin):
    list_display = ("product", "item", "quantity", "comment")
    list_filter = ("product__category",)
    search_fields = ("product__name", "item__name")


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0


@admin.register(PurchaseList)
class PurchaseListAdmin(admin.ModelAdmin):
    list_display = ("date", "created_at")
    inlines = [PurchaseLineInline]


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


@admin.register(ReceiptScan)
class ReceiptScanAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_by", "receipt", "created_at")
    list_filter = ("status",)
    readonly_fields = ("parsed", "error", "created_at", "updated_at")

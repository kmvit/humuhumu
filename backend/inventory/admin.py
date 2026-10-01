from django.contrib import admin

from .models import (
    PurchaseLine,
    PurchaseList,
    Receipt,
    ReceiptItem,
    ReceiptScan,
    RecipeItem,
    ScanQuota,
    StockCategory,
    StockItem,
    StockItemAlias,
    StockMovement,
    WriteOff,
    WriteOffItem,
)
from .services import delete_write_off


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
    list_display = ("variant", "item", "quantity", "comment")
    list_filter = ("variant__product__category",)
    search_fields = ("variant__product__name", "item__name")


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


@admin.register(ScanQuota)
class ScanQuotaAdmin(admin.ModelAdmin):
    """Расход распознаваний по месяцам. Здесь же продаётся пакет сверх тарифа:
    поле «Докуплено» прибавляется к лимиту месяца."""

    list_display = ("month", "used", "extra", "limit")
    list_editable = ("extra",)
    readonly_fields = ("month", "used")

    @admin.display(description="Лимит месяца")
    def limit(self, obj):
        return obj.limit


class WriteOffItemInline(admin.TabularInline):
    model = WriteOffItem
    extra = 0
    fields = ("item", "quantity", "unit_cost")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        # Остатки меняет только API: строка, добавленная здесь, не
        # записала бы движение, и склад разошёлся бы с журналом.
        return False


@admin.register(WriteOff)
class WriteOffAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "reason", "created_by", "created_at")
    search_fields = ("title", "reason")
    readonly_fields = ("title", "reason", "created_by", "created_at")
    inlines = [WriteOffItemInline]

    def has_add_permission(self, request):
        return False

    # Удаление из админки — тем же путём, что из интерфейса: с возвратом
    # товаров в остатки, а не голым DELETE.
    def delete_model(self, request, obj):
        delete_write_off(obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            delete_write_off(obj)

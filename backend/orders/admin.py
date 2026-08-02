from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("subtotal",)
    fields = ("product", "quantity", "unit_price", "status", "subtotal")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "table", "waiter", "status",
        "food_status", "drinks_status", "total", "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("table", "waiter__username", "client__username", "client__phone")
    readonly_fields = (
        "food_started_at", "food_ready_at",
        "drinks_started_at", "drinks_ready_at", "closed_at",
    )
    inlines = [OrderItemInline]
    date_hierarchy = "created_at"

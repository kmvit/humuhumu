from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("subtotal",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "table", "waiter", "status",
        "food_ready", "drinks_ready", "total", "created_at",
    )
    list_filter = ("status", "food_ready", "drinks_ready", "created_at")
    search_fields = ("table", "waiter__username", "client__username", "client__phone")
    inlines = [OrderItemInline]
    date_hierarchy = "created_at"

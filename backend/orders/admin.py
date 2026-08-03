from django.contrib import admin
from django.db.models import Sum

from .models import Order, OrderItem, Table


class StationFilter(admin.SimpleListFilter):
    """Фильтр заказов по наличию еды / напитков."""

    title = "Состав"
    parameter_name = "station"

    def lookups(self, request, model_admin):
        return [("kitchen", "Есть еда"), ("bar", "Есть напитки")]

    def queryset(self, request, queryset):
        if self.value() in ("kitchen", "bar"):
            return queryset.filter(
                items__product__category__station=self.value()
            ).distinct()
        return queryset


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    ordering = ("sort_order", "name")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("subtotal",)
    fields = ("product", "quantity", "unit_price", "guest", "status", "subtotal")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "table", "customer_name", "waiter", "status",
        "food_status", "drinks_status", "total", "created_at",
    )
    list_filter = ("status", StationFilter, "created_at")
    search_fields = ("table", "customer_name", "waiter__username", "client__username", "client__phone")
    readonly_fields = (
        "food_started_at", "food_ready_at",
        "drinks_started_at", "drinks_ready_at", "closed_at",
    )
    inlines = [OrderItemInline]
    date_hierarchy = "created_at"
    change_list_template = "admin/orders/order/change_list.html"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        try:
            cl = response.context_data["cl"]
        except (AttributeError, KeyError):
            return response
        # считаем по текущей выборке фильтров; re-query по pk, чтобы join не задвоил суммы
        ids = list(cl.queryset.values_list("pk", flat=True))
        agg = Order.objects.filter(pk__in=ids).aggregate(total=Sum("total"))
        response.context_data["order_total_sum"] = agg["total"] or 0
        response.context_data["order_count"] = len(ids)
        return response

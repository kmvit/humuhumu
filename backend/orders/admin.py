from django.contrib import admin
from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from .models import Order, OrderItem, Table

# выручка позиции = цена × количество (для агрегатов, subtotal — это property)
REVENUE = ExpressionWrapper(
    F("unit_price") * F("quantity"),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


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


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """Проданные позиции: фильтр по блюду/статусу/дате + сводка «сколько продано»."""

    list_display = (
        "product", "quantity", "unit_price", "subtotal_display",
        "order_table", "order_status", "order_created",
    )
    list_filter = ("product", "order__status", "order__created_at")
    search_fields = ("product__name", "order__table", "order__customer_name")
    date_hierarchy = "order__created_at"
    list_select_related = ("product", "order")
    change_list_template = "admin/orders/orderitem/change_list.html"

    @admin.display(description="Сумма")
    def subtotal_display(self, obj):
        return obj.subtotal

    @admin.display(description="Стол", ordering="order__table")
    def order_table(self, obj):
        return obj.order.table or "—"

    @admin.display(description="Статус заказа", ordering="order__status")
    def order_status(self, obj):
        return obj.order.get_status_display()

    @admin.display(description="Дата заказа", ordering="order__created_at")
    def order_created(self, obj):
        return obj.order.created_at

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        try:
            cl = response.context_data["cl"]
        except (AttributeError, KeyError):
            return response
        qs = cl.queryset
        agg = qs.aggregate(qty=Sum("quantity"), revenue=Sum(REVENUE))
        # разбивка по блюдам для текущей выборки фильтров
        breakdown = list(
            qs.values("product__name")
            .annotate(qty=Sum("quantity"), revenue=Sum(REVENUE))
            .order_by("-qty")
        )
        response.context_data["sold_qty"] = agg["qty"] or 0
        response.context_data["sold_revenue"] = agg["revenue"] or 0
        response.context_data["sold_breakdown"] = breakdown
        return response


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

from rest_framework import serializers

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    station = serializers.CharField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id", "product", "product_name", "station",
            "quantity", "unit_price", "subtotal",
        )
        read_only_fields = ("unit_price",)


class OrderSerializer(serializers.ModelSerializer):
    """Чтение заказа со списком позиций."""

    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    has_food = serializers.BooleanField(read_only=True)
    has_drinks = serializers.BooleanField(read_only=True)
    is_ready = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "client",
            "waiter",
            "closed_by",
            "table",
            "status",
            "status_display",
            "food_status",
            "drinks_status",
            "has_food",
            "has_drinks",
            "is_ready",
            "total",
            "items",
            "created_at",
        )


class OrderItemCreateSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class OrderCreateSerializer(serializers.Serializer):
    """Создание заказа официантом."""

    table = serializers.CharField(max_length=32, required=False, allow_blank=True)
    items = OrderItemCreateSerializer(many=True)

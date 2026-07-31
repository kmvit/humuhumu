from rest_framework import serializers

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ("id", "product", "product_name", "quantity", "unit_price", "subtotal")
        read_only_fields = ("unit_price",)


class OrderSerializer(serializers.ModelSerializer):
    """Чтение заказа со списком позиций."""

    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "client",
            "cashier",
            "status",
            "status_display",
            "pay_method",
            "total",
            "items",
            "created_at",
        )


class OrderItemCreateSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class OrderCreateSerializer(serializers.Serializer):
    """Создание заказа клиентом."""

    pay_method = serializers.ChoiceField(choices=Order.PayMethod.choices)
    items = OrderItemCreateSerializer(many=True)


class OrderStatusSerializer(serializers.Serializer):
    """Смена статуса заказа кассиром/админом."""

    status = serializers.ChoiceField(choices=Order.Status.choices)

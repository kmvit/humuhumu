from rest_framework import serializers

from .models import Order, OrderItem, Table


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ("id", "name", "sort_order", "is_active")


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    station = serializers.CharField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id", "product", "product_name", "station", "status", "guest",
            "quantity", "unit_price", "subtotal",
        )
        read_only_fields = ("unit_price",)


class OrderSerializer(serializers.ModelSerializer):
    """Чтение заказа со списком позиций."""

    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    pay_method_display = serializers.CharField(source="get_pay_method_display", read_only=True)
    has_food = serializers.BooleanField(read_only=True)
    has_drinks = serializers.BooleanField(read_only=True)
    is_ready = serializers.BooleanField(read_only=True)
    food_status = serializers.ReadOnlyField()
    drinks_status = serializers.ReadOnlyField()
    food_served = serializers.SerializerMethodField()
    drinks_served = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "client",
            "waiter",
            "closed_by",
            "table",
            "daily_number",
            "comment",
            "customer_name",
            "public_token",
            "status",
            "status_display",
            "pay_method",
            "pay_method_display",
            "fiscal_receipt",
            "food_status",
            "drinks_status",
            "food_served",
            "drinks_served",
            "has_food",
            "has_drinks",
            "is_ready",
            "total",
            "items",
            "created_at",
            "food_started_at",
            "food_ready_at",
            "drinks_started_at",
            "drinks_ready_at",
            "food_served_at",
            "drinks_served_at",
            "closed_at",
        )

    def get_food_served(self, obj):
        return obj.food_served_at is not None

    def get_drinks_served(self, obj):
        return obj.drinks_served_at is not None


class OrderItemCreateSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    guest = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class OrderCreateSerializer(serializers.Serializer):
    """Создание заказа официантом."""

    table = serializers.CharField(max_length=32, required=False, allow_blank=True)
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)
    items = OrderItemCreateSerializer(many=True)


class ClientOrderSerializer(serializers.Serializer):
    """Заявка от клиента без авторизации: имя + позиции (+ стол из QR-кода)."""

    customer_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)
    table = serializers.CharField(max_length=32, required=False, allow_blank=True)
    items = OrderItemCreateSerializer(many=True)

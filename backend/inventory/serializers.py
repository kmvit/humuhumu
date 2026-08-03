from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .models import Receipt, ReceiptItem, StockCategory, StockItem, StockMovement


class StockCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StockCategory
        fields = ("id", "name", "sort_order", "is_active")


class StockItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    unit_display = serializers.CharField(source="get_unit_display", read_only=True)
    is_low = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockItem
        fields = (
            "id", "category", "category_name", "name", "unit", "unit_display",
            "quantity", "min_quantity", "is_low", "is_active",
        )
        read_only_fields = ("quantity",)


class ReceiptItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    unit_display = serializers.CharField(source="item.get_unit_display", read_only=True)
    subtotal = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = ReceiptItem
        fields = (
            "id", "item", "item_name", "unit", "unit_display",
            "quantity", "unit_cost", "subtotal",
        )


class ReceiptSerializer(serializers.ModelSerializer):
    """Чтение прихода со списком позиций."""

    items = ReceiptItemSerializer(many=True, read_only=True)
    received_by_name = serializers.CharField(
        source="received_by.username", read_only=True, default=""
    )
    total_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = Receipt
        fields = (
            "id", "received_by", "received_by_name", "supplier", "comment",
            "total_cost", "items", "created_at",
        )


class ReceiptItemCreateSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=StockItem.objects.all())
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    unit_cost = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True, min_value=Decimal("0")
    )


class ReceiptCreateSerializer(serializers.Serializer):
    """Создание прихода: поставщик/комментарий + позиции. Увеличивает остатки."""

    supplier = serializers.CharField(max_length=200, required=False, allow_blank=True)
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)
    items = ReceiptItemCreateSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Добавьте хотя бы одну позицию.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        lines = validated_data.pop("items")
        receipt = Receipt.objects.create(
            received_by=user,
            supplier=validated_data.get("supplier", ""),
            comment=validated_data.get("comment", ""),
        )
        for line in lines:
            item = line["item"]
            qty = line["quantity"]
            ReceiptItem.objects.create(
                receipt=receipt,
                item=item,
                quantity=qty,
                unit_cost=line.get("unit_cost"),
            )
            item.apply_movement(
                qty, StockMovement.Kind.RECEIPT, user=user, receipt=receipt
            )
        return receipt

    def to_representation(self, instance):
        return ReceiptSerializer(instance, context=self.context).data


class AdjustSerializer(serializers.Serializer):
    """Корректировка/инвентаризация: выставить остаток позиции в новое значение."""

    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=Decimal("0")
    )
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)


class StockMovementSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True, default=""
    )

    class Meta:
        model = StockMovement
        fields = (
            "id", "delta", "kind", "kind_display", "comment",
            "created_by_name", "created_at",
        )

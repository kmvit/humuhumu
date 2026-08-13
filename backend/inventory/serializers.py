from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

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
    normalize_name,
)


class StockCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StockCategory
        fields = ("id", "name", "sort_order", "is_active")


class StockItemSerializer(serializers.ModelSerializer):
    """Товар склада: остаток, порог, цель пополнения и названия закупки."""

    category_name = serializers.CharField(source="category.name", read_only=True)
    unit_display = serializers.CharField(source="get_unit_display", read_only=True)
    is_low = serializers.BooleanField(read_only=True)
    shortage = serializers.DecimalField(
        max_digits=12, decimal_places=3, read_only=True
    )
    aliases = serializers.SerializerMethodField()

    class Meta:
        model = StockItem
        fields = (
            "id", "category", "category_name", "name", "unit", "unit_display",
            "quantity", "min_quantity", "target_quantity", "shortage",
            "is_low", "is_active", "aliases",
        )
        read_only_fields = ("quantity",)

    def get_aliases(self, obj) -> list[dict]:
        return [{"id": a.id, "name": a.name} for a in obj.aliases.all()]


class StockItemAliasSerializer(serializers.ModelSerializer):
    """Вариант товара — как его называют при закупке и в чеках."""

    class Meta:
        model = StockItemAlias
        fields = ("id", "item", "name")

    def validate_name(self, value):
        norm = normalize_name(value)
        qs = StockItemAlias.objects.filter(norm=norm)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        other = qs.select_related("item").first()
        if other is not None:
            raise serializers.ValidationError(
                f"Такое название уже закреплено за товаром «{other.item.name}»."
            )
        return value


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
    # Как позиция называлась в чеке — запоминаем как алиас варианта, чтобы в
    # следующий раз распознавание сопоставило строку точно.
    raw_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )


def _remember_alias(item, raw_name: str) -> None:
    """Запомнить название из чека за товаром (перепривязать, если уже было).

    Совпадение с собственным названием товара запоминать незачем — fuzzy-сопоставление
    найдёт его и так.
    """
    norm = normalize_name(raw_name)
    if not norm or norm == normalize_name(item.name):
        return
    alias = StockItemAlias.objects.filter(norm=norm).first()
    if alias is None:
        StockItemAlias.objects.create(item=item, name=raw_name.strip())
    elif alias.item_id != item.id:
        # Кладовщик поправил сопоставление — верим последнему решению.
        alias.item = item
        alias.name = raw_name.strip()
        alias.save(update_fields=["item", "name", "norm"])


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
            _remember_alias(item, line.get("raw_name", ""))
        return receipt

    def to_representation(self, instance):
        return ReceiptSerializer(instance, context=self.context).data


class AdjustSerializer(serializers.Serializer):
    """Корректировка/инвентаризация: выставить остаток позиции в новое значение."""

    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=Decimal("0")
    )
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)


class ReceiptScanSerializer(serializers.ModelSerializer):
    """Скан чека: загрузка фото (image) + чтение статуса и черновика (parsed)."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ReceiptScan
        fields = (
            "id", "image", "status", "status_display", "parsed", "error",
            "receipt", "created_at", "updated_at",
        )
        read_only_fields = (
            "status", "status_display", "parsed", "error", "receipt",
            "created_at", "updated_at",
        )


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


class RecipeItemSerializer(serializers.ModelSerializer):
    """Строка тех карты с подписями товара — для чтения и для сохранения карты."""

    item_name = serializers.CharField(source="item.name", read_only=True)
    unit_display = serializers.CharField(
        source="item.get_unit_display", read_only=True
    )

    class Meta:
        model = RecipeItem
        fields = ("id", "item", "item_name", "unit_display", "quantity", "comment")


class RecipeSerializer(serializers.Serializer):
    """Тех карта блюда целиком: блюдо + его состав + ориентировочная себестоимость."""

    product = serializers.IntegerField(read_only=True)
    product_name = serializers.CharField(read_only=True)
    category_name = serializers.CharField(read_only=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    lines = RecipeItemSerializer(many=True, read_only=True)
    # Считается по последним ценам закупки; строки без известной цены пропускаются.
    cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    cost_partial = serializers.BooleanField(read_only=True)


class RecipeLineWriteSerializer(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(queryset=StockItem.objects.all())
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=Decimal("0.001")
    )
    comment = serializers.CharField(max_length=200, required=False, allow_blank=True)


class RecipeWriteSerializer(serializers.Serializer):
    """Замена состава тех карты целиком — так проще, чем возиться со строками."""

    lines = RecipeLineWriteSerializer(many=True)

    def validate_lines(self, value):
        seen = set()
        for line in value:
            if line["item"].id in seen:
                raise serializers.ValidationError(
                    f"Товар «{line['item'].name}» указан дважды."
                )
            seen.add(line["item"].id)
        return value


class PurchaseLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit_display = serializers.CharField(
        source="item.get_unit_display", read_only=True
    )
    category_name = serializers.CharField(
        source="item.category.name", read_only=True
    )
    in_stock = serializers.DecimalField(
        source="item.quantity", max_digits=12, decimal_places=3, read_only=True
    )

    class Meta:
        model = PurchaseLine
        fields = (
            "id", "purchase", "item", "item_name", "unit_display", "category_name",
            "in_stock", "quantity", "is_auto", "is_done", "comment",
        )
        read_only_fields = ("is_auto",)


class PurchaseListSerializer(serializers.ModelSerializer):
    lines = PurchaseLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseList
        fields = ("id", "date", "lines", "created_at")

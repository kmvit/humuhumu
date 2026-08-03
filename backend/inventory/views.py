from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from users.permissions import IsWarehouseOrAdmin

from .models import Receipt, StockCategory, StockItem, StockMovement
from .serializers import (
    AdjustSerializer,
    ReceiptCreateSerializer,
    ReceiptSerializer,
    StockCategorySerializer,
    StockItemSerializer,
    StockMovementSerializer,
)


class StockCategoryViewSet(viewsets.ModelViewSet):
    """Категории склада (назначение). Заводит кладовщик/админ прямо в интерфейсе."""

    queryset = StockCategory.objects.all()
    serializer_class = StockCategorySerializer
    permission_classes = [IsWarehouseOrAdmin]


class StockItemViewSet(viewsets.ModelViewSet):
    """Складская номенклатура и текущие остатки."""

    queryset = StockItem.objects.select_related("category").all()
    serializer_class = StockItemSerializer
    permission_classes = [IsWarehouseOrAdmin]

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """Корректировка/инвентаризация: выставить остаток в новое значение."""
        item = self.get_object()
        ser = AdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target = ser.validated_data["quantity"]
        delta = target - item.quantity
        item.apply_movement(
            delta,
            StockMovement.Kind.ADJUST,
            user=request.user,
            comment=ser.validated_data.get("comment", ""),
        )
        return Response(StockItemSerializer(item).data)

    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        """История движений остатка по позиции."""
        item = self.get_object()
        qs = item.movements.select_related("created_by")[:100]
        return Response(StockMovementSerializer(qs, many=True).data)


class ReceiptViewSet(viewsets.ModelViewSet):
    """Приходы: список и оприходование (увеличивает остатки)."""

    queryset = Receipt.objects.prefetch_related("items__item").select_related(
        "received_by"
    )
    permission_classes = [IsWarehouseOrAdmin]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return ReceiptCreateSerializer
        return ReceiptSerializer

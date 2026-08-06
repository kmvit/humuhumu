from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from users.permissions import IsWarehouseOrAdmin

from .models import Receipt, ReceiptScan, StockCategory, StockItem, StockMovement
from .serializers import (
    AdjustSerializer,
    ReceiptCreateSerializer,
    ReceiptScanSerializer,
    ReceiptSerializer,
    StockCategorySerializer,
    StockItemSerializer,
    StockMovementSerializer,
)
from .tasks import process_receipt_scan


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


class ReceiptScanViewSet(viewsets.ModelViewSet):
    """Оприходование по фото чека: загрузка → распознавание → черновик → подтверждение.

    Остатки не меняются, пока кладовщик не подтвердит распознанный черновик через
    action `confirm` — там уже переиспользуется штатный ReceiptCreateSerializer.
    """

    queryset = ReceiptScan.objects.select_related("created_by", "receipt")
    serializer_class = ReceiptScanSerializer
    permission_classes = [IsWarehouseOrAdmin]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def perform_create(self, serializer):
        # Сохраняем фото и распознаём. По умолчанию синхронно в запросе — в
        # compose нет celery-воркера; при RECEIPT_SCAN_ASYNC=1 уходит в очередь.
        scan = serializer.save(created_by=self.request.user)
        if settings.RECEIPT_SCAN_ASYNC:
            process_receipt_scan.delay(scan.id)
        else:
            process_receipt_scan(scan.id)
            scan.refresh_from_db()

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Подтвердить черновик: создать приход и оприходовать позиции.

        Тело запроса — как у обычного прихода: {supplier, comment, items:[{item,
        quantity, unit_cost}]} (кладовщик уже поправил распознанное на фронте).
        """
        scan = self.get_object()
        if scan.status == ReceiptScan.Status.CONFIRMED:
            return Response(
                {"detail": "Чек уже оприходован."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = ReceiptCreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        receipt = ser.save()

        scan.receipt = receipt
        scan.status = ReceiptScan.Status.CONFIRMED
        scan.save(update_fields=["receipt", "status", "updated_at"])
        return Response(ReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)

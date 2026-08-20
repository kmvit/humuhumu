from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog.models import Product
from users.permissions import IsWarehouseOrAdmin

from .models import (
    PurchaseLine,
    PurchaseList,
    Receipt,
    ReceiptScan,
    RecipeItem,
    StockCategory,
    StockItem,
    StockItemAlias,
    StockMovement,
)
from .serializers import (
    AdjustSerializer,
    PurchaseLineSerializer,
    PurchaseListSerializer,
    ReceiptCreateSerializer,
    ReceiptScanSerializer,
    ReceiptSerializer,
    RecipeItemSerializer,
    RecipeSerializer,
    RecipeWriteSerializer,
    StockCategorySerializer,
    StockItemAliasSerializer,
    StockItemSerializer,
    StockMovementSerializer,
)
from .services import get_or_build_purchase, last_unit_costs
from .tasks import process_receipt_scan


class StockCategoryViewSet(viewsets.ModelViewSet):
    """Категории склада (назначение). Заводит кладовщик/админ прямо в интерфейсе."""

    queryset = StockCategory.objects.all()
    serializer_class = StockCategorySerializer
    permission_classes = [IsWarehouseOrAdmin]


class StockItemViewSet(viewsets.ModelViewSet):
    """Товары склада и их текущие остатки."""

    queryset = StockItem.objects.select_related("category").prefetch_related("aliases")
    serializer_class = StockItemSerializer
    permission_classes = [IsWarehouseOrAdmin]

    def destroy(self, request, *args, **kwargs):
        """Удалить товар.

        На товар ссылаются приходы, тех карты и закуп (on_delete=PROTECT) —
        физически удалить его вместе с историей нельзя. Поэтому: чистый товар
        (без движений, приходов, тех карт и строк закупа) удаляем совсем; товар
        с историей — прячем со склада (is_active=False), данные сохраняются.
        """
        item = self.get_object()
        has_history = (
            item.movements.exists()
            or item.receipt_items.exists()
            or item.recipe_items.exists()
            or item.purchase_lines.exists()
        )
        if has_history:
            item.is_active = False
            item.save(update_fields=["is_active"])
            return Response(
                {
                    "deactivated": True,
                    "detail": "У товара есть история или тех карты — он скрыт со "
                    "склада, данные сохранены.",
                }
            )
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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


class StockItemAliasViewSet(viewsets.ModelViewSet):
    """Варианты товара: как его называют при закупке и в чеках."""

    queryset = StockItemAlias.objects.select_related("item")
    serializer_class = StockItemAliasSerializer
    permission_classes = [IsWarehouseOrAdmin]


class RecipeViewSet(viewsets.ViewSet):
    """Тех карты блюд. Ключ — id блюда из меню, а не отдельная сущность карты."""

    permission_classes = [IsWarehouseOrAdmin]

    @staticmethod
    def _card(product, costs):
        """Собрать тех карту блюда: состав + себестоимость по последним закупкам."""
        lines = list(product.recipe.all())
        cost, partial = Decimal("0"), False
        for line in lines:
            unit_cost = costs.get(line.item_id)
            if unit_cost is None:
                partial = True
                continue
            cost += unit_cost * line.quantity
        return {
            "product": product.id,
            "product_name": product.name,
            "category_name": product.category.name if product.category_id else "",
            "price": product.price,
            # Строки отдаём объектами — сериализовать их будет RecipeSerializer.
            "lines": lines,
            "cost": cost.quantize(Decimal("0.01")),
            "cost_partial": partial or not lines,
        }

    def _queryset(self):
        return Product.objects.select_related("category").prefetch_related(
            "recipe__item"
        )

    def list(self, request):
        """Все блюда меню — и с картой, и пустые (их видно, что карты нет)."""
        products = list(self._queryset())
        costs = last_unit_costs(
            {line.item_id for p in products for line in p.recipe.all()}
        )
        cards = [self._card(p, costs) for p in products]
        return Response(RecipeSerializer(cards, many=True).data)

    def retrieve(self, request, pk=None):
        product = self._queryset().filter(pk=pk).first()
        if product is None:
            return Response(
                {"detail": "Блюдо не найдено"}, status=status.HTTP_404_NOT_FOUND
            )
        costs = last_unit_costs([line.item_id for line in product.recipe.all()])
        return Response(RecipeSerializer(self._card(product, costs)).data)

    def update(self, request, pk=None):
        """Заменить состав тех карты целиком: {lines: [{item, quantity, comment}]}."""
        product = self._queryset().filter(pk=pk).first()
        if product is None:
            return Response(
                {"detail": "Блюдо не найдено"}, status=status.HTTP_404_NOT_FOUND
            )
        ser = RecipeWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        with transaction.atomic():
            product.recipe.all().delete()
            RecipeItem.objects.bulk_create(
                RecipeItem(
                    product=product,
                    item=line["item"],
                    quantity=line["quantity"],
                    comment=line.get("comment", ""),
                )
                for line in ser.validated_data["lines"]
            )

        product = self._queryset().get(pk=product.pk)
        costs = last_unit_costs([line.item_id for line in product.recipe.all()])
        return Response(RecipeSerializer(self._card(product, costs)).data)


class PurchaseViewSet(viewsets.ReadOnlyModelViewSet):
    """Закуп по дням: список формируется сам и правится руками."""

    queryset = PurchaseList.objects.prefetch_related(
        "lines__item__category"
    )
    serializer_class = PurchaseListSerializer
    permission_classes = [IsWarehouseOrAdmin]

    @action(detail=False, methods=["get"])
    def day(self, request):
        """Закуп на дату (?date=YYYY-MM-DD, по умолчанию завтра).

        Список создаётся при первом обращении и дополняется товарами, которых
        стало мало с прошлого раза.
        """
        raw = request.query_params.get("date")
        try:
            day = date_cls.fromisoformat(raw) if raw else None
        except ValueError:
            return Response(
                {"detail": "Дата в формате ГГГГ-ММ-ДД"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if day is None:
            day = timezone.localdate() + timedelta(days=1)

        purchase = get_or_build_purchase(day)
        purchase = self.get_queryset().get(pk=purchase.pk)
        return Response(PurchaseListSerializer(purchase).data)


class PurchaseLineViewSet(viewsets.ModelViewSet):
    """Строки закупа: добавить своё, поправить количество, отметить купленным."""

    queryset = PurchaseLine.objects.select_related("item__category", "purchase")
    serializer_class = PurchaseLineSerializer
    permission_classes = [IsWarehouseOrAdmin]

    def perform_create(self, serializer):
        # Строку завёл человек — автоформирование её больше не трогает.
        serializer.save(is_auto=False)

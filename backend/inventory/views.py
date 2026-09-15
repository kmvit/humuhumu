from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog.models import ProductVariant
from core.plans import RequiresInventory
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
from .services import (
    consume_scan_quota,
    delete_receipt,
    get_or_build_purchase,
    last_unit_costs,
    scan_quota,
)
from .tasks import process_receipt_scan


class StockCategoryViewSet(viewsets.ModelViewSet):
    """Категории склада (назначение). Заводит кладовщик/админ прямо в интерфейсе."""

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return StockCategory.objects.annotate(items_count=Count("items"))
    serializer_class = StockCategorySerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]

    def destroy(self, request, *args, **kwargs):
        """Пустую категорию удаляем, с товарами — объясняем, что делать.

        На категории стоит PROTECT: без обработки кладовщик получил бы 500
        и не понял, почему. Прятать её вместо удаления нельзя — товары
        лежат внутри и исчезли бы из остатков вместе с ней.
        """
        category = self.get_object()
        count = category.items.count()
        if count:
            return Response(
                {
                    "detail": (
                        f"В категории {count} товаров — сначала перенесите их "
                        "в другую категорию или удалите."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class StockItemViewSet(viewsets.ModelViewSet):
    """Товары склада и их текущие остатки."""

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return StockItem.objects.select_related("category").prefetch_related("aliases")
    serializer_class = StockItemSerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]

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

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return Receipt.objects.prefetch_related("items__item").select_related(
            "received_by"
        )
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return ReceiptCreateSerializer
        return ReceiptSerializer

    def destroy(self, request, *args, **kwargs):
        """Удалить приход (ошиблись при оприходовании) и откатить остатки.

        Редактирование делается на фронте как «пересоздать»: заводится новый
        приход с исправленными позициями, а старый удаляется этим же методом.
        """
        delete_receipt(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReceiptScanViewSet(viewsets.ModelViewSet):
    """Оприходование по фото чека: загрузка → распознавание → черновик → подтверждение.

    Остатки не меняются, пока кладовщик не подтвердит распознанный черновик через
    action `confirm` — там уже переиспользуется штатный ReceiptCreateSerializer.
    """

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return ReceiptScan.objects.select_related("created_by", "receipt")
    serializer_class = ReceiptScanSerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def create(self, request, *args, **kwargs):
        """Перед загрузкой сверяемся с месячным лимитом распознаваний.

        Проверка здесь, а не в perform_create: отказывать надо до того, как
        принят файл и создана запись, иначе заведение увидит в списке скан,
        за который его же и отругали.
        """
        used, limit = scan_quota()
        if used >= limit:
            return Response(
                {
                    "detail": (
                        f"Распознано {used} чеков из {limit} за этот месяц — "
                        "лимит тарифа исчерпан. Приход можно завести вручную "
                        "или написать в «Падачу» за дополнительным пакетом."
                    )
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Сохраняем фото и распознаём. По умолчанию синхронно в запросе — в
        # compose нет celery-воркера; при RECEIPT_SCAN_ASYNC=1 уходит в очередь.
        scan = serializer.save(created_by=self.request.user)
        consume_scan_quota()
        if settings.RECEIPT_SCAN_ASYNC:
            process_receipt_scan.delay(scan.id)
        else:
            process_receipt_scan(scan.id)
            scan.refresh_from_db()

    @action(detail=False)
    def quota(self, request):
        """Сколько распознаваний осталось в этом месяце — для подсказки в UI."""
        used, limit = scan_quota()
        return Response({"used": used, "limit": limit, "left": max(0, limit - used)})

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

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return StockItemAlias.objects.select_related("item")
    serializer_class = StockItemAliasSerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]


class RecipeViewSet(viewsets.ViewSet):
    """Тех карты блюд. Ключ — id варианта блюда: у каждого объёма карта своя."""

    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]

    @staticmethod
    def _card(variant, costs):
        """Собрать тех карту варианта: состав + себестоимость по закупкам."""
        lines = list(variant.recipe.all())
        cost, partial = Decimal("0"), False
        for line in lines:
            unit_cost = costs.get(line.item_id)
            if unit_cost is None:
                partial = True
                continue
            cost += unit_cost * line.quantity
        product = variant.product
        return {
            "variant": variant.id,
            # id карточки меню — чтобы фронт мог сгруппировать объёмы
            # одного блюда и предложить скопировать состав соседа
            "product": product.id,
            "product_name": f"{product.name} {variant.label}".strip(),
            "category_name": product.category.name if product.category_id else "",
            "price": variant.price,
            # Строки отдаём объектами — сериализовать их будет RecipeSerializer.
            "lines": lines,
            "cost": cost.quantize(Decimal("0.01")),
            "cost_partial": partial or not lines,
        }

    def _queryset(self):
        return (
            ProductVariant.objects.filter(is_active=True)
            .select_related("product__category")
            .prefetch_related("recipe__item")
            .order_by("product__name", "sort_order", "id")
        )

    def list(self, request):
        """Все варианты меню — и с картой, и пустые (их видно, что карты нет)."""
        variants = list(self._queryset())
        costs = last_unit_costs(
            {line.item_id for v in variants for line in v.recipe.all()}
        )
        cards = [self._card(v, costs) for v in variants]
        return Response(RecipeSerializer(cards, many=True).data)

    def retrieve(self, request, pk=None):
        variant = self._queryset().filter(pk=pk).first()
        if variant is None:
            return Response(
                {"detail": "Блюдо не найдено"}, status=status.HTTP_404_NOT_FOUND
            )
        costs = last_unit_costs([line.item_id for line in variant.recipe.all()])
        return Response(RecipeSerializer(self._card(variant, costs)).data)

    def update(self, request, pk=None):
        """Заменить состав тех карты целиком: {lines: [{item, quantity, comment}]}."""
        variant = self._queryset().filter(pk=pk).first()
        if variant is None:
            return Response(
                {"detail": "Блюдо не найдено"}, status=status.HTTP_404_NOT_FOUND
            )
        ser = RecipeWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        with transaction.atomic():
            variant.recipe.all().delete()
            RecipeItem.objects.bulk_create(
                RecipeItem(
                    variant=variant,
                    item=line["item"],
                    quantity=line["quantity"],
                    comment=line.get("comment", ""),
                )
                for line in ser.validated_data["lines"]
            )

        variant = self._queryset().get(pk=variant.pk)
        costs = last_unit_costs([line.item_id for line in variant.recipe.all()])
        return Response(RecipeSerializer(self._card(variant, costs)).data)


class PurchaseViewSet(viewsets.ReadOnlyModelViewSet):
    """Закуп по дням: список формируется сам и правится руками."""

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return PurchaseList.objects.prefetch_related("lines__item__category")
    serializer_class = PurchaseListSerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]

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

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте и обслуживал бы
        # всех тенантов данными первого. См. core/tenancy.py.
        return PurchaseLine.objects.select_related("item__category", "purchase")
    serializer_class = PurchaseLineSerializer
    permission_classes = [IsWarehouseOrAdmin, RequiresInventory]

    def perform_create(self, serializer):
        # Строку завёл человек — автоформирование её больше не трогает.
        serializer.save(is_auto=False)

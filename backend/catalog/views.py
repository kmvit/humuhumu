import json

from django.db import transaction
from django.db.models import Count, Prefetch, ProtectedError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import ReadOnlyOrAdmin

from .models import Category, Product, ProductLike, ProductVariant
from .serializers import CategorySerializer, ProductSerializer


class ProtectedDeleteMixin:
    """Удаление того, на что ссылаются заказы, — с понятным ответом.

    В базе стоит PROTECT: товар из закрытого чека удалить нельзя, иначе
    рассыплется история и отчёты. Без обработки владелец получил бы 500 и
    не понял, что делать, — поэтому объясняем и подсказываем выход.
    """

    protected_message = "Удалить нельзя — запись уже используется."

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": self.protected_message},
                status=status.HTTP_409_CONFLICT,
            )


class CategoryViewSet(ProtectedDeleteMixin, viewsets.ModelViewSet):
    """Категории. Чтение — всем, запись — админу."""

    serializer_class = CategorySerializer
    permission_classes = [ReadOnlyOrAdmin]
    protected_message = (
        "В категории есть товары — сначала перенесите их в другую "
        "категорию или удалите."
    )

    def get_queryset(self):
        qs = Category.objects.all()
        # гостям и персоналу показываем только активные; владельцу — все,
        # иначе выключенную категорию нечем будет включить обратно
        if getattr(self.request.user, "role", None) != "admin":
            qs = qs.filter(is_active=True)
        return qs


class ProductViewSet(ProtectedDeleteMixin, viewsets.ModelViewSet):
    """Товары. Чтение — всем, запись — админу. Фильтр ?category=<id>."""

    serializer_class = ProductSerializer
    permission_classes = [ReadOnlyOrAdmin]
    protected_message = (
        "Товар есть в заказах — удалить нельзя, иначе рассыплется история. "
        "Снимите галочку «В меню», чтобы убрать его из продажи."
    )

    def get_permissions(self):
        # лайки и топ доступны анонимным гостям
        if self.action in ("like", "unlike", "top"):
            return [AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Product.objects.select_related("category").annotate(
            likes_count=Count("likes")
        )
        # клиентам и гостям показываем только доступные товары, а из
        # вариантов — только продающиеся: снятый размер гостю не предлагаем
        if getattr(self.request.user, "role", None) != "admin":
            qs = qs.filter(is_available=True).prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True),
                )
            )
        else:
            qs = qs.prefetch_related("variants")
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs

    # ——— варианты: цена/размеры правятся одной формой с товаром ———

    class _VariantRow(serializers.Serializer):
        id = serializers.IntegerField(required=False)
        label = serializers.CharField(
            max_length=40, required=False, allow_blank=True, default=""
        )
        price = serializers.DecimalField(max_digits=10, decimal_places=2)
        weight_grams = serializers.IntegerField(
            required=False, allow_null=True, min_value=0
        )
        prep_minutes = serializers.IntegerField(
            required=False, allow_null=True, min_value=0
        )
        is_stopped = serializers.BooleanField(required=False, default=False)

    def _sync_variants(self, product, raw):
        """Привести варианты товара к присланному списку.

        Список — состояние целиком, как в тех картах: с id — обновить,
        без id — создать, отсутствующие — убрать. Проданный вариант
        удалить нельзя (на нём история заказов) — он снимается с продажи.
        """
        if isinstance(raw, str):  # multipart с картинкой: список едет JSON-строкой
            try:
                raw = json.loads(raw)
            except ValueError:
                raise serializers.ValidationError({"variants": "Не разобрать JSON"})
        rows = self._VariantRow(data=raw, many=True)
        rows.is_valid(raise_exception=True)
        rows = rows.validated_data
        if not rows:
            raise serializers.ValidationError(
                {"variants": "У товара должна быть хотя бы одна цена."}
            )
        labels = [r["label"].strip() for r in rows]
        if len(set(labels)) != len(labels):
            raise serializers.ValidationError(
                {"variants": "Варианты не должны повторяться по названию."}
            )

        existing = {v.id: v for v in product.variants.all()}
        keep_ids = set()
        for order, row in enumerate(rows):
            variant = existing.get(row.get("id"))
            if variant is None:
                variant = ProductVariant(product=product)
            variant.label = row["label"].strip()
            variant.price = row["price"]
            variant.weight_grams = row.get("weight_grams")
            variant.prep_minutes = row.get("prep_minutes")
            variant.is_stopped = row.get("is_stopped", False)
            variant.is_active = True  # вернули в форму — значит, снова продаётся
            variant.sort_order = order
            variant.save()
            keep_ids.add(variant.id)

        for variant in product.variants.exclude(id__in=keep_ids):
            try:
                with transaction.atomic():
                    variant.delete()
            except ProtectedError:
                # вариант уже продавался — прячем вместо удаления
                variant.is_active = False
                variant.save(update_fields=["is_active"])

    def _respond_with(self, product, status_code=status.HTTP_200_OK):
        product = self.get_queryset().get(pk=product.pk)
        return Response(self.get_serializer(product).data, status=status_code)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if "variants" not in request.data:
            raise serializers.ValidationError({"variants": "Укажите цену товара."})
        with transaction.atomic():
            product = ser.save()
            self._sync_variants(product, request.data["variants"])
        return self._respond_with(product, status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        product = self.get_object()
        ser = self.get_serializer(
            product, data=request.data, partial=kwargs.pop("partial", False)
        )
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            product = ser.save()
            if "variants" in request.data:
                self._sync_variants(product, request.data["variants"])
        return self._respond_with(product)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @staticmethod
    def _device(request):
        return str(request.data.get("device", "")).strip()[:64]

    @action(detail=True, methods=["post"])
    def like(self, request, pk=None):
        """Гость лайкает блюдо (device — id устройства из localStorage)."""
        device = self._device(request)
        if not device:
            return Response({"detail": "Нет device"}, status=400)
        product = self.get_object()
        ProductLike.objects.get_or_create(product=product, device=device)
        return Response({"id": product.id, "likes": product.likes.count()})

    @action(detail=True, methods=["post"])
    def unlike(self, request, pk=None):
        """Гость снимает лайк."""
        device = self._device(request)
        if not device:
            return Response({"detail": "Нет device"}, status=400)
        product = self.get_object()
        ProductLike.objects.filter(product=product, device=device).delete()
        return Response({"id": product.id, "likes": product.likes.count()})

    @action(detail=False, methods=["get"])
    def top(self, request):
        """Топ блюд по лайкам (только с лайками)."""
        qs = self.get_queryset().filter(likes_count__gt=0).order_by("-likes_count")[:12]
        return Response(self.get_serializer(qs, many=True).data)

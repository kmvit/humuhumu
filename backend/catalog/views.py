import json

from django.db import transaction
from django.db.models import Count, Prefetch, ProtectedError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import ReadOnlyOrAdmin

from inventory.models import StockItem

from .models import (
    Category,
    Modifier,
    ModifierEffect,
    ModifierGroup,
    Product,
    ProductLike,
    ProductVariant,
)
from .serializers import (
    CategorySerializer,
    ModifierGroupSerializer,
    ProductSerializer,
)


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
        groups = Prefetch(
            "modifier_groups",
            queryset=ModifierGroup.objects.filter(is_active=True).prefetch_related(
                "modifiers"
            ),
        )
        if getattr(self.request.user, "role", None) != "admin":
            qs = qs.filter(is_available=True).prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True),
                ),
                groups,
            )
        else:
            qs = qs.prefetch_related("variants", groups)
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

    #: Поля, которыми старый бандл описывает цену прямо у товара.
    LEGACY_FIELDS = ("price", "weight_grams", "prep_minutes", "is_stopped")

    def _legacy_variants(self, request, product=None):
        """Собрать variants из полей старого бандла — или вернуть None.

        Планшет держит свой js в кэше, и после выката владелец какое-то
        время правит меню прежней формой: она шлёт price/weight_grams у
        товара и ничего не знает о вариантах. Без этого сохранение падало
        бы с 400 посреди рабочего дня. Снести вместе с остальной
        совместимостью — когда прежние бандлы вымоются из кэшей.
        """
        data = request.data
        if not any(f in data for f in self.LEGACY_FIELDS):
            return None

        current = list(product.variants.all()) if product else []
        # У многовариантного товара правим ПЕРВЫЙ объём: старая форма
        # другого и не показывала. Остальные не трогаем.
        rest = [
            {
                "id": v.id, "label": v.label, "price": v.price,
                "weight_grams": v.weight_grams, "prep_minutes": v.prep_minutes,
                "is_stopped": v.is_stopped,
            }
            for v in current[1:]
        ]
        head = current[0] if current else None

        def pick(field, fallback):
            return data[field] if field in data else fallback

        stopped = pick("is_stopped", head.is_stopped if head else False)
        first = {
            "label": head.label if head else "",
            "price": pick("price", head.price if head else None),
            "weight_grams": pick("weight_grams", head.weight_grams if head else None),
            "prep_minutes": pick("prep_minutes", head.prep_minutes if head else None),
            "is_stopped": stopped,
        }
        if head:
            first["id"] = head.id
        if first["price"] in (None, ""):
            return None  # цены нет ни в запросе, ни в базе — не наш случай

        # Старая форма знает один стоп на товар: ставя его, гасим все
        # объёмы разом — иначе владелец нажал бы «стоп», а блюдо осталось
        # бы в продаже в других объёмах.
        if "is_stopped" in data:
            for r in rest:
                r["is_stopped"] = stopped
        return [first] + rest

    def _respond_with(self, product, status_code=status.HTTP_200_OK):
        product = self.get_queryset().get(pk=product.pk)
        return Response(self.get_serializer(product).data, status=status_code)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        rows = request.data.get("variants") or self._legacy_variants(request)
        if not rows:
            raise serializers.ValidationError({"variants": "Укажите цену товара."})
        with transaction.atomic():
            product = ser.save()
            self._sync_variants(product, rows)
        return self._respond_with(product, status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        product = self.get_object()
        ser = self.get_serializer(
            product, data=request.data, partial=kwargs.pop("partial", False)
        )
        ser.is_valid(raise_exception=True)
        rows = request.data.get("variants") or self._legacy_variants(request, product)
        with transaction.atomic():
            product = ser.save()
            if rows:
                self._sync_variants(product, rows)
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

class ModifierGroupViewSet(ProtectedDeleteMixin, viewsets.ModelViewSet):
    """Наборы опций: «Молоко», «Добавки». Чтение — всем, запись — админу.

    Сами опции и их действия со складом правятся вложенным списком: набор
    без опций бессмыслен, а держать их отдельным экраном значило бы гонять
    владельца туда-сюда на каждую строку.
    """

    serializer_class = ModifierGroupSerializer
    permission_classes = [ReadOnlyOrAdmin]
    protected_message = "Набор используется в заказах — удалить нельзя."

    def get_queryset(self):
        return ModifierGroup.objects.prefetch_related(
            "products", "modifiers__effects"
        ).order_by("sort_order", "name")

    class _EffectRow(serializers.Serializer):
        kind = serializers.ChoiceField(choices=ModifierEffect.Kind.choices)
        item = serializers.PrimaryKeyRelatedField(queryset=StockItem.objects)
        replacement = serializers.PrimaryKeyRelatedField(
            queryset=StockItem.objects, required=False, allow_null=True
        )
        quantity = serializers.DecimalField(
            max_digits=12, decimal_places=3, required=False, allow_null=True
        )

        def validate(self, attrs):
            kind = attrs["kind"]
            if kind == ModifierEffect.Kind.ADD and not attrs.get("quantity"):
                raise serializers.ValidationError("Для «добавить» укажите количество")
            if kind == ModifierEffect.Kind.SWAP and not attrs.get("replacement"):
                raise serializers.ValidationError("Для «заменить» укажите, на что")
            # Лишнее гасим, иначе запись не пройдёт проверку схемы.
            if kind != ModifierEffect.Kind.ADD:
                attrs["quantity"] = None
            if kind != ModifierEffect.Kind.SWAP:
                attrs["replacement"] = None
            return attrs

    class _ModifierRow(serializers.Serializer):
        id = serializers.IntegerField(required=False)
        name = serializers.CharField(max_length=100)
        price_delta = serializers.DecimalField(
            max_digits=10, decimal_places=2, required=False, default=0
        )
        is_stopped = serializers.BooleanField(required=False, default=False)

    def _sync_modifiers(self, group, raw):
        """Привести опции набора к присланному списку — как варианты товара."""
        rows = self._ModifierRow(data=raw, many=True)
        rows.is_valid(raise_exception=True)

        existing = {m.id: m for m in group.modifiers.all()}
        keep = set()
        for order, (row, sent) in enumerate(zip(rows.validated_data, raw)):
            modifier = existing.get(row.get("id")) or Modifier(group=group)
            modifier.name = row["name"].strip()
            modifier.price_delta = row["price_delta"]
            modifier.is_stopped = row["is_stopped"]
            modifier.sort_order = order
            modifier.save()
            keep.add(modifier.id)
            if "effects" in sent:
                effects = self._EffectRow(data=sent["effects"], many=True)
                effects.is_valid(raise_exception=True)
                modifier.effects.all().delete()
                for e in effects.validated_data:
                    ModifierEffect.objects.create(modifier=modifier, **e)

        for modifier in group.modifiers.exclude(id__in=keep):
            try:
                with transaction.atomic():
                    modifier.delete()
            except ProtectedError:
                # опция уже продавалась — прячем, история чека дороже
                modifier.is_stopped = True
                modifier.save(update_fields=["is_stopped"])

    def _save(self, serializer, request, status_code):
        with transaction.atomic():
            group = serializer.save()
            if "modifiers" in request.data:
                self._sync_modifiers(group, request.data["modifiers"])
        group = self.get_queryset().get(pk=group.pk)
        return Response(self.get_serializer(group).data, status=status_code)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return self._save(ser, request, status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        ser = self.get_serializer(
            self.get_object(), data=request.data, partial=kwargs.pop("partial", False)
        )
        ser.is_valid(raise_exception=True)
        return self._save(ser, request, status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


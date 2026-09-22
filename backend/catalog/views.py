import json

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Prefetch, ProtectedError
from django.utils import timezone
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from users.permissions import IsAdminRole, ReadOnlyOrAdmin

from inventory.models import StockItem

from .image_ai import build_prompt
from .models import (
    Category,
    DishwareSample,
    ImageBatch,
    ImageGeneration,
    MenuImageSettings,
    Modifier,
    ModifierEffect,
    ModifierGroup,
    Product,
    ProductLike,
    ProductVariant,
)
from .serializers import (
    CategorySerializer,
    DishwareSampleSerializer,
    ImageBatchCreateSerializer,
    ImageBatchSerializer,
    ImageGenerationSerializer,
    MenuImageSettingsSerializer,
    ModifierGroupSerializer,
    ProductSerializer,
)
from .services import (
    consume_image_quota,
    image_quota,
    images_enabled,
    month_spend,
)
from .tasks import generate_product_image


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



class ImagesEnabled(BasePermission):
    """Генерация фото включена «Падачей» для этого заведения.

    Рубильник в подписке: кафе его не трогает, поэтому проверяем здесь, а
    не прячем только кнопку — выключенную фичу должно быть нельзя позвать
    и запросом.
    """

    message = "Генерация фото для этого заведения выключена."

    def has_permission(self, request, view):
        return images_enabled()


class DishwareSampleViewSet(viewsets.ModelViewSet):
    """Образцы посуды: в них нейросеть рисует блюда. Только владелец."""

    serializer_class = DishwareSampleSerializer
    permission_classes = [IsAdminRole, ImagesEnabled]

    def get_queryset(self):
        # get_queryset, а не queryset на классе: запрос с фильтром по
        # заведению вычислился бы один раз при импорте. См. core/tenancy.py.
        return DishwareSample.objects.select_related("category")


class MenuImageSettingsView(generics.RetrieveUpdateAPIView):
    """Стиль фото меню — один на заведение."""

    serializer_class = MenuImageSettingsSerializer
    permission_classes = [IsAdminRole, ImagesEnabled]

    def get_object(self):
        return MenuImageSettings.current()


def _default_dishware(product):
    """Чем сервировать, если владелец не выбрал посуду сам.

    Сначала посуда, закреплённая за категорией блюда (кофе — стакан),
    потом общая. Двух образцов достаточно: дальше модель начинает путаться,
    что из этого на столе, а входные картинки ещё и платные.
    """
    qs = DishwareSample.objects.filter(is_active=True)
    picked = list(qs.filter(category=product.category_id)[:2])
    if not picked:
        picked = list(qs.filter(category__isnull=True)[:2])
    return picked


def _apply_generation(generation):
    """Поставить сгенерированное фото в меню.

    Файл КОПИРУЕМ, а не ссылаемся на тот же: генерации владелец удаляет,
    разбирая галерею, и фото блюда не должно исчезнуть из меню вместе с
    черновиком.
    """
    product = generation.product
    generation.image.open("rb")
    try:
        data = generation.image.read()
    finally:
        generation.image.close()

    product.image.save(
        f"ai-{generation.product_id}-{generation.pk}.webp",
        ContentFile(data),
        save=False,
    )
    product.image_is_generated = True
    product.save()

    # «В меню» у блюда ровно одна картинка — снимаем метку с прежней.
    ImageGeneration.objects.filter(product=product).exclude(pk=generation.pk).update(
        applied_at=None
    )
    generation.applied_at = timezone.now()
    generation.save(update_fields=["applied_at", "updated_at"])


def _enqueue(generations):
    """Отправить генерации рисоваться.

    on_commit, а не сразу: воркер расторопнее транзакции и успел бы не
    найти запись, которую мы только что создали. Синхронный режим —
    для установки без воркера, и пачку там лучше не запускать: запрос
    провисит столько, сколько рисуются все картинки.
    """
    ids = [g.id for g in generations]

    def run():
        for generation_id in ids:
            if settings.IMAGE_GEN_ASYNC:
                generate_product_image.delay(generation_id)
            else:
                generate_product_image(generation_id)

    transaction.on_commit(run)


class ImageBatchViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Генерация фото блюд: запуск пачки и её прогресс."""

    #: Потолок на одно нажатие. Лимит месяца и так не даст разгуляться, но
    #: пачка на сотню задач заняла бы воркер на час и заодно распознавание
    #: чеков, которое живёт в той же очереди.
    MAX_PER_BATCH = 30

    serializer_class = ImageBatchSerializer
    # quota отдаём и при выключенной фиче: по нему фронт прячет кнопки.
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        return ImageBatch.objects.prefetch_related("generations__product")

    def create(self, request, *args, **kwargs):
        if not images_enabled():
            return Response(
                {"detail": ImagesEnabled.message},
                status=status.HTTP_403_FORBIDDEN,
            )
        ser = ImageBatchCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        products = list(data.get("products") or [])
        category = data.get("category")
        if not products and category is not None:
            qs = Product.objects.filter(category=category)
            if data["only_without_photo"]:
                qs = qs.filter(image="")
            products = list(qs.select_related("category").prefetch_related("variants"))
        if not products:
            return Response(
                {"detail": "Нечего рисовать: у всех блюд категории уже есть фото."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        count = len(products) * data["variants"]
        if count > self.MAX_PER_BATCH:
            return Response(
                {
                    "detail": (
                        f"За раз рисуем не больше {self.MAX_PER_BATCH} картинок, "
                        f"а тут {count}. Разбейте на несколько подходов."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        used, limit = image_quota()
        if used + count > limit:
            left = max(0, limit - used)
            return Response(
                {
                    "detail": (
                        f"В этом месяце осталось {left} генераций из {limit}, "
                        f"а в запросе {count}. Выберите меньше блюд или "
                        "напишите в «Падачу» за дополнительным пакетом."
                    )
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        site = MenuImageSettings.current()
        chosen = list(data.get("dishware") or [])
        extra = " ".join(
            part for part in (site.extra_prompt.strip(), data["extra"].strip()) if part
        )

        with transaction.atomic():
            batch = ImageBatch.objects.create(
                category=category, created_by=request.user
            )
            generations = []
            for product in products:
                dishware = chosen or _default_dishware(product)
                variants = list(product.variants.all())
                prompt = build_prompt(
                    product,
                    dishware,
                    style=site.style,
                    extra=extra,
                    variant_label=variants[0].label if variants else "",
                )
                for _ in range(data["variants"]):
                    generation = ImageGeneration.objects.create(
                        batch=batch,
                        product=product,
                        prompt=prompt,
                        created_by=request.user,
                    )
                    generation.dishware.set(dishware)
                    generations.append(generation)
            # Списываем до обращения к модели: запрос оплачен нами, чем бы
            # он ни кончился (так же устроено распознавание чеков).
            consume_image_quota(len(generations))
            _enqueue(generations)

        batch = self.get_queryset().get(pk=batch.pk)
        return Response(
            self.get_serializer(batch).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def apply_all(self, request, pk=None):
        """Поставить в меню по одному готовому фото на каждое блюдо пачки."""
        batch = self.get_object()
        applied = 0
        seen = set()
        for generation in batch.generations.select_related("product").order_by("id"):
            if generation.product_id in seen:
                continue
            if generation.status != ImageGeneration.Status.READY or not generation.image:
                continue
            _apply_generation(generation)
            seen.add(generation.product_id)
            applied += 1
        return Response({"applied": applied})

    @action(detail=False)
    def quota(self, request):
        """Остаток генераций и потраченные деньги — для подсказки в UI."""
        used, limit = image_quota()
        return Response(
            {
                "enabled": images_enabled(),
                "used": used,
                "limit": limit,
                "left": max(0, limit - used),
                "spent_usd": str(month_spend()),
            }
        )


class ImageGenerationViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Сгенерированные картинки: галерея черновиков. Фильтры ?product=, ?batch=."""

    serializer_class = ImageGenerationSerializer
    permission_classes = [IsAdminRole, ImagesEnabled]

    def get_queryset(self):
        qs = ImageGeneration.objects.select_related("product")
        product = self.request.query_params.get("product")
        if product:
            qs = qs.filter(product_id=product)
        batch = self.request.query_params.get("batch")
        if batch:
            qs = qs.filter(batch_id=batch)
        return qs

    def perform_destroy(self, instance):
        # Картинку черновика удаляем вместе с записью: на неё никто не
        # ссылается — в меню уехала копия.
        if instance.image:
            instance.image.delete(save=False)
        instance.delete()

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Поставить эту картинку фото блюда."""
        generation = self.get_object()
        if generation.status != ImageGeneration.Status.READY or not generation.image:
            return Response(
                {"detail": "Картинка ещё не готова."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _apply_generation(generation)
        return Response(self.get_serializer(generation).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Нарисовать ещё раз тем же запросом — новой попыткой.

        Новая запись, а не перезапуск прежней: неудачный кадр владелец
        сравнивает с новым, а списанную генерацию всё равно не вернуть.
        """
        source = self.get_object()
        used, limit = image_quota()
        if used >= limit:
            return Response(
                {"detail": f"Лимит месяца исчерпан: {used} из {limit}."},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        with transaction.atomic():
            generation = ImageGeneration.objects.create(
                batch=source.batch,
                product=source.product,
                prompt=source.prompt,
                created_by=request.user,
            )
            generation.dishware.set(source.dishware.all())
            consume_image_quota(1)
            _enqueue([generation])
        return Response(
            self.get_serializer(generation).data, status=status.HTTP_201_CREATED
        )

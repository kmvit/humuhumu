from rest_framework import serializers

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
    ProductVariant,
)


class RelativeImageField(serializers.ImageField):
    """Картинка, которую можно и загрузить, и прочитать.

    Отдаём относительный URL (/media/...), а не абсолютный, как DRF по
    умолчанию: заведение ходит через прокси, и абсолютный адрес с именем
    внутреннего хоста у гостя не откроется.
    """

    def to_representation(self, value):
        return value.url if value else None


class CategorySerializer(serializers.ModelSerializer):
    icon = RelativeImageField(required=False, allow_null=True)

    class Meta:
        model = Category
        fields = ("id", "name", "icon", "station", "sort_order", "is_active")


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = (
            "id", "label", "price", "weight_grams", "prep_minutes",
            "is_stopped", "is_active", "sort_order",
        )


class ModifierEffectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModifierEffect
        fields = ("id", "kind", "item", "replacement", "quantity")


class ModifierSerializer(serializers.ModelSerializer):
    """Опция. effects отдаём только владельцу — гостю их знать незачем."""

    effects = ModifierEffectSerializer(many=True, read_only=True)

    class Meta:
        model = Modifier
        fields = ("id", "name", "price_delta", "is_stopped", "sort_order", "effects")


class ModifierGroupSerializer(serializers.ModelSerializer):
    modifiers = ModifierSerializer(many=True, read_only=True)
    is_required = serializers.BooleanField(read_only=True)
    products = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Product.objects, required=False
    )

    class Meta:
        model = ModifierGroup
        fields = (
            "id", "name", "min_choices", "max_choices", "is_required",
            "sort_order", "is_active", "products", "modifiers",
        )


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    image = RelativeImageField(required=False, allow_null=True)
    thumbnail = serializers.SerializerMethodField()
    # Только чтение: флаг ставит применение генерации, а не клиент.
    image_is_generated = serializers.BooleanField(read_only=True)
    likes = serializers.SerializerMethodField()
    # Варианты приходят и уходят вместе с товаром: гостю нечего делать с
    # карточкой без цен, а админка правит их одной формой. Запись — через
    # вьюху (sync_variants): в multipart с картинкой вложенный список не
    # передать иначе как JSON-строкой.
    variants = ProductVariantSerializer(many=True, read_only=True)
    # Опции едут вместе с блюдом: гость выбирает их в той же карточке, и
    # отдельный запрос за ними только добавил бы мигание в меню.
    modifier_groups = ModifierGroupSerializer(many=True, read_only=True)

    # ——— совместимость со старым бандлом ———
    # PWA кэширует свой js, и после деплоя на планшете барриста какое-то
    # время живёт прежняя версия: она читает price/weight_grams у товара и
    # без них показала бы пустые цены. Отдаём их с первого варианта, пока
    # прежние бандлы не вымоются из кэшей. Снести вместе с приёмом заказа
    # по product в orders/services._resolve_variant.
    price = serializers.SerializerMethodField()
    weight_grams = serializers.SerializerMethodField()
    prep_minutes = serializers.SerializerMethodField()
    is_stopped = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "category",
            "category_name",
            "name",
            "description",
            "image",
            "thumbnail",
            "image_is_generated",
            "is_available",
            "sort_order",
            "variants",
            "modifier_groups",
            "likes",
            "price",
            "weight_grams",
            "prep_minutes",
            "is_stopped",
        )

    def create(self, validated_data):
        if "image" in validated_data:
            validated_data["image_is_generated"] = False
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Владелец загрузил своё фото или убрал картинку — подпись
        # «иллюстрация» с карточки уходит. Обратно флаг ставит только
        # применение сгенерированного фото.
        if "image" in validated_data:
            validated_data["image_is_generated"] = False
        return super().update(instance, validated_data)

    def _first(self, obj):
        # prefetch уже отдал варианты списком — не ходим в базу заново
        variants = list(obj.variants.all())
        return variants[0] if variants else None

    def get_price(self, obj):
        first = self._first(obj)
        return str(first.price) if first else "0.00"

    def get_weight_grams(self, obj):
        first = self._first(obj)
        return first.weight_grams if first else None

    def get_prep_minutes(self, obj):
        first = self._first(obj)
        return first.prep_minutes if first else None

    def get_is_stopped(self, obj):
        """Старый бандл знает один стоп на товар — считаем блюдо стопнутым,
        только когда встали ВСЕ объёмы: иначе он спрячет то, что продаётся."""
        variants = list(obj.variants.all())
        return bool(variants) and all(v.is_stopped for v in variants)

    def get_likes(self, obj):
        # берём аннотированное значение из queryset, иначе считаем на месте
        n = getattr(obj, "likes_count", None)
        return n if n is not None else obj.likes.count()

    def get_thumbnail(self, obj):
        # если превью ещё не сгенерировано — отдаём полное изображение
        if obj.thumbnail:
            return obj.thumbnail.url
        return obj.image.url if obj.image else None


class DishwareSampleSerializer(serializers.ModelSerializer):
    image = RelativeImageField()
    # default=True обязателен, и вот почему. Образец приезжает multipart-ом
    # (с ним файл), а DRF считает multipart html-формой: галочка, которой в
    # форме нет, трактуется как СНЯТАЯ — поле молча становится False, и
    # умолчание модели до него не доходит (Field.default_empty_html). Так
    # загруженный стакан оказывался «не используется»: в студии он не
    # показывался, к генерациям не цеплялся, и нейросеть придумывала посуду
    # сама. Явный default переопределяет и default_empty_html тоже.
    is_active = serializers.BooleanField(required=False, default=True)

    class Meta:
        model = DishwareSample
        fields = (
            "id", "name", "image", "note", "category", "sort_order", "is_active",
        )


class MenuImageSettingsSerializer(serializers.ModelSerializer):
    background = RelativeImageField(required=False, allow_null=True)
    sample_photo = RelativeImageField(required=False, allow_null=True)

    class Meta:
        model = MenuImageSettings
        fields = (
            "id", "style", "extra_prompt", "aspect_ratio", "background",
            "sample_photo",
        )


class ImageGenerationSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    image = RelativeImageField(read_only=True)

    class Meta:
        model = ImageGeneration
        fields = (
            "id", "batch", "source", "product", "product_name", "prompt",
            "model", "image", "status", "error", "cost_usd", "applied_at",
            "created_at",
        )
        read_only_fields = fields


class ImageBatchSerializer(serializers.ModelSerializer):
    """Пачка с прогрессом: фронт поллит её, пока рисуется."""

    generations = ImageGenerationSerializer(many=True, read_only=True)
    counts = serializers.SerializerMethodField()

    class Meta:
        model = ImageBatch
        fields = ("id", "category", "created_at", "counts", "generations")
        read_only_fields = fields

    def get_counts(self, obj):
        rows = list(obj.generations.all())
        return {
            "total": len(rows),
            "pending": sum(1 for r in rows if r.status == ImageGeneration.Status.PENDING),
            "ready": sum(1 for r in rows if r.status == ImageGeneration.Status.READY),
            "failed": sum(1 for r in rows if r.status == ImageGeneration.Status.FAILED),
        }


class ImageBatchCreateSerializer(serializers.Serializer):
    """Что нарисовать: конкретные блюда или категория целиком."""

    products = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Product.objects, required=False
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects, required=False, allow_null=True
    )
    #: для категории: только блюда без фото — обычный случай «заведи меню»
    only_without_photo = serializers.BooleanField(required=False, default=True)
    #: сколько вариантов на блюдо, чтобы владельцу было из чего выбрать
    variants = serializers.IntegerField(required=False, default=1, min_value=1, max_value=3)
    dishware = serializers.PrimaryKeyRelatedField(
        many=True, queryset=DishwareSample.objects, required=False
    )
    extra = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("products") and not attrs.get("category"):
            raise serializers.ValidationError(
                "Укажите блюда или категорию — иначе рисовать нечего."
            )
        return attrs

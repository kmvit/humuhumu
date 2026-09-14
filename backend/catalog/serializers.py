from rest_framework import serializers

from .models import Category, Product, ProductVariant


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


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    image = RelativeImageField(required=False, allow_null=True)
    thumbnail = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()
    # Варианты приходят и уходят вместе с товаром: гостю нечего делать с
    # карточкой без цен, а админка правит их одной формой. Запись — через
    # вьюху (sync_variants): в multipart с картинкой вложенный список не
    # передать иначе как JSON-строкой.
    variants = ProductVariantSerializer(many=True, read_only=True)

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
            "is_available",
            "sort_order",
            "variants",
            "likes",
            "price",
            "weight_grams",
            "prep_minutes",
            "is_stopped",
        )

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

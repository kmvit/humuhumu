from rest_framework import serializers

from .models import Category, Product


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


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    image = RelativeImageField(required=False, allow_null=True)
    thumbnail = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()

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
            "price",
            "weight_grams",
            "prep_minutes",
            "is_available",
            "is_stopped",
            "sort_order",
            "likes",
        )

    def get_likes(self, obj):
        # берём аннотированное значение из queryset, иначе считаем на месте
        n = getattr(obj, "likes_count", None)
        return n if n is not None else obj.likes.count()

    def get_thumbnail(self, obj):
        # если превью ещё не сгенерировано — отдаём полное изображение
        if obj.thumbnail:
            return obj.thumbnail.url
        return obj.image.url if obj.image else None

from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    icon = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ("id", "name", "icon", "station", "sort_order", "is_active")

    def get_icon(self, obj):
        # относительный URL (/media/...), чтобы работало через прокси независимо от хоста
        return obj.icon.url if obj.icon else None


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "category",
            "category_name",
            "name",
            "description",
            "image",
            "price",
            "weight_grams",
            "is_available",
            "sort_order",
        )

    def get_image(self, obj):
        return obj.image.url if obj.image else None

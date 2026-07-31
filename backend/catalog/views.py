from rest_framework import viewsets

from users.permissions import ReadOnlyOrAdmin

from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer


class CategoryViewSet(viewsets.ModelViewSet):
    """Категории. Чтение — всем, запись — админу."""

    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    permission_classes = [ReadOnlyOrAdmin]


class ProductViewSet(viewsets.ModelViewSet):
    """Товары. Чтение — всем, запись — админу. Фильтр ?category=<id>."""

    serializer_class = ProductSerializer
    permission_classes = [ReadOnlyOrAdmin]

    def get_queryset(self):
        qs = Product.objects.select_related("category")
        # клиентам и гостям показываем только доступные товары
        if getattr(self.request.user, "role", None) != "admin":
            qs = qs.filter(is_available=True)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs

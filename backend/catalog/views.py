from django.db.models import Count
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import ReadOnlyOrAdmin

from .models import Category, Product, ProductLike
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

    def get_permissions(self):
        # лайки и топ доступны анонимным гостям
        if self.action in ("like", "unlike", "top"):
            return [AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Product.objects.select_related("category").annotate(
            likes_count=Count("likes")
        )
        # клиентам и гостям показываем только доступные товары
        if getattr(self.request.user, "role", None) != "admin":
            qs = qs.filter(is_available=True)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs

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

from django.db.models import Count, ProtectedError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import ReadOnlyOrAdmin

from .models import Category, Product, ProductLike
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

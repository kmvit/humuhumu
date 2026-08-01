from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import IsBarOrAdmin, IsCookOrAdmin, IsWaiterOrAdmin

from .models import Order
from .serializers import OrderCreateSerializer, OrderSerializer
from .services import OrderError, create_order


class OrderViewSet(viewsets.ModelViewSet):
    """Заказы. Создаёт официант; кухня/бар отмечают готовность; официант закрывает счёт."""

    http_method_names = ["get", "post", "patch"]

    def get_permissions(self):
        if self.action == "create":
            return [IsWaiterOrAdmin()]
        if self.action == "food_ready":
            return [IsCookOrAdmin()]
        if self.action == "drinks_ready":
            return [IsBarOrAdmin()]
        if self.action in ("close_table", "cancel"):
            return [IsWaiterOrAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items__product__category")
        params = self.request.query_params
        # доска кухни: открытые заказы с неготовой едой
        if params.get("station") == "kitchen":
            return qs.filter(
                status=Order.Status.OPEN,
                food_ready=False,
                items__product__category__station="kitchen",
            ).distinct()
        # доска бара: открытые заказы с неготовыми напитками
        if params.get("station") == "bar":
            return qs.filter(
                status=Order.Status.OPEN,
                drinks_ready=False,
                items__product__category__station="bar",
            ).distinct()
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("table"):
            qs = qs.filter(table=params["table"])
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        return OrderSerializer

    def create(self, request, *args, **kwargs):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = create_order(
                waiter=request.user,
                items=serializer.validated_data["items"],
                table=serializer.validated_data.get("table", ""),
            )
        except OrderError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def food_ready(self, request, pk=None):
        """Повар: еда готова."""
        order = self.get_object()
        order.food_ready = True
        order.save(update_fields=["food_ready"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def drinks_ready(self, request, pk=None):
        """Бар: напитки готовы."""
        order = self.get_object()
        order.drinks_ready = True
        order.save(update_fields=["drinks_ready"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def cancel(self, request, pk=None):
        """Официант: отменить заказ."""
        order = self.get_object()
        order.status = Order.Status.CANCELLED
        order.closed_by = request.user
        order.save(update_fields=["status", "closed_by"])
        return Response(OrderSerializer(order).data)

    @action(detail=False, methods=["post"])
    def close_table(self, request):
        """Официант закрывает счёт: все открытые заказы стола → закрыт."""
        table = str(request.data.get("table", "")).strip()
        if not table:
            return Response(
                {"detail": "Не указан стол"}, status=status.HTTP_400_BAD_REQUEST
            )
        open_orders = Order.objects.filter(table=table, status=Order.Status.OPEN)
        count = open_orders.update(
            status=Order.Status.PAID, closed_by=request.user
        )
        return Response({"table": table, "closed": count})

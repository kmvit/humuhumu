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
        if self.action == "food_status":
            return [IsCookOrAdmin()]
        if self.action == "drinks_status":
            return [IsBarOrAdmin()]
        if self.action in ("close_table", "cancel"):
            return [IsWaiterOrAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items__product__category")
        params = self.request.query_params
        # доска кухни: все открытые заказы с едой (канбан — видно все статусы)
        if params.get("station") == "kitchen":
            return qs.filter(
                status=Order.Status.OPEN,
                items__product__category__station="kitchen",
            ).distinct()
        # доска бара: все открытые заказы с напитками
        if params.get("station") == "bar":
            return qs.filter(
                status=Order.Status.OPEN,
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

    def _set_station_status(self, request, field):
        order = self.get_object()
        value = request.data.get("status")
        valid = {c for c, _ in Order.StationStatus.choices}
        if value not in valid:
            return Response(
                {"detail": "Неверный статус"}, status=status.HTTP_400_BAD_REQUEST
            )
        setattr(order, field, value)
        order.save(update_fields=[field])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def food_status(self, request, pk=None):
        """Повар двигает еду по канбану: new/in_progress/ready."""
        return self._set_station_status(request, "food_status")

    @action(detail=True, methods=["patch"])
    def drinks_status(self, request, pk=None):
        """Бар двигает напитки по канбану: new/in_progress/ready."""
        return self._set_station_status(request, "drinks_status")

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

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.models import User
from users.permissions import IsWaiterOrAdmin

from .models import Order
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    OrderStatusSerializer,
)
from .services import OrderError, create_order

# Матрица допустимых переходов статуса по ролям.
# admin может любой переход, поэтому в матрицу не входит.
STATUS_TRANSITIONS = {
    User.Role.COOK: {
        Order.Status.PREPARING: {Order.Status.READY},
    },
    User.Role.CASHIER: {
        Order.Status.READY: {Order.Status.PAID, Order.Status.CANCELLED},
        Order.Status.PREPARING: {Order.Status.CANCELLED},
    },
    User.Role.WAITER: {
        Order.Status.PREPARING: {Order.Status.CANCELLED},
    },
}


class OrderViewSet(viewsets.ModelViewSet):
    """Заказы. Создаёт официант, статусы двигают повар и кассир-бармен."""

    http_method_names = ["get", "post", "patch"]

    def get_permissions(self):
        if self.action == "create":
            return [IsWaiterOrAdmin()]
        # чтение и set_status — любому залогиненному сотруднику;
        # конкретные переходы статуса проверяем в set_status.
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items__product")
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
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
    def set_status(self, request, pk=None):
        """PATCH /api/orders/{id}/set_status/ — смена статуса.

        Повар: На кухне → Готов. Кассир-бармен: Готов → Оплачен/Отменён.
        Админ: любой переход. Остальным — 403.
        """
        order = self.get_object()
        serializer = OrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        role = request.user.role
        if role != User.Role.ADMIN:
            allowed = STATUS_TRANSITIONS.get(role, {}).get(order.status, set())
            if new_status not in allowed:
                raise PermissionDenied("Недопустимый переход статуса для вашей роли")

        order.status = new_status
        update_fields = ["status"]
        if new_status == Order.Status.PAID:
            order.cashier = request.user
            update_fields.append("cashier")
            pay_method = serializer.validated_data.get("pay_method")
            if pay_method:
                order.pay_method = pay_method
                update_fields.append("pay_method")
        order.save(update_fields=update_fields)
        return Response(OrderSerializer(order).data)

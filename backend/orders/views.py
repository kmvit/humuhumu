from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.models import User
from users.permissions import IsCashierOrAdmin
from wallet.services import InsufficientFunds

from .models import Order
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    OrderStatusSerializer,
)
from .services import OrderError, create_order


class OrderViewSet(viewsets.ModelViewSet):
    """Заказы. Клиент видит свои, кассир/админ — все."""

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        user = self.request.user
        qs = Order.objects.prefetch_related("items__product")
        if user.role == User.Role.CLIENT:
            return qs.filter(client=user)
        return qs  # кассир/админ видят всё

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        return OrderSerializer

    def create(self, request, *args, **kwargs):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = create_order(
                client=request.user,
                pay_method=serializer.validated_data["pay_method"],
                items=serializer.validated_data["items"],
            )
        except InsufficientFunds:
            return Response(
                {"detail": "Недостаточно токенов на счёте"},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        except OrderError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], permission_classes=[IsCashierOrAdmin])
    def set_status(self, request, pk=None):
        """PATCH /api/orders/{id}/set_status/ — смена статуса кассиром/админом."""
        order = self.get_object()
        serializer = OrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order.status = serializer.validated_data["status"]
        order.cashier = request.user
        order.save(update_fields=["status", "cashier"])
        return Response(OrderSerializer(order).data)

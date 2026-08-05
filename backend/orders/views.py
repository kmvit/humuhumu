from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from catalog.models import Category
from users.models import User
from users.permissions import IsBarOrAdmin, IsCookOrAdmin, IsWaiterOrAdmin

from .models import Order, OrderItem, Table
from .serializers import (
    ClientOrderSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    TableSerializer,
)
from .services import OrderError, create_order, create_request


class TableViewSet(viewsets.ReadOnlyModelViewSet):
    """Список столов для официанта. Управление — через админку."""

    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated]
    queryset = Table.objects.filter(is_active=True)

STATION_TIMES = {
    Category.Station.KITCHEN: ("food_started_at", "food_ready_at"),
    Category.Station.BAR: ("drinks_started_at", "drinks_ready_at"),
}


class OrderViewSet(viewsets.ModelViewSet):
    """Заказы: официант создаёт/закрывает; кухня и бар ведут готовность позиций."""

    http_method_names = ["get", "post", "patch"]

    def get_permissions(self):
        if self.action in ("place", "track", "cancel_request"):
            return [AllowAny()]  # клиент без авторизации
        if self.action == "create":
            return [IsWaiterOrAdmin()]
        if self.action == "food_status":
            return [IsCookOrAdmin()]
        if self.action == "drinks_status":
            return [IsBarOrAdmin()]
        if self.action in ("close_table", "close", "cancel", "remove_item", "item_guest", "confirm", "set_comment", "move"):
            return [IsWaiterOrAdmin()]
        # item_status — право проверяем внутри по станции позиции
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items__product__category")
        params = self.request.query_params
        # доски кухни/бара: все открытые заказы с позициями этой станции
        if params.get("station") == "kitchen":
            return qs.filter(
                status=Order.Status.OPEN,
                items__product__category__station="kitchen",
            ).distinct()
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
                comment=serializer.validated_data.get("comment", ""),
            )
        except OrderError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    # --- клиентские заявки без авторизации ---

    @action(detail=False, methods=["post"])
    def place(self, request):
        """Клиент отправляет заявку (имя + позиции). Стол назначит официант."""
        serializer = ClientOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = create_request(
                customer_name=serializer.validated_data.get("customer_name", ""),
                items=serializer.validated_data["items"],
                table=serializer.validated_data.get("table", ""),
                comment=serializer.validated_data.get("comment", ""),
            )
        except OrderError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def track(self, request):
        """Клиент смотрит статус своей заявки/заказа по токену."""
        token = request.query_params.get("token")
        order = None
        if token:
            order = (
                Order.objects.prefetch_related("items__product__category")
                .filter(public_token=token)
                .first()
            )
        if not order:
            return Response(
                {"detail": "Заказ не найден"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(OrderSerializer(order).data)

    @action(detail=False, methods=["post"])
    def cancel_request(self, request):
        """Клиент отменяет свою заявку по токену, пока официант её не подтвердил."""
        token = request.data.get("token")
        order = Order.objects.filter(public_token=token).first() if token else None
        if not order:
            return Response(
                {"detail": "Заказ не найден"}, status=status.HTTP_404_NOT_FOUND
            )
        if order.status != Order.Status.REQUESTED:
            return Response(
                {"detail": "Заказ уже подтверждён официантом — отмена только через официанта"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order.status = Order.Status.CANCELLED
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def confirm(self, request, pk=None):
        """Официант подтверждает заявку клиента: назначает стол, заказ идёт в работу."""
        order = self.get_object()
        if order.status != Order.Status.REQUESTED:
            return Response(
                {"detail": "Это не заявка"}, status=status.HTTP_400_BAD_REQUEST
            )
        table = str(request.data.get("table", "")).strip()
        if not table:
            return Response(
                {"detail": "Не указан стол"}, status=status.HTTP_400_BAD_REQUEST
            )
        order.table = table
        order.status = Order.Status.OPEN
        order.waiter = request.user
        order.save(update_fields=["table", "status", "waiter"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def move(self, request, pk=None):
        """Официант переносит заказ на другой стол (гость пересел за другой стол)."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Переносить можно только открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        table = str(request.data.get("table", "")).strip()
        if not table:
            return Response(
                {"detail": "Не указан стол"}, status=status.HTTP_400_BAD_REQUEST
            )
        if table == order.table:
            return Response(
                {"detail": "Заказ уже на этом столе"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order.table = table
        order.save(update_fields=["table"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"], url_path="comment")
    def set_comment(self, request, pk=None):
        """Официант добавляет/меняет комментарий к заказу."""
        order = self.get_object()
        order.comment = str(request.data.get("comment", "")).strip()[:300]
        order.save(update_fields=["comment"])
        return Response(OrderSerializer(order).data)

    # --- готовность станций/позиций ---

    def _sync_times(self, order, station):
        """Проставить/сбросить таймстемпы станции по агрегату статусов её позиций."""
        started, ready = STATION_TIMES[station]
        agg = Order.aggregate_status(
            i.status for i in order.items.all() if i.station == station
        )
        now = timezone.now()
        changed = []
        if agg in (Order.StationStatus.IN_PROGRESS, Order.StationStatus.READY):
            if getattr(order, started) is None:
                setattr(order, started, now); changed.append(started)
        if agg == Order.StationStatus.READY:
            if getattr(order, ready) is None:
                setattr(order, ready, now); changed.append(ready)
        elif getattr(order, ready) is not None:
            setattr(order, ready, None); changed.append(ready)
        if changed:
            order.save(update_fields=changed)

    def _reload_and_respond(self, pk, *stations):
        order = Order.objects.prefetch_related("items__product__category").get(pk=pk)
        for st in stations:
            self._sync_times(order, st)
        return Response(OrderSerializer(order).data)

    @staticmethod
    def _valid_status(value):
        return value in {c for c, _ in Order.StationStatus.choices}

    def _bulk_station(self, request, station):
        order = self.get_object()
        value = request.data.get("status")
        if not self._valid_status(value):
            return Response({"detail": "Неверный статус"}, status=status.HTTP_400_BAD_REQUEST)
        items = [i for i in order.items.all() if i.station == station]
        for it in items:
            it.status = value
        if items:
            OrderItem.objects.bulk_update(items, ["status"])
        return self._reload_and_respond(order.pk, station)

    @action(detail=True, methods=["patch"])
    def food_status(self, request, pk=None):
        """Повар: перевести всю еду заказа в статус new/in_progress/ready."""
        return self._bulk_station(request, Category.Station.KITCHEN)

    @action(detail=True, methods=["patch"])
    def drinks_status(self, request, pk=None):
        """Бар: перевести все напитки заказа в статус new/in_progress/ready."""
        return self._bulk_station(request, Category.Station.BAR)

    @action(detail=True, methods=["patch"])
    def item_status(self, request, pk=None):
        """Готовность отдельной позиции. Повар — кухня, бар — бар, админ — всё."""
        order = self.get_object()
        value = request.data.get("status")
        if not self._valid_status(value):
            return Response({"detail": "Неверный статус"}, status=status.HTTP_400_BAD_REQUEST)
        item = order.items.filter(id=request.data.get("item_id")).first()
        if not item:
            return Response({"detail": "Позиция не найдена"}, status=status.HTTP_404_NOT_FOUND)
        role, st = request.user.role, item.station
        allowed = (
            role == User.Role.ADMIN
            or (role == User.Role.COOK and st == Category.Station.KITCHEN)
            or (role == User.Role.BAR and st == Category.Station.BAR)
        )
        if not allowed:
            return Response(
                {"detail": "Нет прав на эту позицию"}, status=status.HTTP_403_FORBIDDEN
            )
        item.status = value
        item.save(update_fields=["status"])
        return self._reload_and_respond(order.pk, st)

    @action(detail=True, methods=["patch"])
    def cancel(self, request, pk=None):
        """Официант: отменить заказ."""
        order = self.get_object()
        order.status = Order.Status.CANCELLED
        order.closed_by = request.user
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_by", "closed_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def remove_item(self, request, pk=None):
        """Официант убирает позицию из открытого заказа (гость передумал)."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Менять можно только открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item = order.items.filter(id=request.data.get("item_id")).first()
        if not item:
            return Response(
                {"detail": "Позиция не найдена"}, status=status.HTTP_404_NOT_FOUND
            )
        item.delete()
        order = Order.objects.prefetch_related("items__product__category").get(pk=order.pk)
        if order.items.exists():
            order.recalc_total()
            order.save(update_fields=["total"])
            # агрегат станций мог измениться — освежаем таймстемпы
            self._sync_times(order, Category.Station.KITCHEN)
            self._sync_times(order, Category.Station.BAR)
        else:
            order.total = 0
            order.status = Order.Status.CANCELLED
            order.closed_by = request.user
            order.closed_at = timezone.now()
            order.save(update_fields=["total", "status", "closed_by", "closed_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"])
    def item_guest(self, request, pk=None):
        """Официант меняет гостя у позиции (в т.ч. при разбиении счёта на закрытии)."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Менять можно только открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item = order.items.filter(id=request.data.get("item_id")).first()
        if not item:
            return Response(
                {"detail": "Позиция не найдена"}, status=status.HTTP_404_NOT_FOUND
            )
        guest = request.data.get("guest")
        item.guest = int(guest) if guest else None
        item.save(update_fields=["guest"])
        order = Order.objects.prefetch_related("items__product__category").get(pk=order.pk)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Официант закрывает один заказ (не весь стол)."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Закрыть можно только открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order.status = Order.Status.PAID
        order.closed_by = request.user
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_by", "closed_at"])
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
            status=Order.Status.PAID,
            closed_by=request.user,
            closed_at=timezone.now(),
        )
        return Response({"table": table, "closed": count})

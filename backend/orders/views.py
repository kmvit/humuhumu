from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from catalog.models import Category
from inventory.services import return_order_item, write_off_order_item
from users.models import User
from users.permissions import IsBarOrAdmin, IsCookOrAdmin, IsWaiterOrAdmin

from .models import Order, OrderItem, Table
from .serializers import (
    ClientOrderSerializer,
    OrderCreateSerializer,
    OrderItemCreateSerializer,
    OrderSerializer,
    TableSerializer,
)
from .services import OrderError, append_items, create_order, create_request
from payments.models import Payment
from payments.services import (
    PaymentError,
    apply_payment_result,
    record_manual_payment,
    start_terminal_payment,
)


class TableViewSet(viewsets.ReadOnlyModelViewSet):
    """Список столов для официанта. Управление — через админку."""

    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated]
    queryset = Table.objects.filter(is_active=True)

STATION_TIMES = {
    Category.Station.KITCHEN: ("food_started_at", "food_ready_at"),
    Category.Station.BAR: ("drinks_started_at", "drinks_ready_at"),
}
STATION_SERVED = {
    Category.Station.KITCHEN: "food_served_at",
    Category.Station.BAR: "drinks_served_at",
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
        if self.action in ("close_table", "close", "cancel", "add_items", "remove_item", "item_guest", "item_qty", "confirm", "set_comment", "move", "serve", "pay_terminal", "pay_result"):
            return [IsWaiterOrAdmin()]
        # item_status — право проверяем внутри по станции позиции
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items__product__category")
        params = self.request.query_params
        # активные заказы: открытые + оплаченные сегодня (оплата вперёд — кухня
        # ещё готовит). Историю оплаченных не тянем, чтобы не залить доску.
        active = (
            Q(status=Order.Status.OPEN)
            | Q(status=Order.Status.AWAITING)  # отправлен на терминал — стол ещё занят
            | Q(status=Order.Status.PAID, closed_at__date=timezone.localdate())
        )
        # доски кухни/бара: карточка уходит только когда станция ГОТОВА и заказ
        # ЗАКРЫТ. Пока готовится — висит, даже если официант уже закрыл счёт.
        # Открытый заказ показываем всегда (готовый лежит в «Готов» до закрытия).
        if params.get("station") in ("kitchen", "bar"):
            st = params["station"]
            ready = "food_ready_at" if st == "kitchen" else "drinks_ready_at"
            board = (
                Q(status=Order.Status.OPEN)
                | Q(status=Order.Status.AWAITING)
                | Q(
                    status=Order.Status.PAID,
                    closed_at__date=timezone.localdate(),
                    **{f"{ready}__isnull": True},  # закрыт, но ещё не готов → показываем
                )
            )
            return qs.filter(
                board, items__product__category__station=st
            ).distinct()
        # лента «К подаче» у официанта: станция готова, но ещё не отнесена (served).
        # Отдельный механизм — кнопка «Подал» не зависит от досок.
        if params.get("serve") == "1":
            return qs.filter(active).filter(
                Q(food_ready_at__isnull=False, food_served_at__isnull=True)
                | Q(drinks_ready_at__isnull=False, drinks_served_at__isnull=True)
            ).distinct()
        if params.get("status"):
            st = params["status"]
            # доска официанта (status=open) включает и отправленные на терминал
            if st == Order.Status.OPEN:
                qs = qs.filter(status__in=[Order.Status.OPEN, Order.Status.AWAITING])
            else:
                qs = qs.filter(status=st)
        if params.get("table"):
            qs = qs.filter(table=params["table"])
        # закрытые счета — по умолчанию только за сегодня (список у официанта)
        if params.get("closed") == "today":
            qs = qs.filter(closed_at__date=timezone.localdate())
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

    @action(detail=True, methods=["patch"])
    def serve(self, request, pk=None):
        """Официант отметил, что отнёс готовое станции гостю (kitchen/bar)."""
        order = self.get_object()
        field = STATION_SERVED.get(request.data.get("station"))
        if not field:
            return Response(
                {"detail": "Неверная станция"}, status=status.HTTP_400_BAD_REQUEST
            )
        if getattr(order, field) is None:
            setattr(order, field, timezone.now())
            order.save(update_fields=[field])
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
        served = STATION_SERVED[station]
        if agg == Order.StationStatus.READY:
            if getattr(order, ready) is None:
                setattr(order, ready, now); changed.append(ready)
        elif getattr(order, ready) is not None:
            setattr(order, ready, None); changed.append(ready)
            # станция вышла из готовности (напр. дозаказ) — снимаем «подано»,
            # чтобы после повторной готовности заказ снова попал в ленту «К подаче»
            if getattr(order, served) is not None:
                setattr(order, served, None); changed.append(served)
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
        if value == Order.StationStatus.READY:
            for it in items:
                write_off_order_item(it, user=request.user)
        return self._reload_and_respond(order.pk, station)

    @action(detail=True, methods=["patch"])
    def work_status(self, request, pk=None):
        """Стойка: перевести ВЕСЬ заказ в new/in_progress/ready.

        В режиме стойки один человек собирает и еду, и напитки, поэтому
        делить очередь по станциям незачем — статус ставится на весь заказ.
        """
        order = self.get_object()
        value = request.data.get("status")
        if not self._valid_status(value):
            return Response({"detail": "Неверный статус"}, status=status.HTTP_400_BAD_REQUEST)
        items = list(order.items.all())
        for it in items:
            it.status = value
        if items:
            OrderItem.objects.bulk_update(items, ["status"])
        if value == Order.StationStatus.READY:
            for it in items:
                write_off_order_item(it, user=request.user)
        return self._reload_and_respond(
            order.pk, Category.Station.KITCHEN, Category.Station.BAR
        )

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
        if value == Order.StationStatus.READY:
            # Блюдо приготовлено — продукты по тех карте фактически израсходованы.
            write_off_order_item(item, user=request.user)
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

    @action(detail=True, methods=["post"], url_path="add_items")
    def add_items(self, request, pk=None):
        """Официант дописывает позиции в открытый заказ (гость дозаказал)."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Добавлять можно только в открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        items_ser = OrderItemCreateSerializer(data=request.data.get("items", []), many=True)
        items_ser.is_valid(raise_exception=True)
        try:
            append_items(order=order, items=items_ser.validated_data)
        except OrderError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        # новые позиции идут со статусом «новый» — освежаем агрегаты/таймстемпы станций
        order = Order.objects.prefetch_related("items__product__category").get(pk=order.pk)
        self._sync_times(order, Category.Station.KITCHEN)
        self._sync_times(order, Category.Station.BAR)
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
        if self._waiter_locked(request, item):
            return Response(
                {"detail": "Кухня/бар уже готовят — позицию убрать нельзя"},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Если по позиции уже списали склад — возвращаем продукты обратно.
        return_order_item(item, user=request.user)
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

    @action(detail=True, methods=["patch"], url_path="item_qty")
    def item_qty(self, request, pk=None):
        """Официант меняет количество позиции в открытом заказе (кнопка «+»)."""
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
        try:
            qty = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return Response({"detail": "Неверное количество"}, status=status.HTTP_400_BAD_REQUEST)
        if qty < 1:
            return Response(
                {"detail": "Количество должно быть положительным"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if qty != item.quantity and self._waiter_locked(request, item):
            return Response(
                {"detail": "Кухня/бар уже готовят — количество менять нельзя"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if item.stock_written_off_at:
            # позиция уже списана со склада — пересписываем на новое количество
            return_order_item(item, user=request.user)
            item.quantity = qty
            item.save(update_fields=["quantity"])
            write_off_order_item(item, user=request.user)
        else:
            item.quantity = qty
            item.save(update_fields=["quantity"])
        order = Order.objects.prefetch_related("items__product__category").get(pk=order.pk)
        order.recalc_total()
        order.save(update_fields=["total"])
        return Response(OrderSerializer(order).data)

    @staticmethod
    def _waiter_locked(request, item):
        """Официанту нельзя убирать/уменьшать позицию, которую станция уже
        взяла в работу или приготовила (не «новую»). Админа не ограничиваем."""
        return (
            request.user.role != User.Role.ADMIN
            and item.status != Order.StationStatus.NEW
        )

    @staticmethod
    def _pay_method(request):
        """Способ оплаты из запроса; по умолчанию — наличные."""
        method = str(request.data.get("pay_method", "")).strip()
        return method if method in Order.PayMethod.values else Order.PayMethod.CASH

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Официант закрывает один заказ (не весь стол), фиксируя способ оплаты."""
        order = self.get_object()
        if order.status != Order.Status.OPEN:
            return Response(
                {"detail": "Закрыть можно только открытый заказ"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record_manual_payment(order, self._pay_method(request), request.user)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="pay_terminal")
    def pay_terminal(self, request, pk=None):
        """Официант отправляет счёт на кассу-терминал → заказ «к оплате».

        Результат (оплачено/отказ) применяется отдельно: сейчас — дев-ручкой
        pay_result, в бою — вебхуком провайдера (payments.services.apply_payment_result).
        """
        order = self.get_object()
        method = str(request.data.get("method", Payment.Method.CARD))
        try:
            start_terminal_payment(order, method=method)
        except PaymentError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="pay_result")
    def pay_result(self, request, pk=None):
        """Применить результат оплаты с терминала (эмуляция / ручное подтверждение).

        В проде это место занимает вебхук провайдера; ручка оставлена для
        дев-эмуляции и как резервный ручной ввод результата кассиром.
        """
        order = self.get_object()
        payment = (
            Payment.objects.filter(order=order, status=Payment.Status.PENDING)
            .order_by("-created_at")
            .first()
        )
        if not payment:
            return Response(
                {"detail": "Нет ожидающего оплату платежа"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        success = str(request.data.get("result", "")) == "success"
        fiscal = str(request.data.get("fiscal_receipt", "")).strip()
        apply_payment_result(payment, success=success, fiscal_receipt=fiscal, user=request.user)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @action(detail=False, methods=["post"])
    def close_table(self, request):
        """Официант закрывает счёт: все открытые заказы стола → закрыт."""
        table = str(request.data.get("table", "")).strip()
        if not table:
            return Response(
                {"detail": "Не указан стол"}, status=status.HTTP_400_BAD_REQUEST
            )
        method = self._pay_method(request)
        # по заказу: закрываем и создаём Payment на каждый (единый реестр платежей)
        open_orders = list(Order.objects.filter(table=table, status=Order.Status.OPEN))
        for order in open_orders:
            record_manual_payment(order, method, request.user)
        return Response({"table": table, "closed": len(open_orders)})

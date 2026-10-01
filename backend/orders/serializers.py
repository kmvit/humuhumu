from decimal import Decimal

from rest_framework import serializers

from .models import Order, OrderItem, OrderItemModifier, Table


class OrderItemModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemModifier
        fields = ("id", "modifier", "name", "price_delta")


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ("id", "name", "sort_order", "is_active")


class OrderItemSerializer(serializers.ModelSerializer):
    # product — id карточки меню (для группировок на фронте), product_name —
    # полное имя с вариантом: доскам и чекам не нужно собирать его самим
    product = serializers.IntegerField(source="variant.product_id", read_only=True)
    product_name = serializers.CharField(source="display_name", read_only=True)
    variant_label = serializers.CharField(source="variant.label", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    station = serializers.CharField(read_only=True)
    modifiers = OrderItemModifierSerializer(many=True, read_only=True)
    # «овсяное, без сиропа» — кухне и бару важнее строки, чем список объектов
    options_text = serializers.CharField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id", "variant", "variant_label", "product", "product_name",
            "station", "status", "guest", "quantity", "unit_price", "subtotal",
            "modifiers", "options_text",
        )
        read_only_fields = ("unit_price",)


class OrderSerializer(serializers.ModelSerializer):
    """Чтение заказа со списком позиций."""

    items = OrderItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    performer_name = serializers.SerializerMethodField()
    # Гость бонусной программы на заказе: сотрудник видит, что номер уже
    # назван и бонусы за чек начислятся, — и не спрашивает второй раз.
    bonus_guest = serializers.SerializerMethodField()
    pay_method_display = serializers.CharField(source="get_pay_method_display", read_only=True)
    has_food = serializers.BooleanField(read_only=True)
    has_drinks = serializers.BooleanField(read_only=True)
    is_ready = serializers.BooleanField(read_only=True)
    food_status = serializers.ReadOnlyField()
    drinks_status = serializers.ReadOnlyField()
    food_served = serializers.SerializerMethodField()
    drinks_served = serializers.SerializerMethodField()
    payable = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    # Заказ лежит на кассе и ждёт оплаты: гостю — «назовите номер на
    # кассе», баристе — не принимать деньги мимо кассы второй раз.
    kassa_waiting = serializers.SerializerMethodField()
    # Каким путём заплатили: касса / онлайн / отметка сотрудника. Сводке
    # владельца мало «картой»: карта онлайн и карта на кассе — разные
    # деньги, их сверяют с разными выписками.
    pay_channel = serializers.SerializerMethodField()
    # Возврат через кассу идёт по шагам: «pending» — касса возвращает,
    # «failed» — не вышло (refund_error — почему), пусто — возврата нет
    # или он завершён (тогда status уже «refunded»).
    refund_state = serializers.SerializerMethodField()
    refund_error = serializers.SerializerMethodField()

    def _kassa_refund(self, obj):
        # Только у оплаченного и ещё не возвращённого — у остальных
        # возврата через кассу быть не может, и запрос на доске не нужен.
        if obj.status != Order.Status.PAID or obj.paid_at is None:
            return None
        cache = getattr(obj, "_kassa_refund_cache", ...)
        if cache is ...:
            from payments.services import kassa_refunds

            cache = kassa_refunds(obj).order_by("-created_at").first()
            obj._kassa_refund_cache = cache
        return cache

    def get_refund_state(self, obj) -> str:
        from payments.models import Payment

        refund = self._kassa_refund(obj)
        if refund is None:
            return ""
        return {
            Payment.Status.PENDING: "pending",
            Payment.Status.FAILED: "failed",
        }.get(refund.status, "")

    def get_refund_error(self, obj) -> str:
        from payments.models import Payment

        refund = self._kassa_refund(obj)
        if refund is None or refund.status != Payment.Status.FAILED:
            return ""
        return str((refund.kassa_meta or {}).get("error") or "")

    def get_pay_channel(self, obj) -> str:
        from payments.journal import channel_of

        provider = getattr(obj, "pay_provider_ann", None)
        if provider is None and obj.paid_at:
            from payments.models import Payment

            provider = (
                Payment.objects.filter(
                    order=obj, purpose=Payment.Purpose.ORDER,
                    status__in=(Payment.Status.SUCCEEDED, Payment.Status.REFUNDED),
                )
                .order_by("-created_at")
                .values_list("provider", flat=True)
                .first()
            )
        return channel_of(provider) if provider else ""

    def get_kassa_waiting(self, obj) -> bool:
        # Списки приходят с аннотацией (OrderViewSet.get_queryset) — без
        # неё доска делала бы по запросу на каждую карточку.
        annotated = getattr(obj, "kassa_waiting_ann", None)
        if annotated is not None:
            return bool(annotated)
        from payments.services import pending_kassa_payments

        return pending_kassa_payments(obj).exists()

    class Meta:
        model = Order
        fields = (
            "id",
            "client",
            "waiter",
            "performer",
            "performer_name",
            "closed_by",
            "table",
            "daily_number",
            "comment",
            "customer_name",
            "public_token",
            "status",
            "status_display",
            "pay_method",
            "pay_method_display",
            "fiscal_receipt",
            # Оплачен вперёд: бариста должен видеть на карточке, что
            # деньги уже получены, и не спрашивать их при выдаче.
            "paid_at",
            "refunded_at",
            "food_status",
            "drinks_status",
            "food_served",
            "drinks_served",
            "has_food",
            "has_drinks",
            "is_ready",
            "total",
            "bonus_spent",
            "bonus_guest",
            "payable",
            "kassa_waiting",
            "pay_channel",
            "refund_state",
            "refund_error",
            "items",
            "created_at",
            "food_started_at",
            "food_ready_at",
            "drinks_started_at",
            "drinks_ready_at",
            "food_served_at",
            "drinks_served_at",
            "closed_at",
        )

    def get_performer_name(self, obj) -> str:
        """Имя исполнителя для карточки — без второго запроса за профилем."""
        if not obj.performer:
            return ""
        full = f"{obj.performer.first_name} {obj.performer.last_name}".strip()
        return full or obj.performer.username

    def get_bonus_guest(self, obj):
        member = getattr(obj.client, "loyalty", None) if obj.client_id else None
        if member is None:
            return None
        return {"name": member.name, "phone": member.phone, "balance": member.balance}

    def get_food_served(self, obj):
        return obj.food_served_at is not None

    def get_drinks_served(self, obj):
        return obj.drinks_served_at is not None


class OrderItemCreateSerializer(serializers.Serializer):
    variant = serializers.IntegerField(required=False)
    # Старые клиенты (закэшированный PWA-бандл) шлют товар, не вариант.
    # Пока у товара один вариант, это однозначно; поле — на снос, когда
    # прошлые бандлы вымоются из кэшей.
    product = serializers.IntegerField(required=False)
    quantity = serializers.IntegerField(min_value=1, default=1)
    guest = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    #: id выбранных опций; что они значат и можно ли их вместе — решает сервер
    modifiers = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )

    def validate(self, attrs):
        if not attrs.get("variant") and not attrs.get("product"):
            raise serializers.ValidationError("Укажите variant")
        return attrs


class OrderCreateSerializer(serializers.Serializer):
    """Создание заказа официантом."""

    table = serializers.CharField(max_length=32, required=False, allow_blank=True)
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)
    # Кто выполняет. Не обязателен намеренно: в час пик выбирать некогда,
    # и заказ без исполнителя должен приниматься как прежде.
    performer = serializers.IntegerField(required=False, allow_null=True)
    items = OrderItemCreateSerializer(many=True)
    # Гость бонусной программы и сколько бонусов списать. Задаются при
    # создании, а не потом: на стойке с кассой заказ уходит на кассу сразу,
    # и списание, сделанное после, касса уже не увидит.
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    bonus = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal("0"), default=Decimal("0")
    )

    def validate_phone(self, value):
        if not (value or "").strip():
            return ""
        from loyalty.serializers import normalize_phone

        return normalize_phone(value)

    def validate(self, attrs):
        if attrs.get("bonus") and not attrs.get("phone"):
            raise serializers.ValidationError({"bonus": "Списать бонусы можно только с гостем"})
        return attrs


class ClientOrderSerializer(serializers.Serializer):
    """Заявка от клиента без авторизации: имя + позиции (+ стол из QR-кода)."""

    customer_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    comment = serializers.CharField(max_length=300, required=False, allow_blank=True)
    table = serializers.CharField(max_length=32, required=False, allow_blank=True)
    # Телефон нужен только бонусной программе: по нему заказ привязывается к
    # гостю, иначе начислять некому. Поле необязательное — гость, которому
    # бонусы не нужны, не должен упираться в него по дороге к заказу.
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    items = OrderItemCreateSerializer(many=True)

    def validate_phone(self, value):
        # Пустое поле пропускаем, а введённое проверяем: молча потерять
        # телефон из-за опечатки хуже, чем сказать про неё сразу.
        if not (value or "").strip():
            return ""
        from loyalty.serializers import normalize_phone

        return normalize_phone(value)

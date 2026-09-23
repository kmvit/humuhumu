from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import (
    Category,
    Modifier,
    ModifierGroup,
    Product,
    ProductVariant,
)
from core.models import SiteSettings
from payments.acquiring import YooKassaAcquirer
from payments.models import Payment
from payments.services import apply_payment_result
from users.models import User

from .models import Order, OrderItem
from .tasks import cancel_stale_unpaid_orders_task


class OrderFlowBase(APITestCase):
    """Общая обвязка для сценариев заказа: меню, бариста, вход, отправка."""

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=cat, name="Латте")
        self.latte_v = ProductVariant.objects.create(
            product=self.latte, price=Decimal("240")
        )
        self.waiter = User.objects.create_user(
            username="barista", password="demo12345", role=User.Role.WAITER
        )

    def set_mode(self, mode):
        site = SiteSettings.load()
        site.service_mode = mode
        site.save()

    def place(self, table=""):
        return self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "items": [{"product": self.latte.id, "quantity": 1}],
             **({"table": table} if table else {})},
            format="json",
        )

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")


class CounterModeTests(OrderFlowBase):
    """Стойка: гость заказывает по QR и забирает по номеру.

    Отличия от зала: нет стола, нет подтверждения официантом, заказ сразу
    уходит в работу и получает номер, который обнуляется каждый день.
    """

    # ——— зал: поведение не должно измениться ———

    def test_hall_order_waits_for_waiter(self):
        self.set_mode("hall")
        res = self.place(table="5")
        self.assertEqual(res.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.REQUESTED)
        self.assertEqual(order.table, "5")

    # ——— стойка ———

    def test_counter_order_goes_straight_to_work(self):
        self.set_mode("counter")
        res = self.place()
        self.assertEqual(res.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.OPEN)

    def test_counter_ignores_table_from_qr(self):
        """У будки столов нет — даже если стол пришёл в запросе."""
        self.set_mode("counter")
        self.place(table="7")
        self.assertEqual(Order.objects.get().table, "")

    def test_daily_number_counts_from_one(self):
        self.set_mode("counter")
        for _ in range(3):
            self.place()
        self.assertEqual(
            list(Order.objects.order_by("id").values_list("daily_number", flat=True)),
            [1, 2, 3],
        )

    def test_guest_sees_own_number(self):
        self.set_mode("counter")
        token = self.place().data["public_token"]
        res = self.client.get(f"/api/orders/track/?token={token}")
        self.assertEqual(res.data["daily_number"], 1)

    # ——— экран стойки ———

    def test_work_status_moves_whole_order(self):
        """Один человек собирает всё — статус ставится на заказ целиком."""
        self.set_mode("counter")
        self.place()
        order = Order.objects.get()
        self.auth(self.waiter)
        res = self.client.patch(
            f"/api/orders/{order.id}/work_status/", {"status": "ready"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(all(i["status"] == "ready" for i in res.data["items"]))
        self.assertTrue(res.data["is_ready"])

    def test_work_status_rejects_garbage(self):
        self.set_mode("counter")
        self.place()
        order = Order.objects.get()
        self.auth(self.waiter)
        res = self.client.patch(
            f"/api/orders/{order.id}/work_status/", {"status": "готово"}, format="json"
        )
        self.assertEqual(res.status_code, 400)


class StaffOrderTests(OrderFlowBase):
    """Заказ, который заводит сам сотрудник: гость сказал на словах.

    В зале это заказ на стол, на стойке — заказ с номером и без стола.
    Без номера бариста не сможет позвать гостя, а гость — понять, что готово.
    """

    def create(self, table=""):
        self.auth(self.waiter)
        return self.client.post(
            "/api/orders/",
            {"items": [{"product": self.latte.id, "quantity": 1}],
             **({"table": table} if table else {})},
            format="json",
        )

    def test_counter_staff_order_gets_number(self):
        self.set_mode("counter")
        res = self.create()
        self.assertEqual(res.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.daily_number, 1)
        self.assertEqual(order.table, "")

    def test_counter_numbers_shared_with_guest_orders(self):
        """Гостевые и принятые на словах заказы идут одной очередью номеров."""
        self.set_mode("counter")
        self.place()  # заказ гостя по QR → №1
        self.create()  # заказ на словах → №2
        self.assertEqual(
            list(Order.objects.order_by("id").values_list("daily_number", flat=True)), [1, 2]
        )

    def test_hall_staff_order_keeps_table_and_no_number(self):
        """В зале ничего не меняется: стол на месте, номер не нужен."""
        self.set_mode("hall")
        res = self.create(table="7")
        self.assertEqual(res.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.table, "7")
        self.assertIsNone(order.daily_number)


class MoveItemsTests(OrderFlowBase):
    """Перенос отдельных позиций на другой стол: пересела часть компании.

    Позиции уходят в открытый заказ целевого стола, а без него — в новый.
    Суммы обоих заказов пересчитываются; выбор всех позиций — обычный move.
    """

    def setUp(self):
        super().setUp()
        self.set_mode("hall")
        self.auth(self.waiter)

    def create_order(self, table, lines=2):
        res = self.client.post(
            "/api/orders/",
            {"table": table,
             "items": [{"product": self.latte.id, "quantity": 1}] * lines},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        return Order.objects.get(pk=res.data["id"])

    def move(self, order, table, item_ids):
        return self.client.post(
            f"/api/orders/{order.id}/move_items/",
            {"table": table, "item_ids": item_ids},
            format="json",
        )

    def test_subset_moves_to_new_order_on_free_table(self):
        order = self.create_order("5")
        moved, kept = order.items.all()
        res = self.move(order, "7", [moved.id])
        self.assertEqual(res.status_code, 200)
        target = Order.objects.exclude(pk=order.pk).get()
        self.assertEqual(target.table, "7")
        self.assertEqual(target.status, Order.Status.OPEN)
        self.assertEqual(target.waiter, self.waiter)
        self.assertEqual(list(target.items.all()), [moved])
        order.refresh_from_db()
        self.assertEqual(list(order.items.all()), [kept])
        self.assertEqual(order.total, Decimal("240"))
        self.assertEqual(target.total, Decimal("240"))

    def test_subset_merges_into_open_order_of_target_table(self):
        target = self.create_order("7", lines=1)
        order = self.create_order("5")
        moved = order.items.first()
        res = self.move(order, "7", [moved.id])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Order.objects.count(), 2)  # новый заказ не создан
        target.refresh_from_db()
        self.assertEqual(target.items.count(), 2)
        self.assertEqual(target.total, Decimal("480"))

    def test_all_items_just_retable_the_order(self):
        order = self.create_order("5")
        res = self.move(order, "7", [i.id for i in order.items.all()])
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.table, "7")
        self.assertEqual(Order.objects.count(), 1)

    def test_rejects_same_table_and_empty_selection(self):
        order = self.create_order("5")
        self.assertEqual(self.move(order, "5", [order.items.first().id]).status_code, 400)
        self.assertEqual(self.move(order, "7", []).status_code, 400)

    def test_rejects_foreign_item(self):
        other = self.create_order("9", lines=1)
        order = self.create_order("5")
        res = self.move(order, "7", [other.items.first().id])
        self.assertEqual(res.status_code, 404)


class RemoveItemCodeTests(OrderFlowBase):
    """Позицию, которую кухня/бар уже готовят, официант убирает только по
    коду из настроек (SiteSettings.item_remove_code). Новую позицию —
    как раньше, свободно.
    """

    def setUp(self):
        super().setUp()
        self.set_mode("hall")
        self.auth(self.waiter)

    def create_order(self):
        res = self.client.post(
            "/api/orders/",
            {"table": "5", "items": [{"product": self.latte.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        return Order.objects.get(pk=res.data["id"])

    def start_item(self, order):
        item = order.items.first()
        item.status = Order.StationStatus.IN_PROGRESS
        item.save(update_fields=["status"])
        return item

    def remove(self, order, item, code=None):
        payload = {"item_id": item.id}
        if code is not None:
            payload["code"] = code
        return self.client.post(f"/api/orders/{order.id}/remove_item/", payload, format="json")

    def test_new_item_removed_without_code(self):
        order = self.create_order()
        item = order.items.first()
        res = self.remove(order, item)
        self.assertEqual(res.status_code, 200)

    def test_in_progress_item_needs_code_when_none_configured(self):
        """Код не задан в настройках — удаление позиции в работе запрещено всем."""
        order = self.create_order()
        item = self.start_item(order)
        res = self.remove(order, item, code="0000")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())

    def test_in_progress_item_rejects_wrong_code(self):
        SiteSettings.load()
        site = SiteSettings.load()
        site.item_remove_code = "4321"
        site.save()
        order = self.create_order()
        item = self.start_item(order)
        res = self.remove(order, item, code="0000")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())

    def test_in_progress_item_removed_with_correct_code(self):
        site = SiteSettings.load()
        site.item_remove_code = "4321"
        site.save()
        order = self.create_order()
        item = self.start_item(order)
        res = self.remove(order, item, code="4321")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(OrderItem.objects.filter(pk=item.pk).exists())

    def test_admin_removes_in_progress_item_without_code(self):
        admin = User.objects.create_user(
            username="boss", password="demo12345", role=User.Role.ADMIN
        )
        order = self.create_order()
        item = self.start_item(order)
        self.auth(admin)
        res = self.remove(order, item)
        self.assertEqual(res.status_code, 200)

class ModifierOrderTests(APITestCase):
    """Опции в заказе: цена с надбавкой и проверки выбора на сервере."""

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        self.product = Product.objects.create(category=cat, name="Латте")
        self.variant = ProductVariant.objects.create(
            product=self.product, price=Decimal("300")
        )
        self.milk = ModifierGroup.objects.create(
            name="Молоко", min_choices=1, max_choices=1
        )
        self.milk.products.add(self.product)
        self.cow = Modifier.objects.create(group=self.milk, name="Коровье")
        self.oat = Modifier.objects.create(
            group=self.milk, name="Овсяное", price_delta=Decimal("60")
        )
        extras = ModifierGroup.objects.create(name="Добавки", max_choices=2)
        extras.products.add(self.product)
        self.shot = Modifier.objects.create(
            group=extras, name="+ шот", price_delta=Decimal("80")
        )
        # опция чужого блюда — её подставлять нельзя
        other = Product.objects.create(category=cat, name="Чай")
        ProductVariant.objects.create(product=other, price=Decimal("200"))
        alien_group = ModifierGroup.objects.create(name="Чайное", max_choices=1)
        alien_group.products.add(other)
        self.alien = Modifier.objects.create(
            group=alien_group, name="Лимон", price_delta=Decimal("-500")
        )

    def _place(self, modifiers, quantity=1):
        return self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "table": "5",
             "items": [{"variant": self.variant.id, "quantity": quantity,
                        "modifiers": modifiers}]},
            format="json",
        )

    def test_price_includes_options(self):
        res = self._place([self.oat.id, self.shot.id])
        self.assertEqual(res.status_code, 201)
        order = Order.objects.latest("id")
        item = order.items.get()
        self.assertEqual(item.unit_price, Decimal("300"))     # цена варианта как была
        self.assertEqual(item.modifiers_total, Decimal("140"))
        self.assertEqual(item.subtotal, Decimal("440"))
        self.assertEqual(order.total, Decimal("440"))

    def test_options_multiply_with_quantity(self):
        self._place([self.oat.id], quantity=3)
        self.assertEqual(Order.objects.latest("id").total, Decimal("1080"))  # (300+60)×3

    def test_required_group_must_be_chosen(self):
        res = self._place([])
        self.assertEqual(res.status_code, 400)
        self.assertIn("Молоко", str(res.data))

    def test_only_one_from_exclusive_group(self):
        res = self._place([self.cow.id, self.oat.id])
        self.assertEqual(res.status_code, 400)

    def test_option_of_another_dish_is_refused(self):
        """Иначе подставленная опция чужого блюда стоила бы заведению денег."""
        res = self._place([self.cow.id, self.alien.id])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_stopped_option_is_refused(self):
        self.oat.is_stopped = True
        self.oat.save(update_fields=["is_stopped"])
        res = self._place([self.oat.id])
        self.assertEqual(res.status_code, 400)

    def test_snapshot_survives_price_change(self):
        self._place([self.cow.id, self.shot.id])
        item = Order.objects.latest("id").items.get()
        self.shot.price_delta = Decimal("120")
        self.shot.name = "+ двойной шот"
        self.shot.save()
        item.refresh_from_db()
        chosen = item.modifiers.get(modifier=self.shot)
        self.assertEqual(chosen.price_delta, Decimal("80"))   # чек не поехал
        self.assertEqual(chosen.name, "+ шот")

    def test_guest_sees_his_options_in_the_order(self):
        self._place([self.oat.id])
        order = Order.objects.latest("id")
        res = self.client.get(f"/api/orders/track/?token={order.public_token}")
        self.assertEqual(res.status_code, 200)
        item = res.data["items"][0]
        self.assertEqual(item["options_text"], "Овсяное")
        self.assertEqual(item["subtotal"], "360.00")

    def test_menu_carries_option_groups(self):
        res = self.client.get("/api/products/")
        card = [p for p in res.data if p["id"] == self.product.id][0]
        groups = {g["name"]: g for g in card["modifier_groups"]}
        self.assertEqual(set(groups), {"Молоко", "Добавки"})
        self.assertTrue(groups["Молоко"]["is_required"])
        self.assertEqual(
            sorted(m["name"] for m in groups["Молоко"]["modifiers"]),
            ["Коровье", "Овсяное"],
        )



class PrepayCounterTests(OrderFlowBase):
    """Стойка с предоплатой: бар не готовит, пока не заплатили.

    Причина простая и денежная: у окна выдачи никто не отвечает за гостя,
    который назаказывал и не пришёл. В зале за стол отвечает официант,
    поэтому там всё должно остаться как было.
    """

    ENV = {"YOOKASSA_SHOP_ID": "100500", "YOOKASSA_SECRET_KEY": "live_AbCd0123456789xyz"}

    def setUp(self):
        super().setUp()
        self.set_mode("counter")
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.YOOKASSA
        site.online_payment_on = True
        site.prepay_required = True
        site.save()
        env = mock.patch.dict("os.environ", self.ENV)
        env.start()
        self.addCleanup(env.stop)

    def pay(self, order, success=True):
        """Банк подтвердил оплату — тем же путём, что и настоящий вебхук."""
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=order.payable,
            order=order,
            method=Payment.Method.CARD,
            provider="yookassa",
            external_id=f"ext-{order.pk}",
        )
        return apply_payment_result(payment, success=success)

    # ——— заказ ждёт денег ———

    def test_order_waits_for_payment_without_a_number(self):
        self.assertEqual(self.place().status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.UNPAID)
        # Номер брошенного заказа был бы потрачен зря: в окно выкрикивали
        # бы 47-й при дюжине проданных.
        self.assertIsNone(order.daily_number)
        self.assertIsNone(order.paid_at)

    def test_unpaid_order_does_not_reach_the_stations(self):
        self.place()
        self.auth(self.waiter)
        self.assertEqual(len(self.client.get("/api/orders/?station=bar").data), 0)
        self.assertEqual(len(self.client.get("/api/orders/?status=open").data), 0)

    def test_barista_still_sees_it_in_its_own_column(self):
        """Иначе гостю с наличными некому отдать деньги."""
        self.place()
        self.auth(self.waiter)
        board = self.client.get("/api/orders/?status=open&with_unpaid=1").data
        self.assertEqual([o["status"] for o in board], ["unpaid"])

    # ——— оплата запускает заказ ———

    def test_payment_sends_the_order_to_work(self):
        self.place()
        order = Order.objects.get()
        self.pay(order)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.daily_number, 1)
        self.assertIsNotNone(order.paid_at)
        # Закрывать рано: заказ ещё готовят и выдают, а закрытие пишет выручку.
        self.assertIsNone(order.closed_at)

    def test_numbers_go_to_those_who_paid(self):
        """Брошенный заказ не должен съедать номер у оплаченного."""
        self.place()
        second = self.place().data["id"]
        self.pay(Order.objects.get(pk=second))
        self.assertEqual(Order.objects.get(pk=second).daily_number, 1)

    def test_cash_at_the_counter_starts_the_order(self):
        self.place()
        order = Order.objects.get()
        self.auth(self.waiter)

        res = self.client.post(f"/api/orders/{order.id}/prepaid/", {"pay_method": "cash"}, format="json")

        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.pay_method, Order.PayMethod.CASH)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_prepaid_order_is_not_charged_twice(self):
        """Выдача уже оплаченного заказа не должна удваивать выручку дня."""
        self.place()
        order = Order.objects.get()
        self.pay(order)
        self.auth(self.waiter)

        self.client.post(f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        # Способ оплаты остаётся тем, которым заплатили вперёд.
        self.assertEqual(order.pay_method, Order.PayMethod.CARD)

    def test_guest_can_drop_an_unpaid_order(self):
        token = self.place().data["public_token"]
        res = self.client.post("/api/orders/cancel_request/", {"token": token}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Order.objects.get().status, Order.Status.CANCELLED)

    # ——— протухание ———

    def test_forgotten_order_is_cancelled(self):
        self.place()
        Order.objects.update(created_at=timezone.now() - timedelta(minutes=16))

        cancel_stale_unpaid_orders_task()

        self.assertEqual(Order.objects.get().status, Order.Status.CANCELLED)

    def test_fresh_order_is_left_alone(self):
        self.place()
        cancel_stale_unpaid_orders_task()
        self.assertEqual(Order.objects.get().status, Order.Status.UNPAID)

    def test_payment_in_the_last_minute_saves_the_order(self):
        """Уведомление могло потеряться — перед отменой спрашиваем банк."""
        self.place()
        order = Order.objects.get()
        Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=order.payable,
            order=order,
            method=Payment.Method.CARD,
            provider="yookassa",
            external_id="ext-late",
        )
        Order.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        Payment.objects.update(updated_at=timezone.now() - timedelta(minutes=16))

        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}):
            cancel_stale_unpaid_orders_task()

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.daily_number, 1)

    def test_bonuses_are_not_spent_after_the_money_is_taken(self):
        """Списание после оплаты разошлось бы с кассой: платёж-то на полную сумму."""
        site = SiteSettings.load()
        site.bonus_enabled = True
        site.bonus_redeem_guest = True
        site.save()
        self.place()
        order = Order.objects.get()
        self.pay(order)

        res = self.client.post(f"/api/orders/{order.id}/bonus/", {"amount": 50}, format="json")

        self.assertEqual(res.status_code, 400)
        self.assertIn("до оплаты", res.data["detail"])

    # ——— где предоплаты быть не должно ———

    def test_hall_is_untouched(self):
        """В зале заявка нужна официанту до оплаты — иначе он не примет гостя."""
        self.set_mode("hall")
        self.place(table="5")
        self.assertEqual(Order.objects.get().status, Order.Status.REQUESTED)

    def test_without_a_working_bank_orders_are_taken_as_before(self):
        """Требовать предоплату, когда платить нечем, — это не работать совсем."""
        site = SiteSettings.load()
        site.online_payment_on = False
        site.save()
        self.place()
        self.assertEqual(Order.objects.get().status, Order.Status.OPEN)

    def test_switch_off_brings_the_old_behaviour_back(self):
        site = SiteSettings.load()
        site.prepay_required = False
        site.save()
        self.place()
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertEqual(order.daily_number, 1)

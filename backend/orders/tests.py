from decimal import Decimal

from rest_framework.test import APITestCase

from catalog.models import Category, Product
from core.models import SiteSettings
from users.models import User

from .models import Order


class OrderFlowBase(APITestCase):
    """Общая обвязка для сценариев заказа: меню, бариста, вход, отправка."""

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=cat, name="Латте", price=Decimal("240"))
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

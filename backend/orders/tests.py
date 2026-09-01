from decimal import Decimal

from rest_framework.test import APITestCase

from catalog.models import Category, Product
from core.models import SiteSettings
from users.models import User

from .models import Order


class CounterModeTests(APITestCase):
    """Стойка: гость заказывает по QR и забирает по номеру.

    Отличия от зала: нет стола, нет подтверждения официантом, заказ сразу
    уходит в работу и получает номер, который обнуляется каждый день.
    """

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

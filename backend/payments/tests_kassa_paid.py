"""«Оплачено» на стойке — если касса сама не подтвердила оплату.

Подстраховка от того, что касса не ответила или назвала оплаченный
заказ не так, как мы ждём: без неё стойка встала бы. Проверяем, что
сначала спрашиваем кассу, руками подтверждаем только лежащий на ней
заказ, и что фоновый опрос после этого не закрывает заказ второй раз.
"""
from decimal import Decimal

from core.models import SiteSettings
from loyalty.services import enroll
from orders.models import Order
from users.models import User

from .journal import day_journal
from .models import Payment
from .services import settle_order
from .tests_kassa import KassaBase


class KassaPaidTests(KassaBase):
    def setUp(self):
        super().setUp()
        self.barista = User.objects.create_user(
            "barista-k", password="Sh4-staff", role=User.Role.WAITER, first_name="Вика"
        )
        self.client.force_authenticate(self.barista)

    def create(self, **extra):
        res = self.client.post(
            "/api/orders/",
            {"items": [{"product": self.latte.id, "quantity": 2}], **extra},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        return Order.objects.get(pk=res.data["id"])

    def paid(self, order, method=None):
        return self.client.post(
            f"/api/orders/{order.pk}/kassa_paid/",
            {"method": method} if method else {},
            format="json",
        )

    def payment(self, order):
        return Payment.objects.filter(order=order).order_by("-created_at").first()

    def test_kassa_confirms_itself(self):
        order = self.create()
        self.aqsi.paid(self.payment(order).external_id, pay_type=0)
        res = self.paid(order)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["confirmed"], "kassa")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertIsNotNone(order.daily_number)
        self.assertIsNone(self.payment(order).confirmed_by)

    def test_kassa_silent_asks_for_method(self):
        order = self.create()
        res = self.paid(order)
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.data["need_method"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.UNPAID)

    def test_manual_confirm_starts_order_and_is_marked(self):
        order = self.create()
        external = self.payment(order).external_id
        res = self.paid(order, "cash")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["confirmed"], "manual")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertIsNotNone(order.daily_number)
        self.assertEqual(order.pay_method, Order.PayMethod.CASH)
        payment = self.payment(order)
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.method, Payment.Method.CASH)
        self.assertEqual(payment.confirmed_by, self.barista)
        # С кассы не снимаем — там он, скорее всего, уже оплачен.
        self.assertIn(external, self.aqsi.orders)

    def test_background_poll_after_manual_does_not_close_twice(self):
        site = SiteSettings.load()
        site.bonus_enabled = True
        site.bonus_earn_percent = Decimal("5")
        site.save()
        guest = User.objects.create_user(
            username="+79990000000", phone="+79990000000", role=User.Role.CLIENT
        )
        member = enroll(guest)  # 200
        order = self.create(phone="+79990000000")
        self.paid(order, "card")
        number = Order.objects.get(pk=order.pk).daily_number
        # касса «проснулась» и тоже говорит «оплачен»
        self.aqsi.paid(self.payment(order).external_id)
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        self.assertEqual(order.daily_number, number)
        self.assertEqual(Payment.objects.filter(order=order, status="succeeded").count(), 1)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("224"))  # 200 + 5% от 480 — один раз

    def test_kassa_unreachable_still_confirmable(self):
        order = self.create()
        self.aqsi.key_ok = False
        self.assertEqual(self.paid(order).status_code, 409)
        res = self.paid(order, "card")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["confirmed"], "manual")

    def test_order_not_on_kassa_is_refused(self):
        """Заказ до кассы не дошёл — «Оплачено» означало бы деньги без чека."""
        self.aqsi.key_ok = False
        order = self.create()  # касса не приняла
        self.assertFalse(Payment.objects.filter(order=order).exists())
        res = self.paid(order, "cash")
        self.assertEqual(res.status_code, 400)
        self.assertIn("не на кассе", res.data["detail"])

    def test_order_deleted_on_kassa_is_refused(self):
        order = self.create()
        self.aqsi.orders.clear()  # кассир удалил заказ у себя
        res = self.paid(order, "cash")
        self.assertEqual(res.status_code, 400)
        order.refresh_from_db()
        self.assertIsNone(order.paid_at)

    def test_bad_method_refused(self):
        order = self.create()
        res = self.paid(order, "bitcoin")
        self.assertEqual(res.status_code, 400)

    def test_guest_cannot_mark_paid(self):
        order = self.create()
        guest = User.objects.create_user("g", password="x", role=User.Role.CLIENT)
        self.client.force_authenticate(guest)
        self.assertEqual(self.paid(order, "cash").status_code, 403)

    def test_journal_shows_who_confirmed(self):
        order = self.create()
        self.paid(order, "cash")
        rows = day_journal(Order.objects.get(pk=order.pk).created_at.date())["rows"]
        row = next(r for r in rows if r["order"] == order.pk)
        self.assertEqual(row["confirmed_by"], "Вика")
        self.assertEqual(row["status"], Payment.Status.SUCCEEDED)

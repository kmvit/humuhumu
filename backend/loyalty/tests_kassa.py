"""Бонусы на стойке с кассой aQsi.

На стойке с кассой заказ уходит на кассу сразу, как бариста его создал,
и сумма там — та, что была в эту секунду. Проверяем, что бонусы попадают
в сумму на кассе: при создании заказа и когда гость вспомнил про них у
окна, — и что пересчёт не оставляет на кассе заказ со старой ценой.
"""
from decimal import Decimal

from core.models import SiteSettings
from datetime import timedelta

from orders.models import Order
from orders.tasks import cancel_stale_unpaid_orders_task
from payments.models import Payment
from payments.services import settle_order
from payments.tests_kassa import KassaBase
from users.models import User

from .models import LoyaltyMember
from .services import enroll

PHONE = "+79990000000"


class BonusKassaBase(KassaBase):
    def setUp(self):
        super().setUp()
        site = SiteSettings.load()
        site.bonus_enabled = True
        site.bonus_welcome = 200
        site.bonus_earn_percent = Decimal("5")
        site.bonus_redeem_waiter = True
        site.save()
        guest = User.objects.create_user(
            username=PHONE, phone=PHONE, first_name="Гость", role=User.Role.CLIENT
        )
        self.member = enroll(guest)  # 200 бонусов
        self.barista = User.objects.create_user(
            "barista-b", password="Sh4-staff", role=User.Role.WAITER
        )
        self.client.force_authenticate(self.barista)

    def create(self, **extra):
        """Заказ на 2 × 240 = 480 ₽."""
        return self.client.post(
            "/api/orders/",
            {"items": [{"product": self.latte.id, "quantity": 2}], **extra},
            format="json",
        )

    def on_kassa(self, order) -> dict:
        payment = Payment.objects.get(order=order, status=Payment.Status.PENDING)
        return self.aqsi.orders[payment.external_id]


class CreateWithBonusesTests(BonusKassaBase):
    def test_bonuses_reach_kassa_sum(self):
        res = self.create(phone="8 999 000-00-00", bonus="150")
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(pk=res.data["id"])
        self.assertEqual(order.payable, Decimal("330"))
        self.assertEqual(order.client_id, self.member.user_id)
        sent = self.on_kassa(order)
        self.assertEqual(sent["content"]["discountMoney"], 150.0)
        self.assertEqual(Payment.objects.get(order=order).amount, Decimal("330"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("50"))

    def test_guest_who_keeps_earns_after_kassa_payment(self):
        res = self.create(phone=PHONE)
        order = Order.objects.get(pk=res.data["id"])
        self.assertEqual(Payment.objects.get(order=order).amount, Decimal("480"))
        self.aqsi.paid(Payment.objects.get(order=order).external_id)
        self.age(order)
        settle_order(order)
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("224"))  # 200 + 5% от 480

    def test_unknown_guest_creates_nothing(self):
        res = self.create(phone="+79995554433", bonus="10")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.aqsi.orders, {})

    def test_too_many_bonuses_creates_nothing(self):
        res = self.create(phone=PHONE, bonus="300")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.aqsi.orders, {})
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("200"))

    def test_whole_bill_in_bonuses_refused(self):
        """Чек на 0 ₽ касса не пробьёт — рубль остаётся деньгами."""
        LoyaltyMember.objects.filter(pk=self.member.pk).update(balance=Decimal("1000"))
        res = self.create(phone=PHONE, bonus="480")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Order.objects.exists())
        res = self.create(phone=PHONE, bonus="479")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Payment.objects.get().amount, Decimal("1"))

    def test_bonus_without_guest_refused(self):
        self.assertEqual(self.create(bonus="10").status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_redeem_switched_off_creates_nothing(self):
        site = SiteSettings.load()
        site.bonus_redeem_waiter = False
        site.save()
        res = self.create(phone=PHONE, bonus="50")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_plain_order_still_works(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["kassa_waiting"])


class RepriceOnKassaTests(BonusKassaBase):
    """Заказ уже на кассе, гость вспомнил про бонусы у окна."""

    def setUp(self):
        super().setUp()
        self.order = Order.objects.get(pk=self.create().data["id"])

    def redeem(self, amount, as_user=None):
        if as_user:
            self.client.force_authenticate(as_user)
        return self.client.post(
            f"/api/orders/{self.order.pk}/bonus/",
            {"phone": PHONE, "amount": str(amount)},
            format="json",
        )

    def test_order_is_resent_with_new_sum(self):
        old = Payment.objects.get(order=self.order).external_id
        res = self.redeem(100)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["resent_to_kassa"])
        self.assertTrue(res.data["order"]["kassa_waiting"])
        # На кассе ровно один заказ — новый, с учётом бонусов.
        self.assertNotIn(old, self.aqsi.orders)
        self.assertEqual(len(self.aqsi.orders), 1)
        sent = self.on_kassa(self.order)
        self.assertEqual(sent["content"]["discountMoney"], 100.0)
        self.assertEqual(
            Payment.objects.get(order=self.order, status=Payment.Status.PENDING).amount,
            Decimal("380"),
        )

    def test_already_paid_on_kassa_is_refused(self):
        self.aqsi.paid(Payment.objects.get(order=self.order).external_id)
        res = self.redeem(100)
        self.assertEqual(res.status_code, 400)
        self.assertIn("оплачен", res.data["detail"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.bonus_spent, Decimal("0"))  # не списано
        # Номер гость назвал — за оплаченный заказ бонусы ему начислены.
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("224"))

    def test_kassa_unreachable_changes_nothing(self):
        """Снять с кассы не вышло — бонусы не списываем: гость мог бы
        оплатить там старую сумму."""
        before = dict(self.aqsi.orders)
        self.aqsi.key_ok = False
        res = self.redeem(100)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.aqsi.orders, before)
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("200"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.bonus_spent, Decimal("0"))
        self.assertTrue(
            Payment.objects.filter(order=self.order, status=Payment.Status.PENDING).exists()
        )

    def test_reprice_to_zero_does_not_touch_kassa(self):
        LoyaltyMember.objects.filter(pk=self.member.pk).update(balance=Decimal("1000"))
        before = dict(self.aqsi.orders)
        res = self.redeem(480)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.aqsi.orders, before)

    def test_impossible_amount_does_not_touch_kassa(self):
        before = dict(self.aqsi.orders)
        res = self.redeem(500)  # на счету 200
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.aqsi.orders, before)

    def test_guest_in_app_cannot_reprice(self):
        guest = LoyaltyMember.objects.get(pk=self.member.pk).user
        site = SiteSettings.load()
        site.bonus_redeem_guest = True
        site.save()
        Order.objects.filter(pk=self.order.pk).update(client=guest)
        res = self.redeem(100, as_user=guest)
        self.assertEqual(res.status_code, 400)
        self.assertIn("на кассе", res.data["detail"])

    def test_paid_after_reprice_counts_discount(self):
        self.redeem(100)
        payment = Payment.objects.get(order=self.order, status=Payment.Status.PENDING)
        self.aqsi.paid(payment.external_id)
        self.age(self.order)
        settle_order(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)
        self.member.refresh_from_db()
        # 200 − 100 + 5% от 380 деньгами
        self.assertEqual(self.member.balance, Decimal("119"))


class ReviewFixesTests(BonusKassaBase):
    """Дыры, найденные проверкой перед выкатом."""

    def test_auto_cancel_returns_bonuses(self):
        """Касса не приняла заказ, через 15 минут он отменился сам —
        списанные при создании бонусы возвращаются гостю."""
        self.aqsi.key_ok = False  # касса недоступна: заказ ждёт без кассы
        res = self.create(phone=PHONE, bonus="150")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(res.data["kassa_waiting"])
        Order.objects.filter(pk=res.data["id"]).update(
            created_at=Order.objects.get(pk=res.data["id"]).created_at - timedelta(hours=1)
        )
        self.aqsi.key_ok = True
        cancel_stale_unpaid_orders_task()
        self.assertEqual(Order.objects.get(pk=res.data["id"]).status, Order.Status.CANCELLED)
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("200"))

    def test_guest_cancel_by_token_returns_bonuses(self):
        res = self.create(phone=PHONE, bonus="150")
        order = Order.objects.get(pk=res.data["id"])
        order.public_token = order.public_token or __import__("uuid").uuid4()
        order.save(update_fields=["public_token"])
        self.client.force_authenticate(None)
        r = self.client.post(
            "/api/orders/cancel_request/", {"token": str(order.public_token)}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("200"))
        # и с кассы снят
        self.assertFalse(
            Payment.objects.filter(order=order, status=Payment.Status.PENDING).exists()
        )

    def test_staff_cannot_switch_guest_after_bonuses(self):
        """Заказ уже с бонусами гостя А — списать на нём бонусы гостя Б нельзя:
        списанное А при отмене ушло бы Б."""
        res = self.create(phone=PHONE, bonus="100")
        other = User.objects.create_user(
            username="+79991110000", phone="+79991110000", first_name="Б",
            role=User.Role.CLIENT,
        )
        enroll(other)
        before = dict(self.aqsi.orders)
        r = self.client.post(
            f"/api/orders/{res.data['id']}/bonus/",
            {"phone": "+79991110000", "amount": "50"}, format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.aqsi.orders, before)  # касса не тронута
        self.assertEqual(Order.objects.get(pk=res.data["id"]).client_id, self.member.user_id)

    def test_non_finite_amount_is_400(self):
        res = self.create()
        for bad in ("NaN", "Infinity", "-Infinity"):
            r = self.client.post(
                f"/api/orders/{res.data['id']}/bonus/",
                {"phone": PHONE, "amount": bad}, format="json",
            )
            self.assertEqual(r.status_code, 400, bad)

    def test_stale_order_object_cannot_double_spend(self):
        """Два списания с одной и той же устаревшей копией заказа (два
        планшета): второе видит первое и не уводит сумму ниже рубля."""
        from .services import LoyaltyError, redeem

        LoyaltyMember.objects.filter(pk=self.member.pk).update(balance=Decimal("1000"))
        res = self.create()
        stale_a = Order.objects.get(pk=res.data["id"])
        stale_b = Order.objects.get(pk=res.data["id"])
        redeem(self.member.pk, Decimal("400"), stale_a)
        with self.assertRaises(LoyaltyError):
            redeem(self.member.pk, Decimal("400"), stale_b)  # осталось 79
        order = Order.objects.get(pk=res.data["id"])
        self.assertEqual(order.bonus_spent, Decimal("400"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("600"))

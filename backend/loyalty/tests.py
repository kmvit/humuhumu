from decimal import Decimal

from rest_framework.test import APITestCase

from catalog.models import Category, Product
from core.models import SiteSettings
from orders.models import Order
from users.models import User

from .models import BonusTransaction, LoyaltyMember
from .services import LoyaltyError, earn_for_order, enroll, redeem


class LoyaltyBase(APITestCase):
    """Обвязка: тариф «Максимум» (без него бонусов нет), меню, официант."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.MAX
        site.service_mode = "hall"
        site.bonus_enabled = True
        site.bonus_welcome = 200
        site.bonus_earn_percent = Decimal("5")
        site.bonus_redeem_waiter = True
        site.bonus_redeem_guest = False
        site.save()
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=cat, name="Латте", price=Decimal("240"))
        self.waiter = User.objects.create_user(
            username="barista", password="demo12345", role=User.Role.WAITER
        )

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def guest(self, phone="+79990000000", name="Гость"):
        user = User.objects.create_user(
            username=phone, password="demo12345", phone=phone,
            first_name=name, role=User.Role.CLIENT,
        )
        return enroll(user)

    def make_order(self, qty=5):
        """Заказ на qty × 240 ₽, заведённый официантом."""
        self.auth(self.waiter)
        res = self.client.post(
            "/api/orders/",
            {"table": "5", "items": [{"product": self.latte.id, "quantity": qty}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        return Order.objects.get(pk=res.data["id"])


class EnrollTests(LoyaltyBase):
    """Регистрация в программе и приветственные бонусы."""

    def test_enroll_gives_welcome_bonus(self):
        member = self.guest()
        self.assertEqual(member.balance, Decimal("200"))
        self.assertEqual(
            member.transactions.get().type, BonusTransaction.Type.WELCOME
        )

    def test_welcome_amount_comes_from_settings(self):
        site = SiteSettings.load()
        site.bonus_welcome = 500
        site.save()
        self.assertEqual(self.guest().balance, Decimal("500"))

    def test_second_enroll_does_not_double_welcome(self):
        """Гость «зарегистрировался» ещё раз — второй раз не начисляем."""
        member = self.guest()
        again = enroll(member.user)
        self.assertEqual(again.pk, member.pk)
        again.refresh_from_db()
        self.assertEqual(again.balance, Decimal("200"))

    def test_enroll_endpoint_collects_name_phone_birthday(self):
        res = self.client.post(
            "/api/loyalty/enroll/",
            {"name": "Аня", "phone": "8 999 111-22-33", "birth_date": "1990-04-01"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        member = LoyaltyMember.objects.get()
        self.assertEqual(member.phone, "+79991112233")  # номер приведён к одному виду
        self.assertEqual(member.name, "Аня")
        self.assertEqual(str(member.birth_date), "1990-04-01")
        self.assertEqual(member.balance, Decimal("200"))

    def test_enroll_blocked_when_program_off(self):
        site = SiteSettings.load()
        site.bonus_enabled = False
        site.save()
        res = self.client.post(
            "/api/loyalty/enroll/", {"name": "Аня", "phone": "+79991112233"}, format="json"
        )
        self.assertEqual(res.status_code, 400)


class RedeemTests(LoyaltyBase):
    """Списание бонусов в счёт заказа."""

    def test_partial_redeem_reduces_payable_not_total(self):
        member = self.guest()
        order = self.make_order(qty=5)  # 1200 ₽
        redeem(member.pk, Decimal("200"), order)
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("1200"))  # чек по меню не меняется
        self.assertEqual(order.bonus_spent, Decimal("200"))
        self.assertEqual(order.payable, Decimal("1000"))

    def test_full_redeem_leaves_nothing_to_pay(self):
        member = self.guest()
        member.balance = Decimal("1200")
        member.save()
        order = self.make_order(qty=5)
        redeem(member.pk, Decimal("1200"), order)
        order.refresh_from_db()
        self.assertEqual(order.payable, Decimal("0"))

    def test_cannot_redeem_more_than_balance(self):
        member = self.guest()  # 200 бонусов
        order = self.make_order(qty=5)
        with self.assertRaises(LoyaltyError):
            redeem(member.pk, Decimal("500"), order)

    def test_cannot_redeem_more_than_order(self):
        """Остаток бонусов не превращается в сдачу."""
        member = self.guest()
        member.balance = Decimal("5000")
        member.save()
        order = self.make_order(qty=1)  # 240 ₽
        with self.assertRaises(LoyaltyError):
            redeem(member.pk, Decimal("500"), order)

    def test_repeated_redeem_respects_remaining_room(self):
        member = self.guest()
        member.balance = Decimal("1000")
        member.save()
        order = self.make_order(qty=1)  # 240 ₽
        redeem(member.pk, Decimal("200"), order)
        with self.assertRaises(LoyaltyError):
            redeem(member.pk, Decimal("100"), order)  # осталось всего 40


class EarnTests(LoyaltyBase):
    """Начисление за оплаченный заказ."""

    def pay(self, order):
        self.auth(self.waiter)
        return self.client.post(
            f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json"
        )

    def attach(self, order, member):
        order.client = member.user
        order.save(update_fields=["client"])

    def test_earns_percent_of_paid_amount(self):
        member = self.guest()
        order = self.make_order(qty=5)  # 1200 ₽
        self.attach(order, member)
        self.pay(order)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))  # 200 + 5% от 1200

    def test_bonus_paid_part_earns_nothing(self):
        """Иначе бонусы подпитывали бы сами себя и баланс не убывал."""
        member = self.guest()
        order = self.make_order(qty=5)  # 1200 ₽
        self.attach(order, member)
        redeem(member.pk, Decimal("200"), order)
        order.refresh_from_db()
        self.pay(order)
        member.refresh_from_db()
        # 200 − 200 списано + 5% от оплаченной 1000 = 50
        self.assertEqual(member.balance, Decimal("50"))

    def test_guest_without_membership_earns_nothing(self):
        order = self.make_order()
        self.pay(order)
        self.assertFalse(BonusTransaction.objects.filter(type="earn").exists())

    def test_no_double_accrual(self):
        member = self.guest()
        order = self.make_order(qty=5)
        self.attach(order, member)
        self.pay(order)
        earn_for_order(order)  # повторный вызов
        self.assertEqual(
            BonusTransaction.objects.filter(order=order, type="earn").count(), 1
        )

    def test_payment_records_money_actually_taken(self):
        from payments.models import Payment

        member = self.guest()
        order = self.make_order(qty=5)
        self.attach(order, member)
        redeem(member.pk, Decimal("200"), order)
        order.refresh_from_db()
        self.pay(order)
        self.assertEqual(Payment.objects.get(order=order).amount, Decimal("1000"))


class RedeemApiTests(LoyaltyBase):
    """Ручка списания: сценарии официанта и гостя включаются раздельно."""

    def redeem_as_waiter(self, order, phone, amount):
        self.auth(self.waiter)
        return self.client.post(
            f"/api/orders/{order.id}/bonus/",
            {"phone": phone, "amount": str(amount)},
            format="json",
        )

    def test_waiter_finds_guest_by_phone_and_redeems(self):
        member = self.guest()
        order = self.make_order(qty=5)
        res = self.redeem_as_waiter(order, "8 999 000-00-00", 200)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["order"]["payable"], "1000.00")
        self.assertEqual(res.data["member"]["balance"], "0.00")
        order.refresh_from_db()
        self.assertEqual(order.client_id, member.user_id)  # заказ закреплён за гостем

    def test_waiter_redeem_blocked_when_switched_off(self):
        site = SiteSettings.load()
        site.bonus_redeem_waiter = False
        site.save()
        self.guest()
        order = self.make_order(qty=5)
        self.assertEqual(self.redeem_as_waiter(order, "+79990000000", 100).status_code, 403)

    def test_unknown_phone_is_404(self):
        order = self.make_order(qty=5)
        self.assertEqual(self.redeem_as_waiter(order, "+79995554433", 100).status_code, 404)

    def test_guest_redeems_own_order_when_allowed(self):
        site = SiteSettings.load()
        site.bonus_redeem_guest = True
        site.save()
        member = self.guest()
        order = self.make_order(qty=5)
        order.client = member.user
        order.save(update_fields=["client"])
        self.auth(member.user)
        res = self.client.post(
            f"/api/orders/{order.id}/bonus/", {"amount": "150"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["order"]["bonus_spent"], "150.00")

    def test_guest_redeem_blocked_when_switched_off(self):
        member = self.guest()
        order = self.make_order(qty=5)
        order.client = member.user
        order.save(update_fields=["client"])
        self.auth(member.user)
        res = self.client.post(
            f"/api/orders/{order.id}/bonus/", {"amount": "150"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_guest_cannot_touch_foreign_order(self):
        site = SiteSettings.load()
        site.bonus_redeem_guest = True
        site.save()
        member = self.guest()
        other = User.objects.create_user(
            username="+79991110000", password="demo12345",
            phone="+79991110000", role=User.Role.CLIENT,
        )
        order = self.make_order(qty=5)
        order.client = other
        order.save(update_fields=["client"])
        self.auth(member.user)
        res = self.client.post(
            f"/api/orders/{order.id}/bonus/", {"amount": "100"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_guest_cannot_claim_anonymous_order(self):
        """Заказ по QR без входа — ничей. Иначе любой вошедший прицепился бы
        к чужому счёту и получал за него начисления."""
        site = SiteSettings.load()
        site.bonus_redeem_guest = True
        site.save()
        member = self.guest()
        order = self.make_order(qty=5)  # client не заполнен
        self.auth(member.user)
        res = self.client.post(
            f"/api/orders/{order.id}/bonus/", {"amount": "100"}, format="json"
        )
        self.assertEqual(res.status_code, 403)
        order.refresh_from_db()
        self.assertIsNone(order.client_id)

    def test_place_attaches_logged_in_guest(self):
        """Чтобы гость мог списать бонусы, заказ по QR должен стать его."""
        member = self.guest()
        self.auth(member.user)
        res = self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "table": "5",
             "items": [{"product": self.latte.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Order.objects.get(pk=res.data["id"]).client_id, member.user_id)

    def test_anonymous_place_stays_ownerless(self):
        res = self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "table": "5",
             "items": [{"product": self.latte.id, "quantity": 1}]},
            format="json",
        )
        self.assertIsNone(Order.objects.get(pk=res.data["id"]).client_id)

    def test_lookup_returns_name_and_balance(self):
        self.guest(name="Аня")
        self.auth(self.waiter)
        res = self.client.get("/api/loyalty/lookup/?phone=89990000000")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["name"], "Аня")
        self.assertEqual(res.data["balance"], "200.00")


class CancelTests(LoyaltyBase):
    """Отменили заказ — списанные бонусы возвращаются гостю."""

    def test_cancel_returns_spent_bonuses(self):
        member = self.guest()
        order = self.make_order(qty=5)
        order.client = member.user
        order.save(update_fields=["client"])
        redeem(member.pk, Decimal("200"), order)
        self.auth(self.waiter)
        res = self.client.patch(f"/api/orders/{order.id}/cancel/", {}, format="json")
        self.assertEqual(res.status_code, 200)
        member.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(member.balance, Decimal("200"))
        self.assertEqual(order.bonus_spent, Decimal("0"))


class GuestPhoneOnOrderTests(LoyaltyBase):
    """Телефон в заказе по QR — единственный способ начислить бонусы гостю,
    который не входил в приложение. Поле необязательное."""

    def place(self, **extra):
        return self.client.post(
            "/api/orders/place/",
            {"customer_name": "Олег", "table": "5",
             "items": [{"product": self.latte.id, "quantity": 5}], **extra},
            format="json",
        )

    def test_phone_enrolls_new_guest_and_links_order(self):
        res = self.place(phone="8 999 777-00-11")
        self.assertEqual(res.status_code, 201)
        member = LoyaltyMember.objects.get(user__phone="+79997770011")
        self.assertEqual(member.balance, Decimal("200"))  # приветственные
        self.assertEqual(member.name, "Олег")
        self.assertEqual(Order.objects.get(pk=res.data["id"]).client_id, member.user_id)

    def test_known_phone_links_without_second_welcome(self):
        member = self.guest(phone="+79997770011", name="Олег")
        self.place(phone="+7 999 777-00-11")
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("200"))
        self.assertEqual(LoyaltyMember.objects.count(), 1)

    def test_order_without_phone_still_works(self):
        res = self.place()
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(Order.objects.get(pk=res.data["id"]).client_id)
        self.assertFalse(LoyaltyMember.objects.exists())

    def test_typo_in_phone_is_reported_not_swallowed(self):
        res = self.place(phone="12")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_phone_ignored_when_program_off(self):
        site = SiteSettings.load()
        site.bonus_enabled = False
        site.save()
        res = self.place(phone="89997770011")
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(Order.objects.get(pk=res.data["id"]).client_id)
        self.assertFalse(LoyaltyMember.objects.exists())

    def test_bonuses_accrue_for_that_order_after_payment(self):
        """Ради этого всё и затевалось: гость по QR наконец что-то копит."""
        res = self.place(phone="89997770011")
        order = Order.objects.get(pk=res.data["id"])  # 5 × 240 = 1200 ₽
        order.status = Order.Status.OPEN
        order.save(update_fields=["status"])
        self.auth(self.waiter)
        self.client.post(f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json")
        member = LoyaltyMember.objects.get(user__phone="+79997770011")
        self.assertEqual(member.balance, Decimal("260"))  # 200 + 5% от 1200


class FinanceTests(LoyaltyBase):
    """Бонусы — скидка за счёт заведения, а не полученные деньги."""

    def test_revenue_counts_money_not_bonuses(self):
        from django.utils import timezone

        from finance.services import report

        member = self.guest()
        order = self.make_order(qty=5)  # 1200 ₽
        order.client = member.user
        order.save(update_fields=["client"])
        redeem(member.pk, Decimal("200"), order)
        order.refresh_from_db()
        self.auth(self.waiter)
        self.client.post(f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json")

        rep = report(timezone.localdate())
        self.assertEqual(rep["revenue"], "1000.00")  # деньгами взяли 1000
        self.assertEqual(rep["bonuses_spent"], "200.00")
        self.assertEqual(rep["cash"], "1000.00")  # касса сходится с выручкой


class PlanGateTests(LoyaltyBase):
    """Бонусы — тариф «Максимум»."""

    def test_start_plan_has_no_loyalty(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.START
        site.save()
        res = self.client.post(
            "/api/loyalty/enroll/", {"name": "Аня", "phone": "+79991112233"}, format="json"
        )
        # аноним получает от DRF 401, сотрудник — 403; важно, что не 201
        self.assertIn(res.status_code, (401, 403))
        self.assertFalse(LoyaltyMember.objects.exists())

    def test_program_endpoint_reports_off_on_start_plan(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.START
        site.save()
        res = self.client.get("/api/loyalty/program/")
        self.assertFalse(res.data["enabled"])

    def test_program_endpoint_reports_terms_on_max(self):
        res = self.client.get("/api/loyalty/program/")
        self.assertTrue(res.data["enabled"])
        self.assertEqual(res.data["welcome"], 200)
        self.assertTrue(res.data["redeem_waiter"])
        self.assertFalse(res.data["redeem_guest"])

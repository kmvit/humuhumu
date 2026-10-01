from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Category, Product, ProductVariant
from core.models import SiteSettings
from orders.models import Order
from users.models import User

from .models import BonusTransaction, LoyaltyMember
from .services import LoyaltyError, earn_for_order, enroll, redeem


class LoyaltyBase(APITestCase):
    """Обвязка: тариф «Максимум» (без него бонусов нет), меню, официант."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.service_mode = "hall"
        site.bonus_enabled = True
        site.bonus_welcome = 200
        site.bonus_earn_percent = Decimal("5")
        site.bonus_redeem_waiter = True
        site.bonus_redeem_guest = False
        site.save()
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=cat, name="Латте")
        ProductVariant.objects.create(product=self.latte, price=Decimal("240"))
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

    def test_at_least_one_ruble_is_paid_in_money(self):
        """Счёт целиком бонусами — чек на 0 ₽, его касса не пробьёт:
        заказ застрял бы неоплаченным. Рубль гость платит деньгами."""
        member = self.guest()
        member.balance = Decimal("1200")
        member.save()
        order = self.make_order(qty=5)  # 1200 ₽
        with self.assertRaises(LoyaltyError):
            redeem(member.pk, Decimal("1200"), order)
        redeem(member.pk, Decimal("1199"), order)
        order.refresh_from_db()
        self.assertEqual(order.payable, Decimal("1"))
        with self.assertRaises(LoyaltyError):
            redeem(member.pk, Decimal("1"), order)  # последний рубль — деньгами

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
            redeem(member.pk, Decimal("100"), order)  # осталось всего 39 (рубль — деньгами)


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


class StaffEnrollTests(LoyaltyBase):
    """Гостя записывает сотрудник — с отметкой о согласии."""

    def enroll(self, **extra):
        return self.client.post(
            "/api/loyalty/enroll/",
            {"name": "Аня", "phone": "+79991112233", **extra},
            format="json",
        )

    def test_staff_needs_guest_consent(self):
        self.auth(self.waiter)
        res = self.enroll()
        self.assertEqual(res.status_code, 400)
        self.assertIn("consent", res.data)
        self.assertFalse(LoyaltyMember.objects.exists())

    def test_staff_enroll_records_source_and_consent(self):
        self.auth(self.waiter)
        res = self.enroll(consent=True)
        self.assertEqual(res.status_code, 201)
        member = LoyaltyMember.objects.get()
        self.assertEqual(member.source, LoyaltyMember.Source.STAFF)
        self.assertIsNotNone(member.consent_at)
        self.assertEqual(member.balance, Decimal("200"))

    def test_guest_self_signup_marked_as_guest(self):
        self.assertEqual(self.enroll().status_code, 201)
        self.assertEqual(LoyaltyMember.objects.get().source, LoyaltyMember.Source.GUEST)


class ManagerMembersTests(LoyaltyBase):
    """Менеджер в панели: список гостей и ручное заведение."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            username="owner", password="demo12345", role=User.Role.ADMIN
        )

    def add(self, **data):
        self.auth(self.admin)
        return self.client.post("/api/loyalty/members/", data, format="json")

    def test_add_new_guest_gets_welcome(self):
        res = self.add(name="Аня", phone="89991112233", consent=True)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["balance"], "200.00")
        self.assertEqual(res.data["source"], "staff")

    def test_transfer_keeps_old_balance_without_welcome(self):
        res = self.add(name="Аня", phone="89991112233", consent=True,
                       birth_date="1990-04-01", transfer_balance="1350")
        self.assertEqual(res.status_code, 201)
        member = LoyaltyMember.objects.get()
        self.assertEqual(member.balance, Decimal("1350"))
        self.assertEqual(member.source, LoyaltyMember.Source.IMPORT)
        txn = member.transactions.get()
        self.assertEqual(txn.type, BonusTransaction.Type.IMPORT)

    def test_transfer_skips_existing_phone(self):
        self.guest(phone="+79991112233")
        res = self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="500")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(LoyaltyMember.objects.get().balance, Decimal("200"))

    def test_consent_required(self):
        self.assertEqual(self.add(name="Аня", phone="+79991112233").status_code, 400)

    def test_search_by_name_and_phone(self):
        self.guest(phone="+79991112233", name="Аня")
        self.guest(phone="+79995556677", name="Борис")
        self.auth(self.admin)
        by_phone = self.client.get("/api/loyalty/members/?q=8 999 111")
        self.assertEqual([m["name"] for m in by_phone.data["results"]], ["Аня"])
        by_name = self.client.get("/api/loyalty/members/?q=бор")
        self.assertEqual(by_name.data["count"], 1)
        # хвост номера, начинающийся с восьмёрки, — не код страны
        self.guest(phone="+79161238455", name="Вера")
        tail = self.client.get("/api/loyalty/members/?q=8455")
        self.assertEqual([m["name"] for m in tail.data["results"]], ["Вера"])

    def test_transfer_zero_balance(self):
        res = self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="0")
        self.assertEqual(res.status_code, 201)
        member = LoyaltyMember.objects.get()
        self.assertEqual(member.balance, Decimal("0"))
        self.assertFalse(member.transactions.exists())

    def test_transfer_negative_refused(self):
        res = self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="-5")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(LoyaltyMember.objects.exists())

    def test_transfer_fraction_rounded_down(self):
        """Бонусы целые: 99.9 из старой системы — 99."""
        self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="99.9")
        self.assertEqual(LoyaltyMember.objects.get().balance, Decimal("99"))

    def test_transfer_when_program_off(self):
        site = SiteSettings.load()
        site.bonus_enabled = False
        site.save()
        res = self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="10")
        self.assertEqual(res.status_code, 400)

    def test_bad_phone_refused(self):
        res = self.add(name="Аня", phone="123", consent=True)
        self.assertEqual(res.status_code, 400)

    def test_transferred_guest_redeems_and_earns_normally(self):
        self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="1000")
        order = self.make_order(qty=5)  # 1200
        self.client.post(
            f"/api/orders/{order.id}/bonus/", {"phone": "+79991112233", "amount": "1000"},
            format="json",
        )
        self.client.post(f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json")
        member = LoyaltyMember.objects.get()
        self.assertEqual(member.balance, Decimal("10"))  # 5% от 200 деньгами

    def test_existing_client_user_without_membership_is_reused(self):
        """Гость заказывал раньше без бонусов — второго пользователя не плодим."""
        User.objects.create_user(
            username="+79991112233", phone="+79991112233", role=User.Role.CLIENT
        )
        res = self.add(name="Аня", phone="+79991112233", consent=True, transfer_balance="50")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(User.objects.filter(phone="+79991112233").count(), 1)

    def test_list_caps_at_50_and_reports_total(self):
        for i in range(55):
            self.guest(phone=f"+7999123{i:04d}", name=f"Гость {i}")
        self.auth(self.admin)
        res = self.client.get("/api/loyalty/members/?q=Гость")
        self.assertEqual(res.data["count"], 55)
        self.assertEqual(len(res.data["results"]), 50)
        only_count = self.client.get("/api/loyalty/members/?limit=0")
        self.assertEqual(only_count.data["count"], 55)
        self.assertEqual(only_count.data["results"], [])

    def test_waiter_has_no_access(self):
        self.auth(self.waiter)
        self.assertEqual(self.client.get("/api/loyalty/members/").status_code, 403)


class AttachGuestTests(LoyaltyBase):
    """Гость назвал телефон, бонусы не тратит — но за чек получает."""

    def attach(self, order, phone="+79990000000"):
        self.auth(self.waiter)
        return self.client.post(
            f"/api/orders/{order.id}/guest/", {"phone": phone}, format="json"
        )

    def close(self, order):
        self.auth(self.waiter)
        return self.client.post(
            f"/api/orders/{order.id}/close/", {"pay_method": "cash"}, format="json"
        )

    def test_attached_guest_earns_on_payment(self):
        member = self.guest()
        order = self.make_order(qty=5)  # 1200 ₽
        res = self.attach(order, "8 999 000-00-00")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["order"]["bonus_guest"]["name"], "Гость")
        self.assertEqual(res.data["member"]["balance"], "200.00")  # ничего не списано
        self.close(order)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))  # 200 + 5% от 1200

    def test_already_paid_order_earns_at_once(self):
        """Стойка: деньги взяли вперёд, номер гость назвал потом."""
        member = self.guest()
        order = self.make_order(qty=5)
        order.paid_at = timezone.now()
        order.save(update_fields=["paid_at"])
        res = self.attach(order)
        self.assertEqual(res.data["earned"], Decimal("60"))
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))
        # оплата закрывает заказ — второго начисления нет
        self.close(order)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))

    def test_repeat_attach_does_not_earn_twice(self):
        member = self.guest()
        order = self.make_order(qty=5)
        order.paid_at = timezone.now()
        order.save(update_fields=["paid_at"])
        self.attach(order)
        self.attach(order)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))

    def test_unknown_phone_is_404(self):
        order = self.make_order()
        self.assertEqual(self.attach(order, "+79995554433").status_code, 404)

    def test_closed_order_cannot_get_guest(self):
        """Иначе сотрудник вписывал бы свой номер в чужие чеки."""
        self.guest()
        order = self.make_order()
        self.close(order)
        self.assertEqual(self.attach(order).status_code, 400)

    def test_guest_with_bonus_history_on_order_is_not_replaced(self):
        self.guest()
        other = self.guest(phone="+79991110000", name="Другой")
        order = self.make_order(qty=5)
        self.auth(self.waiter)
        self.client.post(
            f"/api/orders/{order.id}/bonus/",
            {"phone": "+79990000000", "amount": "100"}, format="json",
        )
        self.assertEqual(self.attach(order, other.phone).status_code, 400)
        order.refresh_from_db()
        self.assertNotEqual(order.client_id, other.user_id)

    def test_full_flow_redeem_half_then_pay(self):
        """Привязали, списали половину, заплатили остальное деньгами —
        начисление только с денежной части."""
        member = self.guest()  # 200
        order = self.make_order(qty=5)  # 1200 ₽
        self.attach(order)
        self.auth(self.waiter)
        self.client.post(
            f"/api/orders/{order.id}/bonus/", {"phone": member.phone, "amount": "100"},
            format="json",
        )
        self.close(order)
        member.refresh_from_db()
        # 200 − 100 + 5% от 1100
        self.assertEqual(member.balance, Decimal("155"))

    def test_switch_guest_before_any_bonus_operation(self):
        first = self.guest()
        second = self.guest(phone="+79991110000", name="Другой")
        order = self.make_order()
        self.attach(order)
        res = self.attach(order, second.phone)
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.client_id, second.user_id)
        self.close(order)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.balance, Decimal("200"))  # первому ничего
        self.assertGreater(second.balance, Decimal("200"))

    def test_cancelled_order_cannot_get_guest(self):
        self.guest()
        order = self.make_order()
        self.auth(self.waiter)
        self.client.patch(f"/api/orders/{order.id}/cancel/", {}, format="json")
        self.assertEqual(self.attach(order).status_code, 400)

    def test_refund_takes_back_bonuses_earned_on_attach(self):
        """Гость назвал телефон, заплатил, потом деньги вернули —
        начисленное за этот заказ снимается."""
        member = self.guest()
        order = self.make_order(qty=5)
        self.attach(order)
        self.close(order)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("260"))
        self.auth(self.waiter)
        res = self.client.post(f"/api/orders/{order.id}/refund/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("200"))

    def test_program_off_refuses_attach(self):
        self.guest()
        order = self.make_order()
        site = SiteSettings.load()
        site.bonus_enabled = False
        site.save()
        self.assertEqual(self.attach(order).status_code, 400)
        order.refresh_from_db()
        self.assertIsNone(order.client_id)

    def test_attach_works_when_staff_redeem_is_off(self):
        """Списание персоналу закрыто — копить гость всё равно может."""
        site = SiteSettings.load()
        site.bonus_redeem_waiter = False
        site.save()
        self.guest()
        order = self.make_order()
        self.assertEqual(self.attach(order).status_code, 200)

    def test_bad_phone_is_400(self):
        order = self.make_order()
        self.assertEqual(self.attach(order, "12").status_code, 400)

    def test_order_payload_shows_guest_on_board(self):
        self.guest()
        order = self.make_order()
        self.attach(order)
        self.auth(self.waiter)
        row = next(o for o in self.client.get("/api/orders/?status=open").data if o["id"] == order.id)
        self.assertEqual(row["bonus_guest"]["phone"], "+79990000000")

    def test_guest_cannot_attach(self):
        self.guest()
        order = self.make_order()
        guest = User.objects.get(phone="+79990000000")
        self.auth(guest)
        res = self.client.post(
            f"/api/orders/{order.id}/guest/", {"phone": "+79990000000"}, format="json"
        )
        self.assertEqual(res.status_code, 403)


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
    """Бонусы входят в оба тарифа — включает их сам владелец.

    Раньше это была фича «Максимума». Сетку перекроили: тариф означает
    формат заведения, а не набор функций, и кофейне на «Стойке» бонусы
    нужны ничуть не меньше. Единственный выключатель теперь — bonus_enabled.
    """

    def test_counter_plan_has_loyalty_too(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.COUNTER
        site.save()
        res = self.client.get("/api/loyalty/program/")
        self.assertTrue(res.data["enabled"])

    def test_program_endpoint_reports_off_when_owner_turned_bonuses_off(self):
        site = SiteSettings.load()
        site.bonus_enabled = False
        site.save()
        res = self.client.get("/api/loyalty/program/")
        self.assertFalse(res.data["enabled"])

    def test_program_endpoint_reports_terms(self):
        res = self.client.get("/api/loyalty/program/")
        self.assertTrue(res.data["enabled"])
        self.assertEqual(res.data["welcome"], 200)
        self.assertTrue(res.data["redeem_waiter"])
        self.assertFalse(res.data["redeem_guest"])

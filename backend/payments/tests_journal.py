"""Журнал платежей владельца и канал оплаты заказа.

Главное, что проверяем: карта онлайн и карта на кассе не смешиваются,
возврат вычитается, а наличные мимо банка остаются наличными.
"""
from decimal import Decimal

from django.contrib.admin.sites import site as admin_site
from rest_framework.test import APITestCase

from orders.models import Order
from users.models import User

from .models import AcquiringCredentials, Payment


def pay(order, amount, *, provider, method, status=Payment.Status.SUCCEEDED,
        purpose=Payment.Purpose.ORDER, fiscal=""):
    return Payment.objects.create(
        purpose=purpose, status=status, amount=Decimal(amount), order=order,
        method=method, provider=provider, fiscal_receipt=fiscal,
    )


class PaymentJournalTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner-j", password="Sh4-owner", role=User.Role.ADMIN)
        self.client.force_authenticate(self.owner)
        self.cash = Order.objects.create(status=Order.Status.PAID, total=Decimal("200"))
        self.kassa = Order.objects.create(status=Order.Status.PAID, total=Decimal("300"))
        self.online = Order.objects.create(status=Order.Status.PAID, total=Decimal("450"))
        pay(self.cash, "200", provider="manual", method="cash")
        pay(self.kassa, "300", provider="aqsi", method="card", fiscal="ФД 7, ФП 1")
        pay(self.online, "450", provider="yookassa", method="card",
            status=Payment.Status.REFUNDED)
        pay(self.online, "450", provider="yookassa", method="card",
            purpose=Payment.Purpose.REFUND)
        # Снятый с кассы — денег нет, в итоги не идёт, но в журнале виден.
        pay(self.kassa, "300", provider="aqsi", method="card", status=Payment.Status.CANCELLED)

    def test_totals_split_card_by_channel(self):
        response = self.client.get("/api/payments/journal/")
        self.assertEqual(response.status_code, 200, response.data)
        t = response.data["totals"]
        self.assertEqual(t["cash"], Decimal("200"))
        self.assertEqual(t["card_kassa"], Decimal("300"))
        self.assertEqual(t["online"], Decimal("450"))
        self.assertEqual(t["refunds"], Decimal("450"))
        self.assertEqual(t["income"], Decimal("950"))
        self.assertEqual(t["net"], Decimal("500"))

    def test_rows_say_how_it_was_paid(self):
        rows = self.client.get("/api/payments/journal/").data["rows"]
        self.assertEqual(len(rows), 5)
        kassa = next(r for r in rows if r["fiscal_receipt"])
        self.assertEqual(kassa["channel"], "kassa")
        self.assertEqual(kassa["provider_display"], "aQsi (смарт-касса Т-Банка)")
        self.assertEqual(kassa["order"], self.kassa.pk)
        refund = next(r for r in rows if r["refund"])
        self.assertEqual(refund["channel"], "online")

    def test_orders_of_the_day_with_payment(self):
        orders = {o["id"]: o for o in self.client.get("/api/payments/journal/").data["orders"]}
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[self.kassa.pk]["channel"], "kassa")
        self.assertEqual(orders[self.kassa.pk]["method"], "card")
        self.assertEqual(orders[self.cash.pk]["method_display"], "Наличные")
        self.assertEqual(orders[self.online.pk]["channel_display"], "Онлайн")

    def test_other_day_is_empty(self):
        data = self.client.get("/api/payments/journal/?date=2020-01-01").data
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["totals"]["net"], Decimal("0"))

    def test_bad_date(self):
        self.assertEqual(self.client.get("/api/payments/journal/?date=вчера").status_code, 400)

    def test_staff_cannot_see_revenue(self):
        barista = User.objects.create_user("barista-j", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        self.assertEqual(self.client.get("/api/payments/journal/").status_code, 403)

    def test_order_knows_its_channel(self):
        for order in (self.cash, self.kassa, self.online):
            Order.objects.filter(pk=order.pk).update(paid_at=order.created_at)
        rows = {o["id"]: o for o in self.client.get("/api/orders/").data}
        self.assertEqual(rows[self.cash.pk]["pay_channel"], "manual")
        self.assertEqual(rows[self.kassa.pk]["pay_channel"], "kassa")
        self.assertEqual(rows[self.online.pk]["pay_channel"], "online")


class CredentialsAdminTests(APITestCase):
    def test_kassa_key_is_shown_as_filled(self):
        """Поддержка видит, что ключ кассы задан, а не «пусто»."""
        row = AcquiringCredentials(provider="aqsi")
        row.set_values({"api_key": "aqsi-key-0123456789abcdef"})
        row.save()
        admin = admin_site._registry[AcquiringCredentials]
        self.assertEqual(admin.filled(row), "API-ключ")

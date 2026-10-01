"""Тесты оплаты через кассу aQsi.

Сеть не трогаем: подменяется httpx.request в providers.py, а подмена
отвечает как кабинет aQsi — по методу и пути. Проверяем то, что ломается
молча и дорого: заказ уходит на кассу с правильной суммой, оплата на
кассе пускает заказ в работу ровно один раз, а заказ, оплаченный или
отменённый мимо кассы, с неё снимается — иначе кассир возьмёт деньги
второй раз.
"""
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import httpx
from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Category, Modifier, ModifierGroup, Product, ProductVariant
from core.models import SiteSettings
from orders.models import Order, OrderItemModifier
from users.models import User

from .models import AcquiringCredentials, Payment
from .providers import AqsiProvider
from .services import settle_order

KEY = "aqsi-key-0123456789abcdef"


class FakeAqsi:
    """Кабинет aQsi в памяти: магазины, отложенные заказы и их статусы."""

    def __init__(self):
        self.shops = [{"id": "shop-1", "name": "Монти"}]
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.key_ok = True

    def paid(self, order_id, pay_type=1):
        self.orders[order_id]["status"] = "Оплачен"
        self.orders[order_id]["receipts"] = [{
            "documentNumber": 117,
            "fp": "3522713744",
            "content": {"type": 1, "checkClose": {"payments": [{"type": pay_type, "amount": 1}]}},
        }]

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("/pub", 1)[1]
        self.calls.append((method, path))
        request = httpx.Request(method, url)
        if headers.get("x-client-key") != f"Application {KEY}" or not self.key_ok:
            return httpx.Response(401, request=request)
        if method == "GET" and path == "/v2/Shops/list":
            return httpx.Response(200, json=self.shops, request=request)
        if method == "POST" and path == "/v2/Orders/simple":
            self.orders[json["id"]] = {**json, "status": "Отложен", "receipts": []}
            return httpx.Response(201, json={"guid": json["id"]}, request=request)
        order_id = path.rsplit("/", 1)[1]
        if method == "GET":
            if order_id not in self.orders:
                # Так отвечает боевой кабинет aQsi (проверено 30.09.2026).
                return httpx.Response(412, json={
                    "message": "Запрос содержит некорректные параметры",
                    "errors": ["Заказ не найден"],
                }, request=request)
            return httpx.Response(200, json=self.orders[order_id], request=request)
        if method == "DELETE":
            self.orders.pop(order_id, None)
            return httpx.Response(204, request=request)
        return httpx.Response(404, request=request)


class KassaBase(APITestCase):
    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=cat, name="Латте")
        self.variant = ProductVariant.objects.create(product=self.latte, price=Decimal("240"))

        site = SiteSettings.load()
        site.service_mode = SiteSettings.ServiceMode.COUNTER
        site.prepay_required = True
        site.kassa = SiteSettings.Kassa.AQSI
        site.save()
        row = AcquiringCredentials(provider="aqsi")
        row.set_values({"api_key": KEY})
        row.save()

        self.aqsi = FakeAqsi()
        patcher = mock.patch("payments.providers.httpx.request", side_effect=self.aqsi)
        patcher.start()
        self.addCleanup(patcher.stop)

    def place(self):
        """Гость оформляет заказ по QR — на стойке с кассой он ждёт оплаты."""
        response = self.client.post(
            "/api/orders/place/",
            {"customer_name": "Аня", "items": [{"product": self.latte.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(pk=response.data["id"])

    def to_kassa(self, order):
        return self.client.post(
            "/api/orders/pay_at_kassa/", {"token": str(order.public_token)}, format="json"
        )

    @staticmethod
    def age(order):
        """Пауза между опросами кассы прошла."""
        Payment.objects.filter(order=order).update(
            updated_at=timezone.now() - timedelta(minutes=1)
        )

    def kassa_order(self, order) -> dict:
        payment = Payment.objects.get(order=order, provider="aqsi")
        return self.aqsi.orders[payment.external_id]


class PayAtKassaTests(KassaBase):
    def test_prepay_works_with_kassa_alone(self):
        """Без онлайн-оплаты предоплата всё равно включается: платить есть где."""
        order = self.place()
        self.assertEqual(order.status, Order.Status.UNPAID)
        self.assertTrue(self.client.get("/api/site/").data["kassa_payment"])

    def test_order_goes_to_kassa_with_positions(self):
        order = self.place()
        response = self.to_kassa(order)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["kassa_waiting"])
        # Заказ на кассе ждёт оплаты, но готовить его ещё рано.
        self.assertEqual(response.data["status"], Order.Status.UNPAID)

        sent = self.kassa_order(order)
        self.assertEqual(sent["number"], str(order.pk))
        self.assertEqual(sent["shop"], "shop-1")
        self.assertFalse(sent["isEditableByDevice"])
        [position] = sent["content"]["positions"]
        self.assertEqual(position["text"], "Латте")
        self.assertEqual(position["quantity"], 2)
        self.assertEqual(position["price"], 240.0)
        self.assertEqual(position["tax"], 6)

    def test_price_includes_options_and_bonuses_are_a_discount(self):
        order = self.place()
        milk = ModifierGroup.objects.create(name="Молоко")
        oat = Modifier.objects.create(group=milk, name="Овсяное", price_delta=Decimal("50"))
        item = order.items.get()
        OrderItemModifier.objects.create(order_item=item, modifier=oat, name="Овсяное",
                                         price_delta=Decimal("50"))
        order.recalc_total()
        order.bonus_spent = Decimal("100")
        order.save()

        self.assertEqual(self.to_kassa(order).status_code, 200)
        sent = self.kassa_order(order)
        [position] = sent["content"]["positions"]
        self.assertEqual(position["text"], "Латте (Овсяное)")
        self.assertEqual(position["price"], 290.0)
        self.assertEqual(sent["content"]["discountMoney"], 100.0)
        # Сумма на кассе = то, что гость должен деньгами.
        total = position["price"] * position["quantity"] - sent["content"]["discountMoney"]
        self.assertEqual(Decimal(str(total)), order.payable)

    def test_second_tap_does_not_duplicate(self):
        order = self.place()
        self.to_kassa(order)
        self.to_kassa(order)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertEqual(len(self.aqsi.orders), 1)

    def test_changed_sum_replaces_order_on_kassa(self):
        """Сумма изменилась — прежний заказ снимаем, иначе кассир возьмёт старую."""
        order = self.place()
        self.to_kassa(order)
        order.bonus_spent = Decimal("40")
        order.save()
        self.to_kassa(order)
        self.assertEqual(len(self.aqsi.orders), 1)
        live = Payment.objects.get(order=order, status=Payment.Status.PENDING)
        self.assertEqual(live.amount, Decimal("440"))

    def test_kassa_unavailable_is_honest(self):
        site = SiteSettings.load()
        site.kassa = SiteSettings.Kassa.NONE
        site.save()
        order = self.place()
        response = self.to_kassa(order)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(order=order).exists())

    def test_kassa_error_leaves_no_pending_payment(self):
        order = self.place()
        self.aqsi.key_ok = False
        response = self.to_kassa(order)
        self.assertEqual(response.status_code, 400)
        self.assertIn("API-ключ", response.data["detail"])
        self.assertFalse(Payment.objects.filter(order=order).exists())


class KassaSettleTests(KassaBase):
    def test_paid_at_kassa_starts_order_once(self):
        order = self.place()
        self.to_kassa(order)
        payment = Payment.objects.get(order=order)
        self.aqsi.paid(payment.external_id, pay_type=0)
        self.age(order)

        settle_order(Order.objects.get(pk=order.pk))
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)  # в работе
        self.assertIsNotNone(order.daily_number)
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.pay_method, Order.PayMethod.CASH)
        self.assertEqual(payment.method, Payment.Method.CASH)
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(order.fiscal_receipt, "ФД 117, ФП 3522713744")

        # Повторный опрос ничего не переписывает.
        paid_at = order.paid_at
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        self.assertEqual(order.paid_at, paid_at)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_barista_board_polls_kassa(self):
        """Доска баристы сама узнаёт об оплате на кассе."""
        order = self.place()
        self.to_kassa(order)
        payment = Payment.objects.get(order=order)
        self.aqsi.paid(payment.external_id)
        self.age(order)

        barista = User.objects.create_user("barista-k", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        response = self.client.get("/api/orders/?status=open&with_unpaid=1")
        self.assertEqual(response.status_code, 200)
        row = next(o for o in response.data if o["id"] == order.pk)
        self.assertEqual(row["status"], Order.Status.OPEN)
        self.assertFalse(row["kassa_waiting"])
        self.assertEqual(row["pay_method"], Order.PayMethod.CARD)

    def test_manual_prepaid_is_refused_with_kassa(self):
        """При кассе оплата только через неё: ручной отметки нет вовсе."""
        order = self.place()
        self.to_kassa(order)
        barista = User.objects.create_user("barista-m", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        response = self.client.post(f"/api/orders/{order.pk}/prepaid/", {"pay_method": "cash"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("только через кассу", response.data["detail"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.UNPAID)
        self.assertFalse(Payment.objects.filter(order=order, status="succeeded").exists())
        # Заказ на кассе не тронут — его по-прежнему можно там оплатить.
        self.assertEqual(len(self.aqsi.orders), 1)

    def test_stale_order_on_kassa_is_not_cancelled(self):
        """Заказ на кассе ждёт сколько угодно: касса могла лечь, гость в очереди."""
        from orders.tasks import cancel_stale_unpaid_orders_task

        waiting, forgotten = self.place(), self.place()
        self.to_kassa(waiting)
        Order.objects.filter(pk__in=[waiting.pk, forgotten.pk]).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        cancel_stale_unpaid_orders_task()
        waiting.refresh_from_db()
        forgotten.refresh_from_db()
        self.assertEqual(waiting.status, Order.Status.UNPAID)
        self.assertEqual(len(self.aqsi.orders), 1)
        self.assertEqual(forgotten.status, Order.Status.CANCELLED)

    def test_barista_cancels_order_on_kassa(self):
        """Гость ушёл — бариста отменяет, заказ снимается и с кассы."""
        order = self.place()
        self.to_kassa(order)
        barista = User.objects.create_user("barista-c", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        response = self.client.patch(f"/api/orders/{order.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Order.Status.CANCELLED)
        self.assertEqual(self.aqsi.orders, {})

    def test_guest_cancel_removes_order_from_kassa(self):
        order = self.place()
        self.to_kassa(order)
        response = self.client.post(
            "/api/orders/cancel_request/", {"token": str(order.public_token)}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.aqsi.orders, {})

    def test_cancelled_on_kassa_keeps_order_waiting(self):
        """Кассир снял заказ — гость может заплатить иначе, заказ не теряется."""
        order = self.place()
        self.to_kassa(order)
        payment = Payment.objects.get(order=order)
        self.aqsi.orders[payment.external_id]["status"] = "Отменен"
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(order.status, Order.Status.UNPAID)

    def test_order_deleted_on_kassa_stops_waiting(self):
        """Кассир удалил заказ у себя — у нас он перестаёт быть «на кассе»."""
        order = self.place()
        self.to_kassa(order)
        self.aqsi.orders.clear()
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        self.assertEqual(Payment.objects.get(order=order).status, Payment.Status.CANCELLED)
        self.assertEqual(order.status, Order.Status.UNPAID)

    def test_refund_of_kassa_payment_does_not_call_bank(self):
        order = self.place()
        self.to_kassa(order)
        self.aqsi.paid(Payment.objects.get(order=order).external_id)
        self.age(order)
        settle_order(order)
        barista = User.objects.create_user("barista-r", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        response = self.client.post(f"/api/orders/{order.pk}/refund/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDED)


class KassaSettingsApiTests(KassaBase):
    def setUp(self):
        super().setUp()
        site = SiteSettings.load()
        site.kassa = SiteSettings.Kassa.NONE
        site.save()
        AcquiringCredentials.objects.filter(provider="aqsi").delete()
        self.owner = User.objects.create_user("owner-k", password="Sh4-owner", role=User.Role.ADMIN)
        self.client.force_authenticate(self.owner)

    def test_owner_connects_kassa_without_seeing_key(self):
        response = self.client.put(
            "/api/kassa/", {"provider": "aqsi", "values": {"api_key": KEY, "vat": "7"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["ready"])
        self.assertEqual(response.data["filled"]["api_key"], True)
        self.assertEqual(response.data["values"]["vat"], "7")
        self.assertNotIn(KEY, json.dumps(response.data))
        self.assertEqual(SiteSettings.load().kassa, "aqsi")

    def test_wrong_key_is_not_saved(self):
        self.aqsi.key_ok = False
        response = self.client.put(
            "/api/kassa/", {"provider": "aqsi", "values": {"api_key": KEY}}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AcquiringCredentials.objects.filter(provider="aqsi").exists())
        self.assertEqual(SiteSettings.load().kassa, "none")

    def test_several_shops_need_a_choice(self):
        self.aqsi.shops.append({"id": "shop-2", "name": "Второй зал"})
        response = self.client.put(
            "/api/kassa/", {"provider": "aqsi", "values": {"api_key": KEY}}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("несколько магазинов", response.data["detail"])
        response = self.client.put(
            "/api/kassa/",
            {"provider": "aqsi", "values": {"api_key": KEY, "shop_id": "Второй зал"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_staff_cannot_touch_kassa(self):
        barista = User.objects.create_user("barista-s", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(barista)
        self.assertEqual(self.client.get("/api/kassa/").status_code, 403)

    def test_wipe_disconnects(self):
        self.client.put(
            "/api/kassa/", {"provider": "aqsi", "values": {"api_key": KEY}}, format="json"
        )
        response = self.client.delete("/api/kassa/")
        self.assertEqual(response.data["provider"], "none")
        self.assertFalse(AcquiringCredentials.objects.filter(provider="aqsi").exists())


class AqsiReceiptParsingTests(APITestCase):
    def test_mixed_payment_counts_as_card(self):
        method, _ = AqsiProvider._from_receipts([
            {"content": {"type": 1, "checkClose": {"payments": [{"type": 0}, {"type": 1}]}}}
        ])
        self.assertEqual(method, "card")

    def test_order_id_is_stable(self):
        p = Payment(pk=42)
        self.assertEqual(AqsiProvider().order_id(p), AqsiProvider().order_id(p))
        uuid.UUID(AqsiProvider().order_id(p))


class BaristaOrderKassaTests(KassaBase):
    """Заказ, принятый баристой на словах, при кассе тоже оплачивается на ней."""

    def setUp(self):
        super().setUp()
        self.barista = User.objects.create_user("barista-o", password="Sh4-staff", role=User.Role.WAITER)
        self.client.force_authenticate(self.barista)

    def create(self):
        return self.client.post(
            "/api/orders/", {"items": [{"product": self.latte.id, "quantity": 1}]}, format="json"
        )

    def test_barista_order_goes_to_kassa(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Order.Status.UNPAID)
        self.assertTrue(response.data["kassa_waiting"])
        # Номер выдачи — только после оплаты, как у заказа с сайта.
        self.assertIsNone(response.data["daily_number"])
        self.assertEqual(len(self.aqsi.orders), 1)

    def test_paid_at_kassa_goes_to_work(self):
        order = Order.objects.get(pk=self.create().data["id"])
        self.aqsi.paid(Payment.objects.get(order=order).external_id, pay_type=0)
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OPEN)
        self.assertIsNotNone(order.daily_number)
        self.assertEqual(order.pay_method, Order.PayMethod.CASH)

    def test_kassa_down_keeps_order_waiting(self):
        self.aqsi.key_ok = False
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Order.Status.UNPAID)
        self.assertFalse(response.data["kassa_waiting"])
        self.aqsi.key_ok = True
        again = self.client.post(f"/api/orders/{response.data['id']}/pay_terminal/", {})
        self.assertEqual(again.status_code, 200, again.data)
        self.assertTrue(again.data["kassa_waiting"])

    def test_hand_out_without_kassa_payment_is_refused(self):
        """«Выдал, взял картой» без кассы при кассе запрещено."""
        order = Order.objects.create(status=Order.Status.OPEN, total=Decimal("240"))
        order.items.create(variant=self.variant, quantity=1, unit_price=self.variant.price)
        response = self.client.post(f"/api/orders/{order.pk}/close/", {"pay_method": "card"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("через кассу", response.data["detail"])
        self.assertFalse(Payment.objects.filter(order=order).exists())

    def test_hand_out_of_paid_order_works(self):
        order = Order.objects.get(pk=self.create().data["id"])
        self.aqsi.paid(Payment.objects.get(order=order).external_id)
        self.age(order)
        settle_order(order)
        response = self.client.post(f"/api/orders/{order.pk}/close/", {"pay_method": "card"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Order.Status.PAID)
        self.assertEqual(Payment.objects.filter(order=order, status="succeeded").count(), 1)

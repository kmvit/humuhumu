"""Тесты интернет-эквайринга.

Сеть не трогаем: у драйверов подменяется единственный метод _post, который
ходит в банк. Проверяем то, что ломается молча и дорого — подпись,
повторные уведомления и включение оплаты без доступов.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from rest_framework.test import APITestCase

from catalog.models import Category, Product
from core.models import SiteSettings
from orders.models import Order

from .acquiring import SberAcquirer, TBankAcquirer, get_acquirer
from .models import Payment
from .services import apply_payment_result


class AcquirerChoiceTests(TestCase):
    """Выбор провайдера — настройка заведения, а доступы — окружение."""

    def test_no_acquiring_by_default(self):
        self.assertEqual(get_acquirer().name, "none")
        self.assertFalse(get_acquirer().configured())

    def test_choice_comes_from_site_settings(self):
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.TBANK
        site.save()
        self.assertEqual(get_acquirer().name, "tbank")

    @mock.patch.dict("os.environ", {"TBANK_TERMINAL_KEY": "", "TBANK_PASSWORD": ""})
    def test_chosen_but_not_configured_is_not_available(self):
        """Выбрали банк, но не завели ключи — оплату не включаем.

        Иначе гость нажмёт «оплатить» и упрётся в ошибку банка.
        """
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.TBANK
        site.save()
        self.assertFalse(get_acquirer().configured())


class TBankSignatureTests(TestCase):
    """Подпись Т-Кассы — единственное, что отличает банк от подделки."""

    def setUp(self):
        patcher = mock.patch.dict(
            "os.environ", {"TBANK_TERMINAL_KEY": "term-1", "TBANK_PASSWORD": "secret"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acq = TBankAcquirer()

    def test_signature_roundtrip(self):
        payload = {
            "TerminalKey": "term-1",
            "OrderId": "7",
            "Success": True,
            "Status": "CONFIRMED",
            "PaymentId": "999",
            "Amount": 12000,
        }
        payload["Token"] = self.acq._token(payload)

        result = self.acq.read_callback(payload, {})
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.external_id, "999")

    def test_forged_notification_rejected(self):
        payload = {
            "TerminalKey": "term-1",
            "PaymentId": "999",
            "Status": "CONFIRMED",
            "Token": "подделка",
        }
        self.assertIsNone(self.acq.read_callback(payload, {}))

    def test_foreign_terminal_rejected(self):
        """Подпись верная, но терминал чужой — платёж не наш."""
        payload = {"TerminalKey": "term-999", "PaymentId": "1", "Status": "CONFIRMED"}
        payload["Token"] = self.acq._token(payload)
        self.assertIsNone(self.acq.read_callback(payload, {}))

    def test_authorized_is_not_paid_yet(self):
        """AUTHORIZED — деньги захолдированы, но не списаны."""
        payload = {"TerminalKey": "term-1", "PaymentId": "5", "Status": "AUTHORIZED"}
        payload["Token"] = self.acq._token(payload)
        result = self.acq.read_callback(payload, {})
        self.assertFalse(result.success)
        self.assertTrue(result.pending)


class SberCallbackTests(TestCase):
    """Сберу на слово не верим: статус спрашиваем сами."""

    def setUp(self):
        patcher = mock.patch.dict(
            "os.environ", {"SBER_USERNAME": "user", "SBER_PASSWORD": "pass"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acq = SberAcquirer()

    def test_callback_does_not_trust_payload(self):
        """В уведомлении «оплачено», а у банка — нет. Верим банку."""
        with mock.patch.object(self.acq, "_post", return_value={"orderStatus": 0}) as post:
            result = self.acq.read_callback(
                {"mdOrder": "abc", "status": "1", "orderStatus": 2}, {}
            )
        self.assertFalse(result.success)
        self.assertTrue(result.pending)
        self.assertIn("getOrderStatusExtended", post.call_args[0][0])

    def test_paid_status_recognised(self):
        with mock.patch.object(self.acq, "_post", return_value={"orderStatus": 2}):
            result = self.acq.read_callback({"mdOrder": "abc"}, {})
        self.assertTrue(result.success)


class CallbackEndpointTests(APITestCase):
    """Ручка уведомлений: заказ закрывается, повтор ничего не ломает."""

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        product = Product.objects.create(category=cat, name="Латте", price=Decimal("240"))
        self.order = Order.objects.create(status=Order.Status.OPEN, total=Decimal("240"))
        self.order.items.create(product=product, quantity=1, unit_price=product.price)
        self.payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal("240"),
            order=self.order,
            method=Payment.Method.CARD,
            provider="tbank",
            external_id="999",
        )
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.TBANK
        site.save()
        patcher = mock.patch.dict(
            "os.environ", {"TBANK_TERMINAL_KEY": "term-1", "TBANK_PASSWORD": "secret"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def notify(self, status="CONFIRMED"):
        acq = TBankAcquirer()
        payload = {
            "TerminalKey": "term-1",
            "PaymentId": "999",
            "OrderId": str(self.payment.pk),
            "Status": status,
            "Amount": 24000,
        }
        payload["Token"] = acq._token(payload)
        return self.client.post(
            "/api/payments/callback/tbank/", payload, format="json"
        )

    def test_notification_closes_order(self):
        self.assertEqual(self.notify().status_code, 200)
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.pay_method, Order.PayMethod.CARD)
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_repeat_notification_does_not_reclose(self):
        """Банк повторяет уведомления. Второе не должно переписывать закрытие."""
        self.notify()
        self.order.refresh_from_db()
        closed_at = self.order.closed_at

        self.assertEqual(self.notify().status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.closed_at, closed_at)

    def test_forged_notification_leaves_order_open(self):
        response = self.client.post(
            "/api/payments/callback/tbank/",
            {"TerminalKey": "term-1", "PaymentId": "999", "Status": "CONFIRMED", "Token": "нет"},
            format="json",
        )
        # 200, чтобы банк не долбил повторами, но заказ не тронут
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)

    def test_unknown_provider_is_harmless(self):
        response = self.client.post(
            "/api/payments/callback/кто-то/", {"foo": "bar"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)


class ManualPaymentUnaffectedTests(TestCase):
    """Наличные и карта на месте работают как работали."""

    def test_apply_result_still_closes_order(self):
        order = Order.objects.create(status=Order.Status.AWAITING, total=Decimal("100"))
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal("100"),
            order=order,
            method=Payment.Method.CASH,
        )
        apply_payment_result(payment, success=True)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.pay_method, Order.PayMethod.CASH)

    def test_default_provider_is_manual(self):
        """Умолчание больше не врёт про несуществующую ЮKassa."""
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal("1"),
        )
        self.assertEqual(payment.provider, "manual")

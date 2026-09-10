"""Тесты интернет-эквайринга.

Сеть не трогаем: у драйверов подменяется единственный метод _post, который
ходит в банк. Проверяем то, что ломается молча и дорого — подпись,
повторные уведомления и включение оплаты без доступов.
"""
from decimal import Decimal
from unittest import mock

import httpx

from django.test import TestCase
from rest_framework.test import APITestCase

from catalog.models import Category, Product
from core.models import SiteSettings
from orders.models import Order

from .acquiring import AcquiringError, SberAcquirer, TBankAcquirer, YooKassaAcquirer, get_acquirer
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


class YooKassaTests(TestCase):
    """ЮKassa: сумма в рублях, ключ идемпотентности, уведомление без подписи."""

    def setUp(self):
        patcher = mock.patch.dict(
            "os.environ", {"YOOKASSA_SHOP_ID": "shop-1", "YOOKASSA_SECRET_KEY": "secret"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acq = YooKassaAcquirer()

    def payment(self, amount="240"):
        return Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal(amount),
            method=Payment.Method.CARD,
            provider="yookassa",
        )

    def test_amount_goes_in_roubles_not_kopecks(self):
        """Копейки, отправленные как рубли, — это счёт в сто раз больше."""
        payment = self.payment("240")
        answer = {"id": "2c-abc", "confirmation": {"confirmation_url": "https://yoo/x"}}
        with mock.patch.object(self.acq, "_post", return_value=answer) as post:
            url = self.acq.create(payment, return_url="https://cafe/ok")

        self.assertEqual(url, "https://yoo/x")
        self.assertEqual(post.call_args[0][1]["amount"], {"value": "240.00", "currency": "RUB"})
        payment.refresh_from_db()
        self.assertEqual(payment.external_id, "2c-abc")

    def test_idempotence_key_is_stable_per_payment(self):
        """Повтор запроса не должен выставлять гостю второй счёт."""
        one, two = self.payment(), self.payment()
        self.assertEqual(self.acq._key(one), self.acq._key(one))
        self.assertNotEqual(self.acq._key(one), self.acq._key(two))

    def test_callback_does_not_trust_payload(self):
        """Уведомление ЮKassa не подписывает — верим только ответу банка."""
        with mock.patch.object(self.acq, "_get", return_value={"status": "canceled"}) as get:
            result = self.acq.read_callback(
                {"event": "payment.succeeded", "object": {"id": "2c-abc", "status": "succeeded"}}, {}
            )
        self.assertFalse(result.success)
        self.assertFalse(result.pending)
        self.assertIn("/payments/2c-abc", get.call_args[0][0])

    def test_paid_status_recognised(self):
        with mock.patch.object(self.acq, "_get", return_value={"status": "succeeded"}):
            result = self.acq.read_callback({"object": {"id": "2c-abc"}}, {})
        self.assertTrue(result.success)

    def test_held_money_is_not_paid_yet(self):
        with mock.patch.object(self.acq, "_get", return_value={"status": "waiting_for_capture"}):
            result = self.acq.read_callback({"object": {"id": "2c-abc"}}, {})
        self.assertFalse(result.success)
        self.assertTrue(result.pending)

    def test_notification_without_payment_id_ignored(self):
        self.assertIsNone(self.acq.read_callback({"event": "payment.succeeded"}, {}))

    def test_bank_error_text_reaches_staff(self):
        """Причину отказа банк пишет в тело; сотруднику нужна она, а не «400»."""
        response = httpx.Response(400, json={"description": "Сумма меньше минимальной"})
        with self.assertRaises(AcquiringError) as cm:
            self.acq._read(response)
        self.assertIn("минимальной", str(cm.exception))


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
        """Платёж у кассы не должен приписываться банку."""
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal("1"),
        )
        self.assertEqual(payment.provider, "manual")


class OnlinePaymentToggleTests(APITestCase):
    """Выключатель приёма оплаты картой в панели владельца.

    Отдельно от выбора банка: банк с ключами настраивают один раз, а
    закрыть оплату может понадобиться в любой момент — банк лёг, день
    только за наличные.
    """

    ENV = {"TBANK_TERMINAL_KEY": "term-1", "TBANK_PASSWORD": "secret"}

    def setUp(self):
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.TBANK
        site.online_payment_on = True
        site.save()

    def site(self):
        return self.client.get("/api/site/").data

    @mock.patch.dict("os.environ", ENV)
    def test_on_and_configured_shows_button(self):
        self.assertTrue(self.site()["online_payment"])

    @mock.patch.dict("os.environ", ENV)
    def test_owner_can_switch_payment_off(self):
        site = SiteSettings.load()
        site.online_payment_on = False
        site.save()
        data = self.site()
        self.assertFalse(data["online_payment"])
        # банк остаётся подключённым — выключатель его не сбрасывает
        self.assertTrue(data["acquiring_ready"])

    @mock.patch.dict("os.environ", {"TBANK_TERMINAL_KEY": "", "TBANK_PASSWORD": ""})
    def test_switch_on_does_not_fake_missing_keys(self):
        """Без доступов «включено» ничего не даёт — иначе гость упрётся в банк."""
        data = self.site()
        self.assertTrue(data["online_payment_on"])
        self.assertFalse(data["acquiring_ready"])
        self.assertFalse(data["online_payment"])

    @mock.patch.dict("os.environ", ENV)
    def test_backend_refuses_payment_when_switched_off(self):
        """Ручка публичная: спрятать кнопку мало, старая вкладка дошла бы до банка."""
        from orders.models import Order

        from .services import PaymentError, start_online_payment

        site = SiteSettings.load()
        site.online_payment_on = False
        site.save()
        order = Order.objects.create(status=Order.Status.OPEN, table="5", total=Decimal("500"))
        with self.assertRaises(PaymentError):
            start_online_payment(order, return_url="https://example.com/")

    def test_acquiring_name_shown_for_owner(self):
        self.assertEqual(self.site()["acquiring_name"], "Т-Банк (Т-Касса)")

    def test_no_bank_selected_reports_nothing_configured(self):
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.NONE
        site.save()
        data = self.site()
        self.assertEqual(data["acquiring_name"], "")
        self.assertFalse(data["acquiring_ready"])

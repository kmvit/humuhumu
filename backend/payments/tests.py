"""Тесты интернет-эквайринга.

Сеть не трогаем: у драйверов подменяется единственный метод _post, который
ходит в банк. Проверяем то, что ломается молча и дорого — подпись,
повторные уведомления и включение оплаты без доступов.
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import httpx

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Category, Product, ProductVariant
from core.models import Organization, SiteSettings
from core.tenancy import organization_context
from orders.models import Order
from users.models import User

from .acquiring import AcquiringError, SberAcquirer, TBankAcquirer, YooKassaAcquirer, get_acquirer
from .models import AcquiringCredentials, Payment
from .acquiring import Result
from .services import apply_bank_result, apply_payment_result, record_manual_payment
from .tasks import settle_pending_payments_task


#: Похож на боевой ключ ЮKassa: проверка формата пропускает только такие.
SECRET = "live_AbCd0123456789xyz"


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
    """Подпись Т-Кассы — единственное, что отличает банк от подделки.

    Эталоны взяты из документации банка (developer.tbank.ru/eacq), а не
    посчитаны нашей же функцией: раньше тест подписывал уведомление тем
    же _token(), что и проверял, и не заметил, что ни одно настоящее
    уведомление подпись не проходит.
    """

    def setUp(self):
        patcher = mock.patch.dict(
            "os.environ", {"TBANK_TERMINAL_KEY": "term-1", "TBANK_PASSWORD": "secret"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acq = TBankAcquirer()

    def test_request_token_matches_bank_example(self):
        """Пример «Сформировать токен» для Init: вложенные DATA и Receipt не в счёт."""
        acq = TBankAcquirer({"terminal_key": "MerchantTerminalKey", "password": "11111111111111"})
        body = {
            "TerminalKey": "MerchantTerminalKey",
            "Amount": 19200,
            "OrderId": "00000",
            "Description": "Подарочная карта на 1000 рублей",
            "DATA": {"Phone": "+71234567890", "Email": "a@test.com"},
            "Receipt": {"Email": "a@test.ru", "Taxation": "osn", "Items": []},
        }
        self.assertEqual(
            acq._token(body),
            "72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2",
        )

    def test_real_notification_is_accepted(self):
        """Пример уведомления из документации — с логическим Success: true."""
        acq = TBankAcquirer({"terminal_key": "1234567890DEMO", "password": "11111111111"})
        payload = {
            "TerminalKey": "1234567890DEMO",
            "OrderId": "000000",
            "Success": True,
            "Status": "AUTHORIZED",
            "PaymentId": "0000000",
            "ErrorCode": "0",
            "Amount": "1111",
            "CardId": "000000",
            "Pan": "200000******0000",
            "ExpDate": "1111",
            "RebillId": "000000",
            "Token": "1c0964277d0213349243065a0d5b838b8e90d2d25f740d0f2767836e710e80c8",
        }
        result = acq.read_callback(payload, {})
        self.assertIsNotNone(result)
        self.assertEqual(result.external_id, "0000000")
        self.assertTrue(result.pending)

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
        result = TBankAcquirer._result("5", "AUTHORIZED")
        self.assertFalse(result.success)
        self.assertTrue(result.pending)

    def test_statuses(self):
        self.assertTrue(TBankAcquirer._result("1", "CONFIRMED").success)
        for failed in ("REJECTED", "AUTH_FAIL", "CANCELED", "DEADLINE_EXPIRED"):
            result = TBankAcquirer._result("1", failed)
            self.assertFalse(result.success or result.pending, failed)
        # Промежуточные и незнакомые — пережидаем, а не отменяем заказ.
        for waiting in ("NEW", "FORM_SHOWED", "3DS_CHECKING", "CONFIRMING", "НЕЧТО"):
            self.assertTrue(TBankAcquirer._result("1", waiting).pending, waiting)


class TBankRequestsTests(TestCase):
    """Что уходит в банк и как читается ответ (сеть подменена)."""

    def setUp(self):
        self.acq = TBankAcquirer({"terminal_key": "term-1", "password": "secret"})

    def test_init_is_one_stage_and_names_our_webhook(self):
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER, amount=Decimal("240"), provider="tbank"
        )
        answer = {"Success": True, "PaymentId": "777", "PaymentURL": "https://pay.example/777"}
        with mock.patch.object(self.acq, "_post", return_value=answer) as post:
            url = self.acq.create(payment, return_url="https://cafe.padacha.ru/?token=abc")

        self.assertEqual(url, "https://pay.example/777")
        method, body = post.call_args.args
        self.assertEqual(method, "Init")
        self.assertEqual(body["Amount"], 24000)
        self.assertEqual(body["PayType"], "O")
        self.assertEqual(
            body["NotificationURL"], "https://cafe.padacha.ru/api/payments/callback/tbank/"
        )
        self.assertEqual(body["Token"], self.acq._token(body))
        payment.refresh_from_db()
        self.assertEqual(payment.external_id, "777")

    def test_webhook_address_is_https_even_if_django_saw_http(self):
        """За прокси Django видел http — банк слал уведомление в 301 и терял его."""
        from .acquiring import _callback_url

        self.assertEqual(
            _callback_url("http://monti.padacha.ru/?token=x", "tbank"),
            "https://monti.padacha.ru/api/payments/callback/tbank/",
        )
        self.assertEqual(
            _callback_url("http://localhost:5174/?token=x", "tbank"),
            "http://localhost:5174/api/payments/callback/tbank/",
        )

    def test_bank_refusal_reason_reaches_staff(self):
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER, amount=Decimal("0.5"), provider="tbank"
        )
        answer = {
            "Success": False, "ErrorCode": "251", "Message": "Неверные параметры.",
            "Details": "Неверная сумма. Сумма должна быть больше или равна 100 копеек.",
        }
        with mock.patch.object(self.acq, "_post", return_value=answer):
            with self.assertRaisesMessage(AcquiringError, "100 копеек"):
                self.acq.create(payment, return_url="https://cafe.padacha.ru/")

    def test_status_asks_get_state(self):
        with mock.patch.object(
            self.acq, "_post", return_value={"Success": True, "Status": "CONFIRMED"}
        ) as post:
            result = self.acq.status("777")
        self.assertEqual(post.call_args.args[0], "GetState")
        self.assertTrue(result.success)
        self.assertEqual(result.external_id, "777")

    def test_check_refuses_wrong_password(self):
        answer = {"Success": False, "ErrorCode": "204", "Message": "Неверные параметры.",
                  "Details": "Неверный токен. Проверьте пару TerminalKey/SecretKey."}
        with mock.patch.object(self.acq, "_post", return_value=answer):
            with self.assertRaisesMessage(AcquiringError, "пароль не подходит"):
                self.acq.check()

    def test_check_refuses_unknown_terminal(self):
        answer = {"Success": False, "ErrorCode": "501", "Details": "Терминал не найден."}
        with mock.patch.object(self.acq, "_post", return_value=answer):
            with self.assertRaisesMessage(AcquiringError, "банк не знает такого терминала"):
                self.acq.check()

    def test_check_accepts_keys_when_only_the_order_is_missing(self):
        """Подпись принята, просто заказа нет — значит, ключи верные."""
        answer = {"Success": False, "ErrorCode": "7", "Details": "Заказ не найден."}
        with mock.patch.object(self.acq, "_post", return_value=answer) as post:
            self.acq.check()
        self.assertEqual(post.call_args.args[0], "CheckOrder")

    def test_bank_certificate_is_trusted(self):
        """Т-Банк на сертификатах Минцифры — без их корня TLS не сойдётся."""
        from .acquiring import _russian_tls

        names = [str(c["subject"]) for c in _russian_tls().get_ca_certs()]
        self.assertTrue(any("Russian Trusted Root CA" in n for n in names))
        with mock.patch("payments.acquiring.httpx.post") as post:
            post.return_value = httpx.Response(
                200,
                json={"Success": True, "Status": "NEW"},
                request=httpx.Request("POST", "https://securepay.tinkoff.ru/v2/GetState"),
            )
            self.acq.status("1")
        self.assertIs(post.call_args.kwargs["verify"], _russian_tls())


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
        product = Product.objects.create(category=cat, name="Латте")
        variant = ProductVariant.objects.create(product=product, price=Decimal("240"))
        self.order = Order.objects.create(status=Order.Status.OPEN, total=Decimal("240"))
        self.order.items.create(variant=variant, quantity=1, unit_price=variant.price)
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
            "Success": True,
            "Status": status,
            "ErrorCode": "0",
            "Amount": 24000,
        }
        payload["Token"] = acq._token(payload)
        return self.client.post(
            "/api/payments/callback/tbank/", payload, format="json"
        )

    def test_notification_closes_order(self):
        response = self.notify()
        self.assertEqual(response.status_code, 200)
        # Т-Банку нужен ровно «OK», иначе он повторяет уведомление месяц.
        self.assertEqual(response.content, b"OK")
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
        self.assertFalse(self.site()["online_payment"])
        # банк остаётся подключённым — выключатель его не сбрасывает
        self.assertTrue(get_acquirer().configured())

    @mock.patch.dict("os.environ", {"TBANK_TERMINAL_KEY": "", "TBANK_PASSWORD": ""})
    def test_switch_on_does_not_fake_missing_keys(self):
        """Без доступов «включено» ничего не даёт — иначе гость упрётся в банк."""
        data = self.site()
        self.assertTrue(data["online_payment_on"])
        self.assertFalse(get_acquirer().configured())
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

    @mock.patch.dict("os.environ", ENV)
    def test_public_site_does_not_name_the_bank(self):
        """Гостю — только «кнопка есть/нет». С кем у кафе договор, он не спрашивал.

        Раньше название банка и состояние его доступов отдавались отсюда
        же, то есть кому угодно без авторизации.
        """
        data = self.site()
        self.assertNotIn("acquiring_name", data)
        self.assertNotIn("acquiring_ready", data)


class CredentialsStorageTests(TestCase):
    """Доступы лежат у заведения, зашифрованные, и не ходят к соседям."""

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain="beta.padacha.ru")

    def test_secret_is_not_readable_in_the_table(self):
        """В базе шифр, а не пароль: унесённый дамп сам по себе бесполезен."""
        with organization_context(self.a):
            row = AcquiringCredentials(provider="yookassa")
            row.set_values({"shop_id": "100500", "secret_key": SECRET})
            row.save()
            raw = AcquiringCredentials.all_objects.get(pk=row.pk).payload
        self.assertNotIn("live_секрет", raw)
        self.assertNotIn("100500", raw)
        self.assertEqual(row.values()["secret_key"], SECRET)

    def test_blank_values_are_not_stored(self):
        """Пустой пароль — это «не задан», а не «задан пустым»."""
        row = AcquiringCredentials(provider="yookassa", organization=self.a)
        row.set_values({"shop_id": "100500", "secret_key": "   "})
        self.assertEqual(row.values(), {"shop_id": "100500"})

    def test_neighbour_does_not_get_our_keys(self):
        """Главное в переезде: банк выбран у двоих, а доступы у каждого свои."""
        with organization_context(self.a):
            row = AcquiringCredentials(provider="yookassa")
            row.set_values({"shop_id": "100500", "secret_key": "live_альфы"})
            row.save()
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()
            self.assertEqual(get_acquirer().shop_id, "100500")

        with organization_context(self.b):
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()
            # Банк тот же, доступов своих нет — значит оплаты нет. Раньше
            # здесь подхватились бы общие ключи соседа, и выручка Беты
            # ушла бы в магазин Альфы.
            self.assertFalse(get_acquirer().configured())
            self.assertEqual(get_acquirer().shop_id, "")

    @mock.patch.dict("os.environ", {"YOOKASSA_SHOP_ID": "env-shop", "YOOKASSA_SECRET_KEY": "env-key"})
    def test_env_is_ignored_when_there_are_neighbours(self):
        """Общая установка: окружение общее, поэтому как источник не годится."""
        with organization_context(self.b):
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()
            self.assertFalse(get_acquirer().configured())

    @mock.patch.dict("os.environ", {"YOOKASSA_SHOP_ID": "env-shop", "YOOKASSA_SECRET_KEY": "env-key"})
    def test_env_still_works_on_a_single_install(self):
        """Отдельная установка (кафе на своём сервере) продолжает жить на .env."""
        self.b.delete()
        with organization_context(self.a):
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()
            self.assertTrue(get_acquirer().configured())
            self.assertEqual(get_acquirer().shop_id, "env-shop")

    def test_unreadable_payload_is_not_a_crash(self):
        """Сменили ключ шифрования — «нет доступов», а не пятисотка на весь сайт."""
        row = AcquiringCredentials(provider="yookassa", organization=self.a, payload="мусор")
        self.assertEqual(row.values(), {})


class AcquiringSettingsApiTests(APITestCase):
    """Ручка владельца: выбрать банк и ввести ключи, не показывая их обратно."""

    def setUp(self):
        self.org = Organization.objects.order_by("pk").first()
        self.org.domain = "testserver"
        self.org.save()
        # Второе заведение — чтобы запасной источник из окружения молчал
        # и тесты проверяли именно ручку.
        Organization.objects.create(name="Бета", slug="beta", domain="beta.padacha.ru")
        self.owner = User.objects.create_user(
            "owner", password="Sh4-owner", role=User.Role.ADMIN, organization=self.org
        )
        self.client.force_authenticate(self.owner)
        # Ключи теперь проверяются у банка пробным запросом — в тестах
        # подменяем только сеть, чтобы проверка формата осталась живой.
        bank = mock.patch.object(YooKassaAcquirer, "_get", return_value={"account_id": "100500"})
        self.bank = bank.start()
        self.addCleanup(bank.stop)

    def save(self, **body):
        return self.client.put("/api/acquiring/", body, format="json")

    def test_owner_sets_bank_and_keys(self):
        response = self.save(
            provider="yookassa",
            values={"shop_id": "100500", "secret_key": SECRET},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ready"])
        # Вне запроса заведение нужно называть явно: их в базе два.
        with organization_context(self.org):
            self.assertEqual(SiteSettings.load().acquiring, "yookassa")
            self.assertEqual(get_acquirer().secret, SECRET)

    def test_secrets_never_come_back(self):
        self.save(provider="yookassa", values={"shop_id": "100500", "secret_key": SECRET})
        data = self.client.get("/api/acquiring/").data
        self.assertNotIn(SECRET, str(data))
        self.assertEqual(data["filled"], {"shop_id": True, "secret_key": True, "vat": False})

    def test_blank_field_keeps_the_old_value(self):
        """Форма не знает секрета, поэтому пустое поле значит «не менял»."""
        self.save(provider="yookassa", values={"shop_id": "100500", "secret_key": SECRET})
        self.save(provider="yookassa", values={"shop_id": "100501", "secret_key": ""})
        with organization_context(self.org):
            self.assertEqual(get_acquirer().shop_id, "100501")
            self.assertEqual(get_acquirer().secret, SECRET)

    def test_switching_bank_turns_payment_off(self):
        """Ключи нового банка ещё не проверены — гость не должен на них наткнуться."""
        self.save(provider="yookassa", values={"shop_id": "100500", "secret_key": SECRET})
        with organization_context(self.org):
            site = SiteSettings.load()
            site.online_payment_on = True
            site.save()

        self.save(provider="tbank", values={})
        with organization_context(self.org):
            self.assertFalse(SiteSettings.load().online_payment_on)

    def test_old_keys_survive_a_round_trip(self):
        """Ушли в другой банк и вернулись — вводить заново не заставляем."""
        self.save(provider="yookassa", values={"shop_id": "100500", "secret_key": SECRET})
        self.save(provider="tbank", values={})
        self.save(provider="yookassa", values={})
        self.assertTrue(self.client.get("/api/acquiring/").data["ready"])

    def test_delete_wipes_the_keys(self):
        self.save(provider="yookassa", values={"shop_id": "100500", "secret_key": SECRET})
        data = self.client.delete("/api/acquiring/").data
        self.assertFalse(data["ready"])
        self.assertEqual(data["filled"], {"shop_id": False, "secret_key": False, "vat": False})

    def test_unknown_bank_is_refused(self):
        self.assertEqual(self.save(provider="sberbank-ru", values={}).status_code, 400)

    def test_staff_cannot_read_or_change_it(self):
        """Реквизиты приёма денег — дело владельца, а не склада с официантом."""
        for role in (User.Role.WAREHOUSE, User.Role.WAITER):
            user = User.objects.create_user(
                f"сотрудник-{role}", password="Sh4-staff", role=role, organization=self.org
            )
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get("/api/acquiring/").status_code, 403)
            self.assertEqual(self.save(provider="yookassa", values={}).status_code, 403)

    def test_guest_cannot_read_it(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/acquiring/").status_code, 401)


class MigrateAcquiringKeysTests(TestCase):
    """Разовый перенос ключей из окружения в заведения."""

    ENV = {"YOOKASSA_SHOP_ID": "env-shop", "YOOKASSA_SECRET_KEY": "env-key"}

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.name, self.a.domain = "Монти", "monti.padacha.ru"
        self.a.save()
        self.b = Organization.objects.create(name="Соседи", slug="sosedi", domain="sosedi.example.com")
        with organization_context(self.a):
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()

    def run_command(self, **options) -> str:
        out = StringIO()
        call_command("migrate_acquiring_keys", stdout=out, **options)
        return out.getvalue()

    @mock.patch.dict("os.environ", ENV)
    def test_keys_land_in_the_cafe_that_chose_the_bank(self):
        self.run_command()
        with organization_context(self.a):
            self.assertEqual(get_acquirer().shop_id, "env-shop")
        with organization_context(self.b):
            self.assertFalse(AcquiringCredentials.objects.exists())

    @mock.patch.dict("os.environ", ENV)
    def test_dry_run_changes_nothing(self):
        self.assertIn("пробный запуск", self.run_command(dry_run=True))
        self.assertFalse(AcquiringCredentials.all_objects.exists())

    @mock.patch.dict("os.environ", ENV)
    def test_shared_bank_is_not_guessed(self):
        """Один банк у двоих — чьи это ключи, решает человек, а не команда."""
        with organization_context(self.b):
            site = SiteSettings.load()
            site.acquiring = SiteSettings.Acquiring.YOOKASSA
            site.save()
        self.assertIn("не угадывает", self.run_command())
        self.assertFalse(AcquiringCredentials.all_objects.exists())

    @mock.patch.dict("os.environ", ENV)
    def test_existing_keys_are_not_overwritten(self):
        with organization_context(self.a):
            row = AcquiringCredentials(provider="yookassa")
            row.set_values({"shop_id": "своё", "secret_key": "своё"})
            row.save()
        self.run_command()
        with organization_context(self.a):
            self.assertEqual(get_acquirer().shop_id, "своё")


class CredentialsCheckTests(APITestCase):
    """Ключи проверяются, пока владелец у экрана, а не на платеже гостя.

    История прямо из боя: в форму ЮKassa уехали доступы от другого банка,
    сохранились без единого возражения, и первым об этом узнал гость —
    банк отказал уже на его оплате.
    """

    def setUp(self):
        self.org = Organization.objects.order_by("pk").first()
        self.org.domain = "testserver"
        self.org.save()
        Organization.objects.create(name="Бета", slug="beta-check", domain="beta-check.padacha.ru")
        self.owner = User.objects.create_user(
            "owner-check", password="Sh4-owner", role=User.Role.ADMIN, organization=self.org
        )
        self.client.force_authenticate(self.owner)

    def save(self, **values):
        return self.client.put(
            "/api/acquiring/", {"provider": "yookassa", "values": values}, format="json"
        )

    def test_keys_from_another_bank_are_refused(self):
        with mock.patch.object(YooKassaAcquirer, "_get") as bank:
            response = self.save(shop_id="ТЕРМ1", secret_key="0123456789abcdef")

        self.assertEqual(response.status_code, 400)
        self.assertIn("shopId", response.data["detail"])
        # До банка с таким даже не ходили — незачем.
        bank.assert_not_called()
        with organization_context(self.org):
            self.assertFalse(AcquiringCredentials.objects.exists())

    def test_secret_without_live_prefix_is_refused(self):
        with mock.patch.object(YooKassaAcquirer, "_get"):
            response = self.save(shop_id="100500", secret_key="0123456789abcdef")
        self.assertEqual(response.status_code, 400)
        self.assertIn("live_", response.data["detail"])

    def test_bank_has_the_last_word(self):
        """Формат сошёлся, а банк ключ не принял — сохранять нечего."""
        with mock.patch.object(
            YooKassaAcquirer, "_get", side_effect=AcquiringError("Invalid credentials")
        ):
            response = self.save(shop_id="100500", secret_key=SECRET)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid credentials", response.data["detail"])
        with organization_context(self.org):
            self.assertFalse(AcquiringCredentials.objects.exists())

    def test_good_keys_go_to_the_bank_and_are_saved(self):
        with mock.patch.object(
            YooKassaAcquirer, "_get", return_value={"account_id": "100500"}
        ) as bank:
            response = self.save(shop_id="100500", secret_key=SECRET)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ready"])
        self.assertIn("/me", bank.call_args[0][0])

    def test_owner_gets_the_address_for_the_bank_cabinet(self):
        """Без этого адреса банк молчит об оплате, а взять его больше негде."""
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={}):
            self.save(shop_id="100500", secret_key=SECRET)
        data = self.client.get("/api/acquiring/").data
        self.assertTrue(data["callback_url"].endswith("/api/payments/callback/yookassa/"))


class SettleWithoutWebhookTests(APITestCase):
    """Оплата доводится опросом банка, даже если уведомление не пришло.

    Случай не гипотетический: адрес уведомлений прописывается в кабинете
    банка руками, и пока он не прописан, деньги у заведения, а заказ висит
    открытым — гость смотрит на кнопку «оплатить» и платит второй раз.
    """

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        product = Product.objects.create(category=cat, name="Флэт уайт")
        variant = ProductVariant.objects.create(product=product, price=Decimal("290"))
        self.order = Order.objects.create(
            status=Order.Status.OPEN, total=Decimal("290"), public_token=uuid.uuid4()
        )
        self.order.items.create(variant=variant, quantity=1, unit_price=variant.price)
        self.payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=Decimal("290"),
            order=self.order,
            method=Payment.Method.CARD,
            provider="yookassa",
            external_id="3244d9f7",
        )
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.YOOKASSA
        site.save()
        env = mock.patch.dict(
            "os.environ", {"YOOKASSA_SHOP_ID": "100500", "YOOKASSA_SECRET_KEY": SECRET}
        )
        env.start()
        self.addCleanup(env.stop)

    def age(self, seconds=60):
        """Состарить платёж: опрос бережёт банк и не дёргает свежие."""
        Payment.objects.filter(pk=self.payment.pk).update(
            updated_at=timezone.now() - timedelta(seconds=seconds)
        )

    def test_guest_screen_closes_the_paid_order(self):
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}):
            response = self.client.get(f"/api/orders/track/?token={self.order.public_token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paid")
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.pay_method, Order.PayMethod.CARD)
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_unpaid_order_is_left_alone(self):
        """Гость ещё на странице банка — трогать заказ рано."""
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "pending"}):
            self.client.get(f"/api/orders/track/?token={self.order.public_token}")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)

    def test_bank_is_not_asked_on_every_poll(self):
        """Гость опрашивает заказ каждые пять секунд — банк столько не нужен."""
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}) as bank:
            self.client.get(f"/api/orders/track/?token={self.order.public_token}")
        bank.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.OPEN)

    def test_dead_bank_does_not_break_the_guest_screen(self):
        self.age()
        with mock.patch.object(
            YooKassaAcquirer, "_get", side_effect=AcquiringError("банк недоступен")
        ):
            response = self.client.get(f"/api/orders/track/?token={self.order.public_token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "open")

    def test_yesterday_payment_is_not_resurrected(self):
        """Заказ давно провели наличными — закрывать его второй раз нельзя."""
        Payment.objects.filter(pk=self.payment.pk).update(
            created_at=timezone.now() - timedelta(days=2),
            updated_at=timezone.now() - timedelta(days=2),
        )
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}) as bank:
            self.client.get(f"/api/orders/track/?token={self.order.public_token}")
        bank.assert_not_called()

    def test_background_task_closes_it_for_a_closed_tab(self):
        """Гость закрыл вкладку сразу после оплаты — заказ закроет задача."""
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}):
            report = settle_pending_payments_task()

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(sum(v for v in report.values() if isinstance(v, int)), 1)

    def test_second_path_does_not_close_the_order_twice(self):
        """Уведомление и опрос могут сойтись на одном платеже в одну секунду.

        Второй пришедший не должен ни переписать время закрытия, ни
        начислить гостю бонусы повторно.
        """
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={"status": "succeeded"}):
            self.client.get(f"/api/orders/track/?token={self.order.public_token}")
        self.order.refresh_from_db()
        closed_at, closed_by = self.order.closed_at, self.order.closed_by

        self.payment.refresh_from_db()
        applied = apply_bank_result(
            self.payment, Result(external_id="3244d9f7", success=True)
        )

        self.assertFalse(applied)
        self.order.refresh_from_db()
        self.assertEqual(self.order.closed_at, closed_at)
        self.assertEqual(self.order.closed_by, closed_by)

    def test_manual_payments_are_never_asked_about(self):
        """У кассового платежа банка нет — спрашивать не у кого."""
        Payment.objects.filter(pk=self.payment.pk).update(provider="manual", external_id=None)
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get") as bank:
            settle_pending_payments_task()
        bank.assert_not_called()


class RefundTests(APITestCase):
    """Возврат денег за заказ: банк, реестр, бонусы, склад и выручка.

    Возврат — не отмена. Отменяют то, чего не было; возвращают
    проданное, и это должно быть видно и в кассе, и в смене.
    """

    ENV = {"YOOKASSA_SHOP_ID": "100500", "YOOKASSA_SECRET_KEY": SECRET}

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        product = Product.objects.create(category=cat, name="Раф")
        self.variant = ProductVariant.objects.create(product=product, price=Decimal("320"))
        self.order = Order.objects.create(
            status=Order.Status.OPEN, total=Decimal("320"), public_token=uuid.uuid4()
        )
        self.order.items.create(variant=self.variant, quantity=1, unit_price=self.variant.price)
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.YOOKASSA
        site.save()
        env = mock.patch.dict("os.environ", self.ENV)
        env.start()
        self.addCleanup(env.stop)
        self.waiter = User.objects.create_user(
            "barista-refund", password="Sh4-staff", role=User.Role.WAITER
        )
        self.client.force_authenticate(self.waiter)

    def pay_online(self):
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=self.order.payable,
            order=self.order,
            method=Payment.Method.CARD,
            provider="yookassa",
            external_id="pay-1",
        )
        apply_payment_result(payment, success=True)
        self.order.refresh_from_db()
        return payment

    def refund(self, **body):
        return self.client.post(f"/api/orders/{self.order.id}/refund/", body, format="json")

    # ——— деньги ———

    def test_online_payment_goes_back_through_the_bank(self):
        self.pay_online()
        with mock.patch.object(
            YooKassaAcquirer, "_post", return_value={"id": "ref-1", "status": "succeeded"}
        ) as bank:
            res = self.refund()

        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("/refunds", bank.call_args[0][0])
        self.assertEqual(bank.call_args[0][1]["payment_id"], "pay-1")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.assertIsNotNone(self.order.refunded_at)

    def test_registry_keeps_both_records(self):
        """Исходный платёж не переписываем: он был, и касса должна его видеть."""
        self.pay_online()
        with mock.patch.object(YooKassaAcquirer, "_post", return_value={"id": "ref-1", "status": "succeeded"}):
            self.refund()

        paid = Payment.objects.get(purpose=Payment.Purpose.ORDER)
        back = Payment.objects.get(purpose=Payment.Purpose.REFUND)
        self.assertEqual(paid.status, Payment.Status.REFUNDED)
        self.assertEqual(back.amount, Decimal("320"))
        self.assertEqual(back.external_id, "ref-1")

    def test_bank_refusal_leaves_everything_as_it_was(self):
        """Сказать «готово», когда банк отказал, — отпустить гостя без денег."""
        self.pay_online()
        with mock.patch.object(
            YooKassaAcquirer, "_post", side_effect=AcquiringError("Возврат невозможен")
        ):
            res = self.refund()

        self.assertEqual(res.status_code, 400)
        self.assertIn("невозможен", res.data["detail"])
        self.order.refresh_from_db()
        # Заказ остаётся закрытым и оплаченным — ровно как до попытки.
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertIsNone(self.order.refunded_at)
        self.assertFalse(Payment.objects.filter(purpose=Payment.Purpose.REFUND).exists())

    def test_cash_refund_needs_no_bank(self):
        """Наличные кассир отдаёт из ящика — нам остаётся записать это."""
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.order.refresh_from_db()

        res = self.refund()

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Payment.objects.get(purpose=Payment.Purpose.REFUND).method, Payment.Method.CASH)

    def test_unpaid_order_cannot_be_refunded(self):
        self.assertEqual(self.refund().status_code, 400)

    def test_second_refund_is_refused(self):
        self.pay_online()
        with mock.patch.object(YooKassaAcquirer, "_post", return_value={"id": "ref-1", "status": "succeeded"}):
            self.refund()
        with mock.patch.object(YooKassaAcquirer, "_post") as bank:
            res = self.refund()
        self.assertEqual(res.status_code, 400)
        bank.assert_not_called()

    # ——— заказ и отмена ———

    def test_paid_order_cannot_be_cancelled_instead(self):
        """Отмена оплаченного заказа оставляла бы деньги у заведения."""
        self.pay_online()
        res = self.client.patch(f"/api/orders/{self.order.id}/cancel/", {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("вернуть", res.data["detail"])

    # ——— выручка ———

    def test_day_revenue_loses_the_refunded_money(self):
        from shifts.services import day_revenue

        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.order.refresh_from_db()
        self.assertEqual(day_revenue(timezone.localdate()), Decimal("320.00"))

        self.refund()
        self.assertEqual(day_revenue(timezone.localdate()), Decimal("0.00"))

    def test_yesterdays_revenue_is_not_rewritten(self):
        """Иначе вчерашняя зарплата менялась бы задним числом."""
        from shifts.services import day_revenue

        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        yesterday = timezone.now() - timedelta(days=1)
        Order.objects.filter(pk=self.order.pk).update(closed_at=yesterday)
        Payment.objects.filter(order=self.order).update(created_at=yesterday)
        self.order.refresh_from_db()

        self.refund()

        self.assertEqual(day_revenue(yesterday.date()), Decimal("320.00"))
        self.assertEqual(day_revenue(timezone.localdate()), Decimal("-320.00"))

    def test_refund_with_bonuses_leaves_no_revenue(self):
        """Часть чека оплачена бонусами — возврат всё равно обнуляет выручку дня."""
        from shifts.services import day_revenue

        Order.objects.filter(pk=self.order.pk).update(bonus_spent=Decimal("100"))
        self.order.refresh_from_db()
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.assertEqual(day_revenue(timezone.localdate()), Decimal("320.00"))

        self.refund()
        self.assertEqual(day_revenue(timezone.localdate()), Decimal("0.00"))

    def test_refunded_order_still_counts_for_performer(self):
        """Бариста заказ сделал — возврат не вычёркивает его работу."""
        from shifts.services import performer_stats

        Order.objects.filter(pk=self.order.pk).update(performer=self.waiter)
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.refund()
        stats = performer_stats(timezone.localdate())
        self.assertEqual(stats[self.waiter.id]["orders"], 1)

    def test_refund_of_yesterday_does_not_eat_todays_rate(self):
        """Выручка дня в минусе — бонус ноль, ставка смены целая."""
        from shifts.models import Shift, ShiftMember
        from shifts.services import shift_report

        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        yesterday = timezone.now() - timedelta(days=1)
        Order.objects.filter(pk=self.order.pk).update(closed_at=yesterday)
        self.refund()

        shift = Shift.objects.create(
            date=timezone.localdate(), daily_rate=Decimal("2000"), bonus_percent=Decimal("9")
        )
        ShiftMember.objects.create(shift=shift, user=self.waiter, role="waiter")
        r = shift_report(shift)
        self.assertEqual(r["revenue"], "-320.00")
        self.assertEqual(r["bonus_pool"], "0.00")
        self.assertEqual(r["payout"], "2000.00")

    # ——— бонусы ———

    def with_bonus_guest(self, balance="500", spend=None):
        """Гость в бонусной программе, 10% начисления; по желанию — часть чека бонусами."""
        from loyalty.models import LoyaltyMember
        from loyalty.services import redeem

        site = SiteSettings.load()
        site.bonus_enabled = True
        site.bonus_earn_percent = Decimal("10")
        site.save()
        guest = User.objects.create_user("guest-refund", role=User.Role.CLIENT)
        member = LoyaltyMember.objects.create(user=guest, balance=Decimal(balance))
        Order.objects.filter(pk=self.order.pk).update(client=guest)
        self.order.refresh_from_db()
        if spend:
            redeem(member.pk, Decimal(spend), self.order)
            self.order.refresh_from_db()
        return member

    def test_refund_returns_spent_and_takes_earned_bonuses(self):
        member = self.with_bonus_guest(balance="500", spend="100")
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        member.refresh_from_db()
        # 500 − 100 списано + 22 начислено (10% от 220 деньгами)
        self.assertEqual(member.balance, Decimal("422"))

        self.assertEqual(self.refund().status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("500"))

    def test_refund_keeps_bonus_part_on_the_order(self):
        """Списание остаётся на заказе: на нём стоит выручка дня и отчёт месяца."""
        from finance.services import report

        self.with_bonus_guest(balance="500", spend="100")
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.refund()
        self.order.refresh_from_db()
        self.assertEqual(self.order.bonus_spent, Decimal("100"))
        refund = Payment.objects.get(order=self.order, purpose=Payment.Purpose.REFUND)
        self.assertEqual(refund.amount, Decimal("220"))
        # продали на 220 деньгами, 220 вернули — выручки ноль, а не фантомные 100
        r = report(timezone.localdate())
        self.assertEqual(r["revenue"], "0.00")
        self.assertEqual(r["bonuses_spent"], "100.00")

    def test_spent_earned_bonuses_do_not_push_balance_below_zero(self):
        from loyalty.models import LoyaltyMember

        member = self.with_bonus_guest(balance="0")
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        LoyaltyMember.objects.filter(pk=member.pk).update(balance=Decimal("10"))  # 32 − потратил 22
        self.refund()
        member.refresh_from_db()
        self.assertEqual(member.balance, Decimal("0"))

    def test_stale_ready_does_not_write_off_refunded_order(self):
        """Вернули продукты на склад, а планшет ещё жмёт «готово» — второго списания нет."""
        from inventory.models import StockCategory, StockItem
        from inventory.services import write_off_order_item

        cat = StockCategory.objects.create(name="Молоко")
        milk = StockItem.objects.create(category=cat, name="Молоко", unit=StockItem.Unit.MILLILITER)
        self.variant.recipe.create(item=milk, quantity=Decimal("200"))
        item = self.order.items.first()
        write_off_order_item(item, user=self.waiter)
        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.refund(return_to_stock=True)
        milk.refresh_from_db()
        self.assertEqual(milk.quantity, Decimal("0.000"))

        item.refresh_from_db()
        write_off_order_item(item, user=self.waiter)
        milk.refresh_from_db()
        self.assertEqual(milk.quantity, Decimal("0.000"))

    # ——— склад ———

    def test_stock_returns_only_when_asked(self):
        from inventory.models import StockCategory, StockItem

        cat = StockCategory.objects.create(name="Молоко")
        milk = StockItem.objects.create(category=cat, name="Молоко", unit=StockItem.Unit.MILLILITER)
        self.variant.recipe.create(item=milk, quantity=Decimal("200"))
        item = self.order.items.first()
        from inventory.services import write_off_order_item

        write_off_order_item(item, user=self.waiter)
        milk.refresh_from_db()
        self.assertEqual(milk.quantity, Decimal("-200.000"))

        record_manual_payment(self.order, Payment.Method.CASH, user=self.waiter)
        self.refund(return_to_stock=True)

        milk.refresh_from_db()
        self.assertEqual(milk.quantity, Decimal("0.000"))


class GuestPaymentErrorTests(APITestCase):
    """Отказ банка: гостю — что делать, владельцу — почему.

    Онлайн-оплату запускает только гость. Раньше текст банка доходил до
    него как есть («Неверные параметры…», ошибка TLS, «введите ключи в
    панели»), а заведение об отказе не узнавало вовсе.
    """

    def setUp(self):
        self.order = Order.objects.create(
            status=Order.Status.OPEN, total=Decimal("290"), public_token=uuid.uuid4()
        )
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.YOOKASSA
        site.online_payment_on = True
        site.save()
        env = mock.patch.dict(
            "os.environ", {"YOOKASSA_SHOP_ID": "100500", "YOOKASSA_SECRET_KEY": SECRET}
        )
        env.start()
        self.addCleanup(env.stop)
        self.owner = User.objects.create_user(
            "owner", password="Sh4-owner", role=User.Role.ADMIN,
            organization=Organization.objects.order_by("pk").first(),
        )

    def pay(self):
        return self.client.post(
            "/api/orders/pay_online/", {"token": str(self.order.public_token)}, format="json"
        )

    def refuse(self, reason="Incorrect password format in the Authorization header"):
        return mock.patch.object(
            YooKassaAcquirer, "_post", side_effect=AcquiringError(reason)
        )

    def panel(self):
        self.client.force_authenticate(self.owner)
        try:
            return self.client.get("/api/acquiring/").data
        finally:
            self.client.force_authenticate(None)

    def test_guest_does_not_see_the_bank_reason(self):
        with self.refuse():
            response = self.pay()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Authorization", response.data["detail"])
        self.assertIn("оплатите на месте", response.data["detail"])
        # Пустышка в реестре не осталась.
        self.assertFalse(Payment.objects.filter(order=self.order).exists())

    def test_owner_sees_the_reason_and_the_order(self):
        """Запись переживает откат транзакции, в которой банк отказал."""
        with self.refuse():
            self.pay()
        error = self.panel()["last_error"]
        self.assertIn("Authorization", error["error"])
        self.assertEqual(error["order"], self.order.pk)

    def test_missing_keys_are_reported_to_owner_not_guest(self):
        with mock.patch.dict("os.environ", {"YOOKASSA_SECRET_KEY": ""}):
            response = self.pay()
        self.assertNotIn("панели", response.data["detail"])
        self.assertIn("доступы", self.panel()["last_error"]["error"])

    def test_error_disappears_after_a_payment_goes_through(self):
        with self.refuse():
            self.pay()
        answer = {"id": "p-1", "confirmation": {"confirmation_url": "https://pay.example/1"}}
        with mock.patch.object(YooKassaAcquirer, "_post", return_value=answer):
            self.assertEqual(self.pay().status_code, 200)
        self.assertIsNone(self.panel()["last_error"])

    def test_error_disappears_when_owner_saves_keys_the_bank_accepts(self):
        with self.refuse():
            self.pay()
        self.client.force_authenticate(self.owner)
        with mock.patch.object(YooKassaAcquirer, "_get", return_value={}):
            data = self.client.put(
                "/api/acquiring/",
                {"provider": "yookassa", "values": {"shop_id": "100500", "secret_key": SECRET}},
                format="json",
            ).data
        self.assertIsNone(data["last_error"])


class RefundWithoutOwnNumberTests(APITestCase):
    """Т-Банк и Сбер не выдают возврату своего номера.

    Т-Банк отвечает на Cancel номером исходного платежа. Мы писали его в
    запись возврата, вставка падала на уникальности номера и откатывала
    учёт — а деньги банк к тому времени уже вернул (нашли на тестовом
    терминале 03.10.2026).
    """

    setUp = RefundTests.setUp
    ENV = RefundTests.ENV

    def pay(self, provider):
        payment = Payment.objects.create(
            purpose=Payment.Purpose.ORDER,
            status=Payment.Status.PENDING,
            amount=self.order.payable,
            order=self.order,
            method=Payment.Method.CARD,
            provider=provider,
            external_id="9368792155",
        )
        apply_payment_result(payment, success=True)
        return payment

    def assert_refunded(self, payment):
        self.order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertTrue(
            Payment.objects.filter(order=self.order, purpose=Payment.Purpose.REFUND).exists()
        )

    def test_tbank_refund_is_recorded(self):
        payment = self.pay("tbank")
        answer = {"Success": True, "Status": "REFUNDED", "PaymentId": "9368792155"}
        with mock.patch.dict("os.environ", {"TBANK_TERMINAL_KEY": "t", "TBANK_PASSWORD": "p"}), \
                mock.patch.object(TBankAcquirer, "_post", return_value=answer):
            response = self.client.post(f"/api/orders/{self.order.pk}/refund/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assert_refunded(payment)

    def test_sber_refund_is_recorded(self):
        payment = self.pay("sber")
        env = {"SBER_USERNAME": "u", "SBER_PASSWORD": "p"}
        with mock.patch.dict("os.environ", env), \
                mock.patch.object(SberAcquirer, "_post", return_value={"errorCode": "0"}):
            response = self.client.post(f"/api/orders/{self.order.pk}/refund/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assert_refunded(payment)

    def test_driver_echoing_the_payment_number_does_not_lose_the_refund(self):
        """Страховка в сервисе: новый банк повторит ошибку — учёт не потеряется."""
        payment = self.pay("yookassa")
        with mock.patch.object(YooKassaAcquirer, "refund", return_value="9368792155"):
            response = self.client.post(f"/api/orders/{self.order.pk}/refund/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assert_refunded(payment)


class ReceiptTests(APITestCase):
    """Чек по 54-ФЗ для ЮKassa с подключённой кассой.

    У Монти к ЮKassa подключена касса Бизнес.Ру, и банк отклонял каждый
    платёж без блока receipt («Receipt is missing or illegal») — онлайн-
    оплата не прошла ни разу (нашли 04.10.2026).
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        cat = Category.objects.create(name="Кофе", station="bar")
        latte = Product.objects.create(category=cat, name="Латте")
        self.latte = ProductVariant.objects.create(product=latte, price=Decimal("250"))
        cookie = Product.objects.create(category=cat, name="Печенье")
        self.cookie = ProductVariant.objects.create(product=cookie, price=Decimal("90"))
        self.order = Order.objects.create(
            status=Order.Status.UNPAID, total=Decimal("590"), public_token=uuid.uuid4()
        )
        self.order.items.create(variant=self.latte, quantity=2, unit_price=Decimal("250"))
        self.order.items.create(variant=self.cookie, quantity=1, unit_price=Decimal("90"))
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.YOOKASSA
        site.online_payment_on = True
        site.save()
        env = mock.patch.dict(
            "os.environ", {"YOOKASSA_SHOP_ID": "100500", "YOOKASSA_SECRET_KEY": SECRET}
        )
        env.start()
        self.addCleanup(env.stop)

    def bank(self, fiscal=True):
        """ЮKassa: /me говорит, подключена ли касса; платёж создаётся."""
        me = mock.patch.object(
            YooKassaAcquirer, "_get",
            return_value={"fiscalization_enabled": fiscal,
                          "fiscalization": {"enabled": fiscal, "provider": "business_ru"}},
        )
        # Каждый платёж у банка — со своим номером, как в жизни.
        numbers = iter(range(1, 100))

        def answer(*args, **kwargs):
            n = next(numbers)
            return {"id": f"p-{n}", "confirmation": {"confirmation_url": f"https://pay.example/{n}"}}

        post = mock.patch.object(YooKassaAcquirer, "_post", side_effect=answer)
        me.start(); self.addCleanup(me.stop)
        return post.start(), post

    def pay(self, contact=None):
        body = {"token": str(self.order.public_token)}
        if contact is not None:
            body["contact"] = contact
        return self.client.post("/api/orders/pay_online/", body, format="json")

    def test_guest_is_asked_where_to_send_the_receipt(self):
        post, patcher = self.bank()
        response = self.pay()
        patcher.stop()
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["need_contact"])
        post.assert_not_called()

    def test_receipt_goes_to_the_bank_and_sums_match(self):
        post, patcher = self.bank()
        response = self.pay("+7 (999) 123-45-67")
        patcher.stop()
        self.assertEqual(response.status_code, 200, response.data)
        body = post.call_args.args[1]
        receipt = body["receipt"]
        self.assertEqual(receipt["customer"], {"phone": "79991234567"})
        total = sum(
            Decimal(i["amount"]["value"]) * Decimal(i["quantity"]) for i in receipt["items"]
        )
        self.assertEqual(str(total.quantize(Decimal("0.01"))), body["amount"]["value"])
        self.assertEqual({i["vat_code"] for i in receipt["items"]}, {1})
        self.order.refresh_from_db()
        self.assertEqual(self.order.receipt_contact, "79991234567")

    def test_contact_is_remembered_for_the_next_try(self):
        post, patcher = self.bank()
        self.pay("guest@example.com")
        response = self.pay()
        patcher.stop()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            post.call_args.args[1]["receipt"]["customer"], {"email": "guest@example.com"}
        )

    def test_member_phone_is_used_without_asking(self):
        guest = User.objects.create_user("g", password="x", role=User.Role.CLIENT, phone="89990001122")
        Order.objects.filter(pk=self.order.pk).update(client=guest)
        post, patcher = self.bank()
        response = self.pay()
        patcher.stop()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(post.call_args.args[1]["receipt"]["customer"], {"phone": "79990001122"})

    def test_garbage_contact_is_refused(self):
        post, patcher = self.bank()
        response = self.pay("позвоните мне")
        patcher.stop()
        self.assertTrue(response.data["need_contact"])
        post.assert_not_called()

    def test_no_receipt_without_online_kassa(self):
        """Касса к ЮKassa не подключена — ни чека, ни вопроса гостю."""
        post, patcher = self.bank(fiscal=False)
        response = self.pay()
        patcher.stop()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("receipt", post.call_args.args[1])


class ReceiptLinesTests(TestCase):
    """Бонусы раскладываются по позициям так, что чек сходится до копейки."""

    def order(self, *lines, bonus="0"):
        cat = Category.objects.create(name="Кухня", station="kitchen")
        order = Order.objects.create(status=Order.Status.OPEN, total=Decimal("0"))
        for name, price, qty in lines:
            product = Product.objects.create(category=cat, name=name)
            variant = ProductVariant.objects.create(product=product, price=Decimal(price))
            order.items.create(variant=variant, quantity=qty, unit_price=Decimal(price))
        order.recalc_total()
        order.bonus_spent = Decimal(bonus)
        order.save()
        return order

    def check(self, order):
        from .receipt import lines

        result = lines(order, order.payable)
        self.assertEqual(sum(l.total for l in result), order.payable)
        self.assertTrue(all(l.price > 0 and l.quantity > 0 for l in result))
        return result

    def test_without_bonuses_lines_are_the_order(self):
        result = self.check(self.order(("Латте", "250", 2), ("Печенье", "90", 1)))
        self.assertEqual([(l.quantity, l.price) for l in result],
                         [(2, Decimal("250")), (1, Decimal("90"))])

    def test_bonuses_spread_to_the_kopeck(self):
        self.check(self.order(("Латте", "250", 3), ("Печенье", "90", 1), bonus="100"))

    def test_awkward_split_breaks_a_line_in_two(self):
        """333 ₽ скидки на 3 позиции × 7 шт. не делятся на копейки — строка делится."""
        result = self.check(self.order(("Сырник", "100", 7), bonus="3.33"))
        self.assertGreaterEqual(len(result), 1)

    def test_parse_contact(self):
        from .receipt import parse_contact

        self.assertEqual(parse_contact("8 (999) 123-45-67"), "79991234567")
        self.assertEqual(parse_contact("9991234567"), "79991234567")
        self.assertEqual(parse_contact(" Guest@Example.com "), "guest@example.com")
        self.assertEqual(parse_contact("12345"), "")
        self.assertEqual(parse_contact("a@b"), "")


class TBankReceiptTests(APITestCase):
    """Чек Т-Банку: тест 7 в кабинете Т-Бизнеса («Формирование чека»)."""

    def setUp(self):
        cat = Category.objects.create(name="Кофе", station="bar")
        cocoa = Product.objects.create(category=cat, name="Какао")
        variant = ProductVariant.objects.create(product=cocoa, price=Decimal("270"))
        self.order = Order.objects.create(
            status=Order.Status.UNPAID, total=Decimal("270"), public_token=uuid.uuid4()
        )
        self.order.items.create(variant=variant, quantity=1, unit_price=Decimal("270"))
        site = SiteSettings.load()
        site.acquiring = SiteSettings.Acquiring.TBANK
        site.online_payment_on = True
        site.save()

    def keys(self, **extra):
        env = {"TBANK_TERMINAL_KEY": "t-DEMO", "TBANK_PASSWORD": "p", **extra}
        patcher = mock.patch.dict("os.environ", env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pay(self, contact=None):
        body = {"token": str(self.order.public_token)}
        if contact:
            body["contact"] = contact
        answer = {"Success": True, "PaymentId": "777", "PaymentURL": "https://pay.example/777"}
        with mock.patch.object(TBankAcquirer, "_post", return_value=answer) as post:
            response = self.client.post("/api/orders/pay_online/", body, format="json")
        return response, post

    def test_receipt_with_kassa(self):
        self.keys(TBANK_RECEIPT="1", TBANK_TAXATION="usn_income")
        response, post = self.pay("8 999 123-45-67")
        self.assertEqual(response.status_code, 200, response.data)
        method, body = post.call_args.args
        receipt = body["Receipt"]
        self.assertEqual(receipt["Taxation"], "usn_income")
        self.assertEqual(receipt["Phone"], "+79991234567")
        [item] = receipt["Items"]
        self.assertEqual((item["Price"], item["Quantity"], item["Amount"]), (27000, 1, 27000))
        self.assertEqual(item["Tax"], "none")
        self.assertEqual(sum(i["Amount"] for i in receipt["Items"]), body["Amount"])
        # Вложенный Receipt в подпись не входит — подпись та же, что без него.
        self.assertEqual(body["Token"], TBankAcquirer()._token(body))

    def test_guest_asked_for_contact(self):
        self.keys(TBANK_RECEIPT="1")
        response, post = self.pay()
        self.assertTrue(response.data["need_contact"])
        post.assert_not_called()

    def test_no_receipt_without_kassa(self):
        self.keys(TBANK_RECEIPT="0")
        response, post = self.pay()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("Receipt", post.call_args.args[1])

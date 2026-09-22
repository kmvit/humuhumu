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
from .services import apply_payment_result
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
        self.assertEqual(data["filled"], {"shop_id": True, "secret_key": True})

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
        self.assertEqual(data["filled"], {"shop_id": False, "secret_key": False})

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

    def test_manual_payments_are_never_asked_about(self):
        """У кассового платежа банка нет — спрашивать не у кого."""
        Payment.objects.filter(pk=self.payment.pk).update(provider="manual", external_id=None)
        self.age()
        with mock.patch.object(YooKassaAcquirer, "_get") as bank:
            settle_pending_payments_task()
        bank.assert_not_called()

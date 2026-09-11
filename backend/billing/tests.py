"""Подписки внутри общей установки.

Проверяем то, что ломается молча и дорого: лестница блокировки, продление
платежом и совместимость с внешними установками — кафе на своём сервере
ходит за лицензией по HTTP и должно получать тот же подписанный ответ,
что раньше отдавал пульт.
"""
import hashlib
import hmac
import json
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Organization, SiteSettings
from core.tenancy import organization_context

from .models import Client, Payment, Subscription, add_months


def make_sub(**kwargs):
    org = Organization.objects.create(
        name=kwargs.pop("name", "Кафе"),
        slug=kwargs.pop("slug", "cafe-test"),
        domain=kwargs.pop("domain", ""),
        license_key=kwargs.pop("license_key", ""),
    )
    return Subscription.objects.create(organization=org, **kwargs)


class StatusLadderTests(TestCase):
    def test_ladder(self):
        sub = make_sub(paid_until=date(2026, 9, 30), grace_days=7)
        self.assertEqual(sub.status(date(2026, 9, 9)), Subscription.Status.ACTIVE)
        self.assertEqual(sub.status(date(2026, 9, 26)), Subscription.Status.EXPIRING)
        # день в день ещё не просрочка
        self.assertEqual(sub.status(date(2026, 9, 30)), Subscription.Status.EXPIRING)
        self.assertEqual(sub.status(date(2026, 10, 3)), Subscription.Status.GRACE)
        self.assertEqual(sub.status(date(2026, 10, 7)), Subscription.Status.GRACE)
        self.assertEqual(sub.status(date(2026, 10, 8)), Subscription.Status.BLOCKED)

    def test_internal_never_blocked(self):
        sub = make_sub(paid_until=date(2020, 1, 1), is_internal=True)
        self.assertEqual(sub.status(date(2026, 9, 9)), Subscription.Status.ACTIVE)

    def test_trial_week_by_default(self):
        self.assertEqual(make_sub().paid_until, timezone.localdate() + timedelta(days=7))


class PaymentTests(TestCase):
    def test_prepaid_does_not_burn(self):
        future = timezone.localdate() + timedelta(days=10)
        sub = make_sub(paid_until=future)
        Payment.objects.create(subscription=sub, amount=2990, months=1)
        sub.refresh_from_db()
        self.assertEqual(sub.paid_until, add_months(future, 1))

    def test_after_downtime_counts_from_today(self):
        sub = make_sub(paid_until=timezone.localdate() - timedelta(days=60))
        Payment.objects.create(subscription=sub, amount=2990, months=2)
        sub.refresh_from_db()
        self.assertEqual(sub.paid_until, add_months(timezone.localdate(), 2))

    def test_editing_payment_does_not_extend_twice(self):
        sub = make_sub()
        p = Payment.objects.create(subscription=sub, amount=2990, months=1)
        sub.refresh_from_db()
        was = sub.paid_until
        p.comment = "поправили"
        p.save()
        sub.refresh_from_db()
        self.assertEqual(sub.paid_until, was)

    def test_add_months_clamps_short_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))


class LocalSubscriptionTests(TestCase):
    """Заведение этой установки читает подписку рядом, а не по сети."""

    def test_status_comes_from_local_subscription(self):
        from core.license import effective_status, status_payload

        sub = make_sub(paid_until=timezone.localdate() - timedelta(days=30), grace_days=7)
        with organization_context(sub.organization):
            self.assertEqual(effective_status(), "blocked")
            payload = status_payload()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["status"], "blocked")
        # сверяться не с кем — подписка в этой же базе
        self.assertIsNone(payload["checked_at"])

    def test_plan_of_subscription_is_reported(self):
        from core.license import status_payload

        sub = make_sub(plan=SiteSettings.Plan.MAX)
        with organization_context(sub.organization):
            self.assertEqual(status_payload()["plan"], "max")


class LicenseIssuingTests(TestCase):
    """Внешняя установка спрашивает лицензию по ключу — контракт прежний."""

    def _post(self, body):
        return self.client.post(
            "/api/license/", json.dumps(body), content_type="application/json"
        )

    def test_unknown_key_404(self):
        self.assertEqual(self._post({"key": "нет такого"}).status_code, 404)
        self.assertEqual(self._post({}).status_code, 404)

    def test_organization_without_subscription_is_not_licensed(self):
        Organization.objects.create(name="Без подписки", slug="nosub", license_key="k1")
        self.assertEqual(self._post({"key": "k1"}).status_code, 404)

    def test_signed_answer_and_telemetry(self):
        sub = make_sub(license_key="secret-key", plan=SiteSettings.Plan.MAX)
        res = self._post({"key": "secret-key", "version": "abc1234"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        data = body["data"]
        self.assertEqual(data["plan"], "max")
        self.assertEqual(data["paid_until"], sub.paid_until.isoformat())
        self.assertFalse(data["internal"])

        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(
            b"secret-key", canonical.encode(), hashlib.sha256
        ).hexdigest()
        self.assertEqual(body["sign"], expected)

        sub.organization.refresh_from_db()
        self.assertIsNotNone(sub.organization.last_seen_at)
        self.assertEqual(sub.organization.last_version, "abc1234")

    def test_works_on_any_host(self):
        """Ключ адресует заведение сам — домен тут ни при чём.

        Иначе внешней установке было бы некуда стучаться: её домен
        принадлежит ей, а не выдающей установке.
        """
        make_sub(license_key="key-2", slug="other", name="Другое")
        res = self.client.post(
            "/api/license/",
            json.dumps({"key": "key-2"}),
            content_type="application/json",
            HTTP_HOST="какой-угодно.example.com".encode("idna").decode(),
        )
        self.assertEqual(res.status_code, 200)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get("/api/license/").status_code, 405)


class ImportPultTests(TestCase):
    """Импорт реестра из старого пульта.

    Переезд делается один раз, но ошибиться в нём дорого: потеря ключа
    лицензии мгновенно заблокирует внешнюю установку, а повторный запуск
    не должен плодить дубли — переезд обычно повторяют.
    """

    DUMP = [
        {"model": "billing.client", "pk": 1,
         "fields": {"name": "Сеть «Дубль»", "phone": "+7 900 000-00-00",
                    "contact_person": "", "email": "", "notes": ""}},
        {"model": "billing.instance", "pk": 7,
         "fields": {"client": 1, "title": "Точка на набережной",
                    "domain": "naberezhnaya.padacha.ru",
                    "license_key": "key-naberezhnaya", "plan": "hall",
                    "paid_until": "2026-12-31", "grace_days": 5,
                    "is_internal": False, "notes": "",
                    "last_seen_at": None, "last_version": ""}},
        {"model": "billing.payment", "pk": 3,
         "fields": {"instance": 7, "amount": "2990.00", "months": 1,
                    "paid_at": "2026-09-01", "comment": "счёт 12"}},
    ]

    def _run(self):
        import json
        import tempfile

        from django.core.management import call_command

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(self.DUMP, fh)
            path = fh.name
        call_command("import_pult", path, verbosity=0)

    def test_import_creates_organization_subscription_and_payment(self):
        self._run()
        org = Organization.objects.get(domain="naberezhnaya.padacha.ru")
        self.assertEqual(org.name, "Точка на набережной")
        # ключ обязан переехать: по нему внешняя установка получает лицензию
        self.assertEqual(org.license_key, "key-naberezhnaya")
        self.assertEqual(org.client.name, "Сеть «Дубль»")

        sub = org.subscription
        self.assertEqual(sub.plan, "hall")
        self.assertEqual(sub.paid_until, date(2026, 12, 31))
        self.assertEqual(sub.grace_days, 5)
        self.assertEqual(sub.payments.count(), 1)

    def test_import_does_not_extend_subscription_by_imported_payment(self):
        """«Оплачено до» уже посчитано пультом — платёж не должен продлевать
        его второй раз."""
        self._run()
        sub = Subscription.objects.get(organization__domain="naberezhnaya.padacha.ru")
        self.assertEqual(sub.paid_until, date(2026, 12, 31))

    def test_repeat_import_is_idempotent(self):
        self._run()
        self._run()
        self.assertEqual(Organization.objects.filter(domain="naberezhnaya.padacha.ru").count(), 1)
        self.assertEqual(Client.objects.filter(name="Сеть «Дубль»").count(), 1)
        self.assertEqual(Payment.objects.count(), 1)


class SubscriptionDrivesFeaturesTests(TestCase):
    """Оплаченный тариф обязан открывать разделы.

    Ровно эта связь и потерялась: подписка ставила «Максимум», а фичи
    читались из настроек заведения, куда её никто не переносил, — владелец
    платил, а склада не видел.
    """

    def test_features_follow_subscription(self):
        from core.plans import current_plan, features

        sub = make_sub(plan=SiteSettings.Plan.START)
        with organization_context(sub.organization):
            self.assertEqual(current_plan(), "start")
            self.assertEqual(features(), frozenset())

            sub.plan = SiteSettings.Plan.MAX
            sub.save()
            self.assertEqual(current_plan(), "max")
            self.assertIn("inventory", features())

    def test_site_settings_follow_subscription(self):
        """Тариф в настройках не должен спорить с подпиской: его читают и
        в панели владельца, и в Django-админке."""
        sub = make_sub(plan=SiteSettings.Plan.HALL)
        with organization_context(sub.organization):
            self.assertEqual(SiteSettings.load().plan, "hall")
            sub.plan = SiteSettings.Plan.MAX
            sub.save()
            self.assertEqual(SiteSettings.load().plan, "max")

    def test_api_reports_paid_plan(self):
        from rest_framework.test import APIClient

        sub = make_sub(plan=SiteSettings.Plan.MAX, domain="paid.padacha.ru")
        with self.settings(ALLOWED_HOSTS=["*"]):
            data = APIClient().get("/api/site/", HTTP_HOST="paid.padacha.ru").json()
        self.assertEqual(data["plan"], "max")
        self.assertIn("inventory", data["features"])

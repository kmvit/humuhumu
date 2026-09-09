"""Тесты настроек заведения.

Главный здесь — AdminCoversModelTests. Продукт разворачивается разным кафе,
и каждое поле настроек кто-то заполняет руками при подключении. Поле, не
попавшее в fieldsets админки, правится только через shell — то есть для
заказчика его нет. Так уже случилось: из 31 поля в админке было 14, включая
ни одного реквизита. Тест сторожит, чтобы это не повторилось молча.
"""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.contrib import admin as dj_admin
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from users.models import User

from .license import effective_status, sync_license
from .models import LicenseState, SiteSettings
from .plans import features


class AdminCoversModelTests(TestCase):
    def _model_fields(self) -> set[str]:
        return {
            f.name
            for f in SiteSettings._meta.get_fields()
            if getattr(f, "editable", False) and not f.auto_created
        }

    def _admin_fields(self) -> set[str]:
        admin_class = dj_admin.site._registry[SiteSettings]
        names: set[str] = set()
        for _, options in admin_class.fieldsets:
            for entry in options["fields"]:
                names.update((entry,) if isinstance(entry, str) else entry)
        return names

    def test_every_field_is_editable_in_admin(self):
        missing = self._model_fields() - self._admin_fields()
        self.assertEqual(
            missing,
            set(),
            "Поля есть в модели, но не в админке — заказчик их не заполнит: "
            + ", ".join(sorted(missing)),
        )

    def test_admin_has_no_phantom_fields(self):
        """Опечатка в fieldsets роняет всю страницу настроек, а не одно поле."""
        phantom = self._admin_fields() - self._model_fields()
        self.assertEqual(phantom, set(), f"В админке поля, которых нет в модели: {phantom}")


class AcquiringDefaultTests(TestCase):
    def test_new_installation_has_no_online_payment(self):
        """Свежее заведение не должно случайно оказаться с включённой оплатой."""
        self.assertEqual(SiteSettings.load().acquiring, SiteSettings.Acquiring.NONE)


class PlanGateTests(APITestCase):
    """Гейт по тарифу: раздел, которого нет в тарифе, закрыт на бэке.

    Прячущий фронт — вежливость, а permission — защита: без неё кофейня
    на «Старте» получила бы весь «Максимум», просто дёргая API напрямую.
    """

    def setUp(self):
        self.site = SiteSettings.load()
        self.manager = User.objects.create_user(
            "manager", password="x", role=User.Role.WAREHOUSE
        )
        self.client.force_authenticate(self.manager)

    def _set_plan(self, plan):
        self.site.plan = plan
        self.site.save()

    def test_start_blocks_paid_sections(self):
        self._set_plan(SiteSettings.Plan.START)
        for url in ("/api/inventory/items/", "/api/finance/expenses/", "/api/shifts/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_hall_opens_stations_but_not_warehouse(self):
        self._set_plan(SiteSettings.Plan.HALL)
        self.assertIn("stations", features())
        self.assertEqual(self.client.get("/api/inventory/items/").status_code, 403)

    def test_max_opens_everything(self):
        self._set_plan(SiteSettings.Plan.MAX)
        for url in ("/api/inventory/items/", "/api/finance/expenses/", "/api/shifts/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_grandfather_default_is_start_for_new_install(self):
        # Новая установка не должна получать «Максимум» бесплатно.
        self.assertEqual(SiteSettings._meta.get_field("plan").default, "start")

    def test_site_api_exposes_features_but_plan_is_read_only(self):
        self._set_plan(SiteSettings.Plan.START)
        data = self.client.get("/api/site/").json()
        self.assertEqual(data["plan"], "start")
        self.assertEqual(data["features"], [])
        # заведение не может само себе выписать «Максимум»
        self.client.patch("/api/site/", {"plan": "max"}, format="json")
        self.site.refresh_from_db()
        self.assertEqual(self.site.plan, SiteSettings.Plan.START)


@override_settings(LICENSE_KEY="testkey", LICENSE_URL="https://pult.test/api/license/")
class LicenseClientTests(APITestCase):
    """Сверка с пультом, фейл-опен и блокировка middleware."""

    def _state(self, paid_delta_days, checked_delta_days=0, grace=7):
        state = LicenseState.load()
        state.plan = "max"
        state.paid_until = timezone.localdate() + timedelta(days=paid_delta_days)
        state.grace_days = grace
        state.checked_at = timezone.now() - timedelta(days=checked_delta_days)
        state.save()
        return state

    def test_ladder(self):
        self._state(paid_delta_days=30)
        self.assertEqual(effective_status(), "active")
        self._state(paid_delta_days=3)
        self.assertEqual(effective_status(), "expiring")
        self._state(paid_delta_days=-2)
        self.assertEqual(effective_status(), "grace")
        self._state(paid_delta_days=-8)
        self.assertEqual(effective_status(), "blocked")

    def test_fail_open(self):
        # без ключа лицензирование выключено
        with override_settings(LICENSE_KEY=""):
            self.assertEqual(effective_status(), "active")
        # ни одной сверки ещё не было — не блокируем
        self.assertEqual(effective_status(), "active")
        # просрочка есть, но пульт молчит дольше STALE_DAYS — смягчаем до грейса
        self._state(paid_delta_days=-30, checked_delta_days=15)
        self.assertEqual(effective_status(), "grace")

    def test_sync_verifies_signature_and_writes_plan(self):
        data = {
            "plan": "hall",
            "paid_until": (timezone.localdate() + timedelta(days=30)).isoformat(),
            "grace_days": 7,
            "status": "active",
            "issued_at": timezone.now().isoformat(),
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        sign = hmac.new(b"testkey", canonical, hashlib.sha256).hexdigest()
        response = mock.Mock(status_code=200)
        response.json.return_value = {"data": data, "sign": sign}
        response.raise_for_status.return_value = None
        with mock.patch("core.license.httpx.post", return_value=response):
            state = sync_license()
        self.assertEqual(state.plan, "hall")
        self.assertEqual(SiteSettings.load().plan, "hall")  # тариф пришёл из лицензии
        self.assertEqual(state.last_error, "")

        # подделанная подпись — кэш не меняется, ошибка записана
        response.json.return_value = {"data": {**data, "plan": "max"}, "sign": sign}
        with mock.patch("core.license.httpx.post", return_value=response):
            state = sync_license()
        self.assertEqual(state.plan, "hall")
        self.assertIn("подпись", state.last_error)

    def test_middleware_blocks_and_whitelists(self):
        self._state(paid_delta_days=-30)  # заблокировано
        waiter = User.objects.create_user("w1", password="x", role=User.Role.WAITER)
        self.client.force_authenticate(waiter)
        # рабочий API закрыт
        self.assertEqual(self.client.get("/api/orders/").status_code, 402)
        # гостевая витрина и вход — открыты
        self.assertEqual(self.client.get("/api/products/").status_code, 200)
        self.assertEqual(self.client.get("/api/site/").status_code, 200)
        self.assertEqual(self.client.get("/api/users/me/").status_code, 200)
        # кнопка «Проверить оплату» работает у заблокированных
        response = mock.Mock(status_code=200)
        response.json.return_value = {}
        response.raise_for_status.return_value = None
        with mock.patch("core.license.httpx.post", return_value=response):
            self.assertEqual(self.client.get("/api/license/status/").status_code, 200)

    def test_status_endpoint_requires_staff(self):
        self.assertEqual(self.client.get("/api/license/status/").status_code, 401)
        client_user = User.objects.create_user("c1", password="x", role=User.Role.CLIENT)
        self.client.force_authenticate(client_user)
        self.assertEqual(self.client.get("/api/license/status/").status_code, 403)

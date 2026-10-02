"""Синхронизация терминала aQsi через личный кабинет.

Кабинет подменяется: httpx.post (вход) и httpx.put (синхронизация) в
providers.py. Проверяем дорогое: перед синхронизацией с кассы снимаются
заказы, закрытые у нас (иначе после неё кассир возьмёт деньги второй раз),
и если снять не вышло — не синхронизируем; сессия переиспользуется и
обновляется, когда истекла; между нажатиями — пауза; сами по себе не синхронизируемся.
"""
from datetime import timedelta
from unittest import mock

import httpx
from django.utils import timezone

from users.models import User

from .kassa_sync import resync_kassa
from .models import AcquiringCredentials, KassaSync, Payment
from .providers import KassaError
from .tests_kassa import KEY, KassaBase

DEVICE = "29df2999-7035-4459-8175-56edbf1ee0f5"


class FakeLk:
    """Личный кабинет aQsi: вход по логину и пароль, кнопка синхронизации."""

    def __init__(self):
        self.logins = 0
        self.resyncs: list[str] = []
        self.sid = "s%3Asession-1"
        self.expired = False

    def post(self, url, json=None, headers=None, timeout=None, follow_redirects=None):
        request = httpx.Request("POST", url)
        assert url.endswith("/auth")
        if json != {"emailOrPhone": "owner@monty.ru", "password": "lk-pass"}:
            return httpx.Response(401, json={"message": "Неверный логин или пароль"}, request=request)
        self.logins += 1
        self.sid = f"s%3Asession-{self.logins}"
        self.expired = False
        return httpx.Response(
            200, json={"ok": True}, request=request,
            headers={"set-cookie": f"aqsi-web-app.sid={self.sid}; Path=/; "
                                   "Expires=Fri, 02 Oct 2099 07:13:29 GMT"},
        )

    def put(self, url, json=None, cookies=None, headers=None, timeout=None, follow_redirects=None):
        request = httpx.Request("PUT", url)
        if self.expired or (cookies or {}).get("aqsi-web-app.sid") != self.sid:
            return httpx.Response(401, request=request)
        assert url.endswith("/resync")
        self.resyncs.append(url.split("/devices/settings/", 1)[1].removesuffix("/resync"))
        return httpx.Response(200, json={}, request=request)


class KassaSyncTests(KassaBase):
    def setUp(self):
        super().setUp()
        row = AcquiringCredentials.objects.get(provider="aqsi")
        row.set_values({
            "api_key": KEY, "lk_login": "owner@monty.ru", "lk_password": "lk-pass",
            "lk_device": f"https://lk.aqsi.ru/devices/{DEVICE}/settings",
        })
        row.save()
        self.lk = FakeLk()
        for name in ("post", "put"):
            patcher = mock.patch(f"payments.providers.httpx.{name}", side_effect=getattr(self.lk, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.barista = User.objects.create_user("barista-s", password="Sh4-staff", role=User.Role.WAITER)

    def on_kassa(self, minutes_ago=0):
        order = self.place()
        self.assertEqual(self.to_kassa(order).status_code, 200)
        if minutes_ago:
            Payment.objects.filter(order=order).update(
                created_at=timezone.now() - timedelta(minutes=minutes_ago)
            )
        return order

    def pause_passed(self):
        KassaSync.objects.update(attempted_at=timezone.now() - timedelta(hours=1))

    def test_resync_logs_in_and_presses_button(self):
        self.assertTrue(resync_kassa())
        self.assertEqual(self.lk.resyncs, [DEVICE])
        row = KassaSync.objects.get()
        self.assertIsNotNone(row.synced_at)
        self.assertEqual(row.error, "")

    def test_session_reused_and_renewed(self):
        resync_kassa()
        self.pause_passed()
        resync_kassa()
        self.assertEqual(self.lk.logins, 1)  # вторая — по сохранённой сессии
        self.lk.expired = True
        self.pause_passed()
        self.assertTrue(resync_kassa())
        self.assertEqual(self.lk.logins, 2)
        self.assertEqual(len(self.lk.resyncs), 3)

    def test_pause_between_presses(self):
        self.assertTrue(resync_kassa())
        self.assertFalse(resync_kassa())
        self.assertEqual(len(self.lk.resyncs), 1)

    def test_manually_paid_order_is_dropped_before_resync(self):
        """«Оплачено» руками — на кассе заказ лежит «Отложен»: снимаем до синхронизации."""
        order = self.on_kassa()
        payment = Payment.objects.get(order=order)
        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.Status.SUCCEEDED, confirmed_by=self.barista
        )
        self.assertIn(payment.external_id, self.aqsi.orders)
        resync_kassa()
        self.assertNotIn(payment.external_id, self.aqsi.orders)
        payment.refresh_from_db()
        self.assertTrue(payment.kassa_meta.get("off_kassa"))
        # Проверенный больше не спрашиваем.
        self.aqsi.calls.clear()
        self.pause_passed()
        resync_kassa()
        self.assertNotIn(("GET", f"/v2/Orders/simple/{payment.external_id}"), self.aqsi.calls)

    def test_paid_on_kassa_is_left_alone(self):
        order = self.on_kassa()
        payment = Payment.objects.get(order=order)
        self.aqsi.paid(payment.external_id)
        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.Status.SUCCEEDED, confirmed_by=self.barista
        )
        resync_kassa()
        self.assertNotIn(("DELETE", f"/v2/Orders/simple/{payment.external_id}"), self.aqsi.calls)
        self.assertEqual(len(self.lk.resyncs), 1)

    def test_no_resync_if_closed_order_stays_on_kassa(self):
        """Не сняли закрытый заказ — синхронизация вернула бы его на терминал."""
        order = self.on_kassa()
        Payment.objects.filter(order=order).update(status=Payment.Status.CANCELLED)
        self.aqsi.key_ok = False
        with self.assertRaises(KassaError):
            resync_kassa()
        self.assertEqual(self.lk.resyncs, [])
        self.assertIn("дважды", KassaSync.objects.get().error)

    def test_board_does_not_resync_by_itself(self):
        """Решение владельца: синхронизация только по кнопке."""
        self.on_kassa(minutes_ago=10)
        self.client.force_authenticate(self.barista)
        self.client.get("/api/orders/?status=open&with_unpaid=1")
        self.assertEqual(self.lk.resyncs, [])

    def test_endpoint(self):
        self.client.force_authenticate(self.barista)
        res = self.client.post("/api/kassa/resync/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["done"])

    def test_endpoint_not_configured(self):
        row = AcquiringCredentials.objects.get(provider="aqsi")
        row.set_values({"api_key": KEY})
        row.save()
        self.client.force_authenticate(self.barista)
        res = self.client.post("/api/kassa/resync/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("не настроена", res.data["detail"])

    def test_settings_check_login(self):
        owner = User.objects.create_user("owner-s", password="Sh4-staff", role=User.Role.ADMIN)
        self.client.force_authenticate(owner)
        res = self.client.put("/api/kassa/", {
            "provider": "aqsi", "values": {"lk_password": "wrong"},
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("не принял", res.data["detail"])
        res = self.client.get("/api/kassa/")
        self.assertIsNotNone(res.data["sync"])

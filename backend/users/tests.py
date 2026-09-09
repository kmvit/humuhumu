"""Тесты управления сотрудниками из панели владельца.

Здесь стерегутся границы: чтобы владелец заведения не мог тронуть наш
доступ поддержки, не остался без администратора и не удалил человека
вместе с его историей смен.
"""
from rest_framework.test import APITestCase

from .models import User


class StaffApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            "owner", password="Sh4-owner-pass", role=User.Role.ADMIN
        )
        self.waiter = User.objects.create_user(
            "waiter1", password="Sh4-waiter-pass", role=User.Role.WAITER
        )
        self.client.force_authenticate(self.owner)

    def test_only_admin_role_has_access(self):
        self.client.force_authenticate(self.waiter)
        self.assertEqual(self.client.get("/api/staff/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/staff/").status_code, 401)

    def test_list_hides_clients_and_superusers(self):
        User.objects.create_user("guest", role=User.Role.CLIENT)
        User.objects.create_superuser("support", password="Sh4-support-pass")
        usernames = {row["username"] for row in self.client.get("/api/staff/").json()}
        self.assertEqual(usernames, {"owner", "waiter1"})

    def test_create_staff_can_log_in(self):
        res = self.client.post(
            "/api/staff/",
            {
                "username": "cook1",
                "name": "Пётр",
                "role": "cook",
                "password": "Sh4-cook-pass",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertNotIn("password", res.data)  # пароль наружу не отдаём
        created = User.objects.get(username="cook1")
        self.assertTrue(created.check_password("Sh4-cook-pass"))
        self.assertFalse(created.is_superuser)  # владелец не создаёт суперюзеров

    def test_create_rejects_bad_role_and_duplicate_login(self):
        bad_role = self.client.post(
            "/api/staff/",
            {"username": "x1", "role": "client", "password": "Sh4-some-pass"},
            format="json",
        )
        self.assertEqual(bad_role.status_code, 400)

        dup = self.client.post(
            "/api/staff/",
            {"username": "WAITER1", "role": "cook", "password": "Sh4-some-pass"},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)  # логин занят, регистр не спасает

    def test_weak_password_rejected(self):
        res = self.client.post(
            "/api/staff/",
            {"username": "bar1", "role": "bar", "password": "12345"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_password_change(self):
        res = self.client.patch(
            f"/api/staff/{self.waiter.pk}/",
            {"password": "Sh4-new-pass"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.check_password("Sh4-new-pass"))

    def test_dismiss_and_restore_keep_the_record(self):
        res = self.client.post(f"/api/staff/{self.waiter.pk}/dismiss/")
        self.assertEqual(res.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertFalse(self.waiter.is_active)
        self.assertTrue(User.objects.filter(pk=self.waiter.pk).exists())

        self.client.post(f"/api/staff/{self.waiter.pk}/restore/")
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.is_active)

    def test_cannot_dismiss_or_demote_self(self):
        # иначе заведение осталось бы без администратора
        self.assertEqual(
            self.client.post(f"/api/staff/{self.owner.pk}/dismiss/").status_code, 400
        )
        self.assertEqual(
            self.client.patch(
                f"/api/staff/{self.owner.pk}/", {"role": "waiter"}, format="json"
            ).status_code,
            400,
        )
        # но свой пароль сменить можно
        self.assertEqual(
            self.client.patch(
                f"/api/staff/{self.owner.pk}/",
                {"password": "Sh4-owner-new"},
                format="json",
            ).status_code,
            200,
        )

    def test_superuser_is_not_reachable(self):
        support = User.objects.create_superuser("support2", password="Sh4-support-pass")
        self.assertEqual(
            self.client.patch(
                f"/api/staff/{support.pk}/", {"role": "waiter"}, format="json"
            ).status_code,
            404,
        )

    def test_delete_is_not_allowed(self):
        res = self.client.delete(f"/api/staff/{self.waiter.pk}/")
        self.assertEqual(res.status_code, 405)

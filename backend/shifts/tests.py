from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order, Table
from users.models import User

from .models import Shift, ShiftMember, ShiftSettings


class ShiftTests(APITestCase):
    """Смены: состав ставит менеджер, деньги считаются по выручке дня."""

    def setUp(self):
        self.staff = {
            role: User.objects.create_user(
                username=role, password="demo12345", role=role, first_name=role.title()
            )
            for role in ("cook", "waiter", "bar")
        }
        self.cook2 = User.objects.create_user(
            username="cook2", password="demo12345", role=User.Role.COOK
        )
        # менеджер — аккаунт с правами кладовщика
        self.manager = User.objects.create_user(
            username="manager", password="demo12345", role=User.Role.WAREHOUSE
        )
        self.client_user = User.objects.create_user(
            username="guest", password="demo12345", role=User.Role.CLIENT
        )
        penalty, _ = Table.objects.get_or_create(name="Штраф")
        cfg = ShiftSettings.load()
        cfg.daily_rate = Decimal("2000")
        cfg.bonus_percent = Decimal("9")
        cfg.penalty_table = penalty
        cfg.save()

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def put_in_shift(self, *users, date=None):
        self.auth(self.manager)
        for u in users:
            body = {"user": u.id, **({"date": date} if date else {})}
            res = self.client.post("/api/shifts/add_member/", body, format="json")
            self.assertEqual(res.status_code, 200)
        return res.data

    def sale(self, total, table="1"):
        return Order.objects.create(
            table=table,
            status=Order.Status.PAID,
            closed_at=timezone.now(),
            total=Decimal(total),
        )

    # ——— состав смены ———

    def test_manager_puts_staff_in_shift(self):
        data = self.put_in_shift(self.staff["cook"], self.staff["waiter"])
        self.assertEqual(data["members_count"], 2)
        self.assertTrue(data["can_edit"])
        self.assertEqual(Shift.objects.count(), 1)

    def test_manager_removes_from_shift(self):
        self.put_in_shift(self.staff["cook"], self.staff["waiter"])
        res = self.client.post(
            "/api/shifts/remove_member/", {"user": self.staff["cook"].id}, format="json"
        )
        self.assertEqual(res.data["members_count"], 1)
        # убрали последнего — пустая смена не хранится
        self.client.post(
            "/api/shifts/remove_member/",
            {"user": self.staff["waiter"].id},
            format="json",
        )
        self.assertEqual(Shift.objects.count(), 0)

    def test_shift_can_be_set_for_another_day(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.put_in_shift(self.staff["bar"], date=tomorrow)
        self.assertEqual(Shift.objects.get().date.isoformat(), tomorrow)

    def test_worker_cannot_change_shift(self):
        self.auth(self.staff["cook"])
        res = self.client.post(
            "/api/shifts/add_member/", {"user": self.staff["cook"].id}, format="json"
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.get("/api/shifts/staff/").status_code, 403)

    def test_worker_sees_shift_read_only(self):
        self.put_in_shift(self.staff["cook"], self.staff["bar"])
        self.auth(self.staff["cook"])
        data = self.client.get("/api/shifts/day/").data
        self.assertTrue(data["in_shift"])
        self.assertFalse(data["can_edit"])
        self.assertEqual(data["members_count"], 2)

    def test_client_has_no_access(self):
        self.auth(self.client_user)
        self.assertEqual(self.client.get("/api/shifts/day/").status_code, 403)

    # ——— деньги ———

    def test_payout_splits_bonus_and_penalty(self):
        self.put_in_shift(*self.staff.values(), self.cook2)

        self.sale("60000")
        self.sale("40000")
        self.sale("500", table="Штраф")  # подарок гостю за косяк
        self.sale("1500", table="Штраф")

        self.auth(self.staff["waiter"])
        data = self.client.get("/api/shifts/day/").data
        self.assertEqual(Decimal(data["revenue"]), Decimal("100000.00"))
        self.assertEqual(Decimal(data["penalty"]), Decimal("2000.00"))
        self.assertEqual(Decimal(data["bonus_pool"]), Decimal("9000.00"))
        self.assertEqual(data["members_count"], 4)
        # 2000 ставка + 9000/4 бонус − 2000/4 списаний
        self.assertEqual(Decimal(data["bonus_share"]), Decimal("2250.00"))
        self.assertEqual(Decimal(data["penalty_share"]), Decimal("500.00"))
        self.assertEqual(Decimal(data["payout"]), Decimal("3750.00"))

    def test_penalty_table_is_out_of_revenue(self):
        self.put_in_shift(self.staff["waiter"])
        self.sale("1000", table="Штраф")
        data = self.client.get("/api/shifts/day/").data
        self.assertEqual(Decimal(data["revenue"]), Decimal("0.00"))
        self.assertEqual(Decimal(data["penalty"]), Decimal("1000.00"))
        # штраф больше заработка — в минус не уводим
        self.assertEqual(Decimal(data["payout"]), Decimal("1000.00"))

    def test_rate_change_does_not_rewrite_history(self):
        self.put_in_shift(self.staff["cook"])
        cfg = ShiftSettings.load()
        cfg.daily_rate = Decimal("3000")
        cfg.save()
        data = self.client.get("/api/shifts/day/").data
        self.assertEqual(Decimal(data["daily_rate"]), Decimal("2000.00"))

    # ——— ручной штраф за смену ———

    def test_manager_sets_manual_penalty(self):
        self.put_in_shift(self.staff["cook"], self.staff["bar"])  # 2 в смене
        self.auth(self.manager)
        res = self.client.post(
            "/api/shifts/set_penalty/", {"penalty": "1000"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(res.data["manual_penalty"]), Decimal("1000.00"))
        # 1000 делится на двоих → 500 с человека
        self.assertEqual(Decimal(res.data["manual_penalty_share"]), Decimal("500.00"))
        # 2000 ставка − 500 штраф (выручки нет → бонус 0)
        self.assertEqual(Decimal(res.data["payout"]), Decimal("1500.00"))

    def test_manual_penalty_can_exceed_bonus_but_not_below_zero(self):
        self.put_in_shift(self.staff["cook"])
        self.auth(self.manager)
        res = self.client.post(
            "/api/shifts/set_penalty/", {"penalty": "5000"}, format="json"
        )
        # штраф больше ставки+бонуса — выплата не уходит в минус
        self.assertEqual(Decimal(res.data["payout"]), Decimal("0.00"))

    def test_manual_penalty_in_payroll(self):
        self.put_in_shift(self.staff["cook"])
        self.auth(self.manager)
        self.client.post("/api/shifts/set_penalty/", {"penalty": "800"}, format="json")
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual(Decimal(rows[0]["penalty"]), Decimal("800.00"))
        self.assertEqual(Decimal(rows[0]["total"]), Decimal("1200.00"))  # 2000-800

    def test_set_penalty_requires_manager(self):
        self.put_in_shift(self.staff["cook"])
        self.auth(self.staff["cook"])
        res = self.client.post(
            "/api/shifts/set_penalty/", {"penalty": "100"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_set_penalty_without_shift_is_rejected(self):
        self.auth(self.manager)
        res = self.client.post(
            "/api/shifts/set_penalty/", {"penalty": "100"}, format="json"
        )
        self.assertEqual(res.status_code, 400)

    # ——— видимость ———

    def test_worker_sees_only_own_shifts_manager_sees_all(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        old = Shift.objects.create(date=yesterday, daily_rate=2000, bonus_percent=9)
        ShiftMember.objects.create(shift=old, user=self.cook2, role=User.Role.COOK)
        self.put_in_shift(self.staff["cook"])

        self.auth(self.staff["cook"])
        mine = self.client.get("/api/shifts/").data
        self.assertEqual([s["date"] for s in mine], [timezone.localdate().isoformat()])

        # менеджер и админ считают зарплату — видят все смены
        for boss in (self.manager, User.objects.create_user(
            username="boss", password="demo12345", role=User.Role.ADMIN
        )):
            self.auth(boss)
            self.assertEqual(len(self.client.get("/api/shifts/").data), 2)

    def test_payroll_sums_period(self):
        self.put_in_shift(self.staff["cook"])
        self.sale("10000")
        self.auth(self.staff["cook"])
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["days"], 1)
        # один в смене: весь бонус его
        self.assertEqual(Decimal(rows[0]["bonus"]), Decimal("900.00"))
        self.assertEqual(Decimal(rows[0]["total"]), Decimal("2900.00"))

    def test_payroll_scope_by_role(self):
        """Работник видит в сводке только себя, менеджер — всю команду."""
        self.put_in_shift(*self.staff.values())
        self.sale("10000")

        self.auth(self.staff["bar"])
        own = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual([r["user"] for r in own], [self.staff["bar"].id])

        self.auth(self.manager)
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual(len(rows), 3)
        # бонус 900 делится на троих, ставка у каждого своя
        self.assertEqual(
            sum(Decimal(r["total"]) for r in rows), Decimal("6900.00")
        )

    def test_payroll_period_bounds(self):
        """В сводку попадают только смены из периода from..to."""
        today = timezone.localdate()
        self.put_in_shift(self.staff["cook"])
        old = Shift.objects.create(
            date=today - timedelta(days=60), daily_rate=2000, bonus_percent=9
        )
        ShiftMember.objects.create(shift=old, user=self.staff["cook"])

        self.auth(self.manager)
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual(rows[0]["days"], 1)  # период по умолчанию — 30 дней

        wide = self.client.get(
            f"/api/shifts/payroll/?from={(today - timedelta(days=90)).isoformat()}"
            f"&to={today.isoformat()}"
        ).data
        self.assertEqual(wide["rows"][0]["days"], 2)
        self.assertEqual(Decimal(wide["rows"][0]["base"]), Decimal("4000.00"))

    def test_month_calendar_marks_days(self):
        today = timezone.localdate()
        self.put_in_shift(self.staff["cook"])
        # смена в прошлом месяце в календарь текущего не попадает
        past = Shift.objects.create(
            date=today.replace(day=1) - timedelta(days=1),
            daily_rate=2000,
            bonus_percent=9,
        )
        ShiftMember.objects.create(shift=past, user=self.staff["cook"])

        self.auth(self.staff["cook"])
        data = self.client.get("/api/shifts/month/").data
        self.assertEqual(data["month"], f"{today.year}-{today.month:02d}")
        self.assertEqual([d["date"] for d in data["days"]], [today.isoformat()])
        self.assertTrue(data["days"][0]["mine"])

        # чужая смена в календаре видна, но не как своя
        self.auth(self.staff["bar"])
        data = self.client.get("/api/shifts/month/").data
        self.assertFalse(data["days"][0]["mine"])

    def test_month_accepts_explicit_month(self):
        month = (timezone.localdate().replace(day=1) - timedelta(days=1)).strftime(
            "%Y-%m"
        )
        self.auth(self.manager)
        res = self.client.get(f"/api/shifts/month/?month={month}")
        self.assertEqual(res.data["month"], month)
        self.assertEqual(
            self.client.get("/api/shifts/month/?month=нет").status_code, 400
        )

    def test_staff_list_excludes_clients(self):
        self.auth(self.manager)
        names = {u["id"] for u in self.client.get("/api/shifts/staff/").data}
        self.assertIn(self.staff["cook"].id, names)
        self.assertNotIn(self.client_user.id, names)

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Category, Product, ProductVariant
from core.models import Organization, SiteSettings
from core.tenancy import organization_context
from orders.models import Order, Table
from users.models import User

from .models import Shift, ShiftMember, ShiftRate, ShiftSettings, ShiftType
from .services import add_member, get_shift, payroll, shift_report


class ShiftTests(APITestCase):
    """Смены: состав ставит менеджер, деньги считаются по выручке дня."""

    def setUp(self):
        # тесты писались до тарифов и проверяют функционал «Максимума»
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
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


class PerformerTests(APITestCase):
    """Кто именно выполнил заказ — при общем планшете это не видно из входа.

    На точке один логин на всю смену, а зарплата у барист сдельная: без
    отметки исполнителя посчитать, кто сколько сделал, нечем.
    """

    def setUp(self):
        # Заведение адресуется доменом: как только их становится двое,
        # запрос без явного домена перестаёт находить нужное.
        self.org = Organization.objects.order_by("pk").first()
        self.org.domain = "testserver"
        self.org.save()
        cat = Category.objects.create(name="Кофе", station="bar")
        product = Product.objects.create(category=cat, name="Латте")
        self.variant = ProductVariant.objects.create(product=product, price=Decimal("240"))
        self.anna = User.objects.create_user(
            "anna", password="demo12345", role=User.Role.WAITER, first_name="Анна"
        )
        self.boris = User.objects.create_user(
            "boris", password="demo12345", role=User.Role.WAITER, first_name="Борис"
        )
        self.client.force_authenticate(self.anna)

    def order(self, performer=None):
        body = {"items": [{"variant": self.variant.id, "quantity": 1}]}
        if performer is not None:
            body["performer"] = performer
        return self.client.post("/api/orders/", body, format="json")

    def close(self, order_id):
        return self.client.post(f"/api/orders/{order_id}/close/", {"pay_method": "cash"}, format="json")

    # ——— отметка исполнителя ———

    def test_performer_is_saved_with_the_order(self):
        res = self.order(performer=self.boris.id)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["performer"], self.boris.id)
        self.assertEqual(res.data["performer_name"], "Борис")

    def test_without_a_choice_it_is_the_one_who_logged_in(self):
        """В зале у каждого свой вход — там отметка верна по умолчанию."""
        self.assertEqual(self.order().data["performer"], self.anna.id)

    def test_qr_order_can_be_claimed_later(self):
        """Заказ гостя приходит ничей: его оформил гость, а делает смена."""
        order = Order.objects.create(status=Order.Status.OPEN, total=Decimal("240"))
        res = self.client.patch(
            f"/api/orders/{order.id}/performer/", {"performer": self.boris.id}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Order.objects.get(pk=order.id).performer, self.boris)

    def test_mark_can_be_removed(self):
        """Ошиблись кнопкой — снять отметку можно, поле необязательное."""
        order_id = self.order(performer=self.boris.id).data["id"]
        self.client.patch(f"/api/orders/{order_id}/performer/", {"performer": None}, format="json")
        self.assertIsNone(Order.objects.get(pk=order_id).performer)

    def test_stranger_cannot_be_written_in(self):
        """id приходит с планшета; чужой сотрудник испортил бы сдельный отчёт."""
        alien = Organization.objects.create(name="Соседи", slug="sosedi-perf", domain="sosedi.example.com")
        with organization_context(alien):
            other = User.objects.create_user(
                "чужой", password="demo12345", role=User.Role.WAITER, organization=alien
            )
        res = self.order(performer=other.id)
        # Заказ принят, но исполнителем записан тот, кто вошёл, а не чужак.
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["performer"], self.anna.id)

    # ——— счёт выполненного ———

    def test_shift_counts_orders_of_each_person(self):
        add_member(self.anna, timezone.localdate())
        add_member(self.boris, timezone.localdate())
        for _ in range(2):
            self.close(self.order(performer=self.anna.id).data["id"])
        self.close(self.order(performer=self.boris.id).data["id"])

        rows = {m["user"]: m for m in shift_report(day=timezone.localdate(), shift=get_shift(timezone.localdate()))["members"]}

        self.assertEqual(rows[self.anna.id]["orders"], 2)
        self.assertEqual(rows[self.anna.id]["orders_total"], "480.00")
        self.assertEqual(rows[self.boris.id]["orders"], 1)

    def test_open_orders_are_not_counted_yet(self):
        """Пока счёт не закрыт, выручки по нему нет — и считать нечего."""
        add_member(self.anna, timezone.localdate())
        self.order(performer=self.anna.id)
        report = shift_report(shift=get_shift(timezone.localdate()))
        self.assertEqual(report["members"][0]["orders"], 0)

    def test_work_of_those_outside_the_shift_is_not_lost(self):
        """Менеджер забыл поставить в смену, а человек работал."""
        add_member(self.anna, timezone.localdate())
        self.close(self.order(performer=self.boris.id).data["id"])

        report = shift_report(shift=get_shift(timezone.localdate()))

        self.assertEqual([o["name"] for o in report["outsiders"]], ["Борис"])
        self.assertEqual(report["outsiders"][0]["orders"], 1)

    def test_payout_still_splits_evenly(self):
        """Сдельной оплаты пока нет: цифры показываем, деньги делим как раньше."""
        add_member(self.anna, timezone.localdate())
        add_member(self.boris, timezone.localdate())
        self.close(self.order(performer=self.anna.id).data["id"])

        report = shift_report(shift=get_shift(timezone.localdate()))

        payouts = {m["payout"] for m in report["members"]}
        self.assertEqual(len(payouts), 1)

    # ——— список для выбора ———

    def test_list_puts_the_shift_first(self):
        add_member(self.boris, timezone.localdate())
        rows = self.client.get("/api/shifts/performers/").data
        self.assertEqual(rows[0]["name"], "Борис")
        self.assertTrue(rows[0]["in_shift"])
        self.assertIn("Анна", [r["name"] for r in rows])

    def test_list_is_not_empty_without_a_shift(self):
        """Иначе поле молчало бы из-за того, что менеджер не поставил смену."""
        rows = self.client.get("/api/shifts/performers/").data
        self.assertEqual({r["in_shift"] for r in rows}, {False})
        self.assertEqual(len(rows), 2)

    def test_barista_may_read_the_list(self):
        """Выбирает человек за стойкой, а не менеджер."""
        self.assertEqual(self.client.get("/api/shifts/performers/").status_code, 200)


class PaySettingsApiTests(APITestCase):
    """Правила оплаты правит владелец у себя, а не разработчик в Django-админке.

    Ставка меняется чаще, чем выходит обновление, — держать её за
    админкой значило держать владельца на коротком поводке.
    """

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user(
            "owner-pay", password="demo12345", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            "manager-pay", password="demo12345", role=User.Role.WAREHOUSE
        )
        self.waiter = User.objects.create_user(
            "waiter-pay", password="demo12345", role=User.Role.WAITER
        )
        self.client.force_authenticate(self.owner)

    def save(self, **body):
        return self.client.patch("/api/shifts/settings/", body, format="json")

    def test_owner_reads_the_rules(self):
        res = self.client.get("/api/shifts/settings/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["daily_rate"], "2000.00")
        self.assertEqual(res.data["bonus_percent"], "9.00")

    def test_owner_changes_the_rate(self):
        res = self.save(daily_rate="2500", bonus_percent="10")
        self.assertEqual(res.status_code, 200)
        cfg = ShiftSettings.load()
        self.assertEqual(cfg.daily_rate, Decimal("2500"))
        self.assertEqual(cfg.bonus_percent, Decimal("10"))

    def test_today_shift_picks_up_the_new_rate(self):
        """Иначе владелец поднял бы ставку и не увидел этого в сегодняшней смене."""
        add_member(self.waiter, timezone.localdate())
        self.save(daily_rate="2500")
        self.assertEqual(
            get_shift(timezone.localdate()).daily_rate, Decimal("2500")
        )

    def test_past_shifts_keep_their_numbers(self):
        """Прошлое — это история выплат, её правка переписала бы расчёт задним числом."""
        yesterday = timezone.localdate() - timedelta(days=1)
        add_member(self.waiter, yesterday)
        self.save(daily_rate="2500")
        self.assertEqual(get_shift(yesterday).daily_rate, Decimal("2000"))

    def test_penalty_table_is_set_and_cleared(self):
        table = Table.objects.create(name="12")
        self.save(penalty_table=table.id)
        self.assertEqual(ShiftSettings.load().penalty_table, table)

        self.save(penalty_table=None)
        self.assertIsNone(ShiftSettings.load().penalty_table)

    def test_tables_come_with_the_rules(self):
        Table.objects.create(name="3")
        self.assertEqual(
            [t["name"] for t in self.client.get("/api/shifts/settings/").data["tables"]],
            ["3"],
        )

    def test_nonsense_is_refused(self):
        self.assertEqual(self.save(daily_rate="-100").status_code, 400)
        self.assertEqual(self.save(bonus_percent="150").status_code, 400)
        self.assertEqual(self.save(daily_rate="много").status_code, 400)
        self.assertEqual(ShiftSettings.load().daily_rate, Decimal("2000"))

    def test_manager_sets_the_shift_but_not_its_price(self):
        """Состав смены — дело менеджера, стоимость рабочего дня — владельца."""
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get("/api/shifts/settings/").status_code, 403)
        self.assertEqual(self.save(daily_rate="9999").status_code, 403)

    def test_staff_cannot_touch_it(self):
        self.client.force_authenticate(self.waiter)
        self.assertEqual(self.save(daily_rate="9999").status_code, 403)


class PayrollOrdersTests(APITestCase):
    """Сводка за период показывает и деньги, и сделанное — рядом.

    Ради этих цифр бариста и отмечает себя на заказе: по ним владелец
    решает, переходить ли на сдельную оплату.
    """

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        cat = Category.objects.create(name="Кофе", station="bar")
        product = Product.objects.create(category=cat, name="Латте")
        self.variant = ProductVariant.objects.create(product=product, price=Decimal("240"))
        self.anna = User.objects.create_user(
            "anna-pr", password="demo12345", role=User.Role.WAITER, first_name="Анна"
        )
        self.owner = User.objects.create_user(
            "owner-pr", password="demo12345", role=User.Role.ADMIN
        )
        add_member(self.anna, timezone.localdate())
        self.client.force_authenticate(self.anna)

    def make_and_close(self, n=1):
        for _ in range(n):
            res = self.client.post(
                "/api/orders/",
                {"items": [{"variant": self.variant.id, "quantity": 1}], "performer": self.anna.id},
                format="json",
            )
            self.client.post(
                f"/api/orders/{res.data['id']}/close/", {"pay_method": "cash"}, format="json"
            )

    def test_period_summary_counts_orders(self):
        self.make_and_close(3)
        self.client.force_authenticate(self.owner)

        row = self.client.get("/api/shifts/payroll/").data["rows"][0]

        self.assertEqual(row["orders"], 3)
        self.assertEqual(row["orders_total"], "720.00")

    def test_worker_sees_own_numbers(self):
        """Официант видит только свою строку — и в ней своё сделанное."""
        self.make_and_close(1)
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual([r["user"] for r in rows], [self.anna.id])
        self.assertEqual(rows[0]["orders"], 1)

    def test_days_without_marks_are_zero(self):
        rows = self.client.get("/api/shifts/payroll/").data["rows"]
        self.assertEqual(rows[0]["orders"], 0)
        self.assertEqual(rows[0]["orders_total"], "0.00")


class ResultSchemeTests(APITestCase):
    """Оплата за результат, этап 1: типы смен, ставка «тип × роль», старший.

    У Монти смены по 6, 8 и 12 часов, и бариста на 12 часов стоит дороже
    официанта на 6 — одна ставка на всех тут не работает.
    """

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-rs", password="demo12345", role=User.Role.ADMIN)
        self.manager = User.objects.create_user(
            "manager-rs", password="demo12345", role=User.Role.WAREHOUSE
        )
        self.bar = User.objects.create_user(
            "bar-rs", password="demo12345", role=User.Role.BAR, first_name="Вика"
        )
        self.waiter = User.objects.create_user(
            "waiter-rs", password="demo12345", role=User.Role.WAITER, first_name="Олег"
        )
        self.cook = User.objects.create_user(
            "cook-rs", password="demo12345", role=User.Role.COOK, first_name="Гена"
        )
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.senior_bonus = Decimal("300")
        cfg.daily_rate = Decimal("2000")
        cfg.save()
        self.full = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))
        self.short = ShiftType.objects.create(
            name="Короткая", starts_at=time(14), ends_at=time(20), sort_order=1
        )
        for shift_type, role, rate in (
            (self.full, "bar", "3000"),
            (self.full, "waiter", "2500"),
            (self.short, "bar", "1600"),
        ):
            ShiftRate.objects.create(shift_type=shift_type, role=role, rate=Decimal(rate))
        self.today = timezone.localdate()

    def member(self, user, report=None):
        report = report or shift_report(shift=get_shift(self.today))
        return next(m for m in report["members"] if m["user"] == user.id)

    # ——— ставки ———

    def test_rate_depends_on_type_and_role(self):
        add_member(self.bar, self.today, shift_type=self.full)
        add_member(self.waiter, self.today, shift_type=self.full)
        report = shift_report(shift=get_shift(self.today))
        self.assertEqual(self.member(self.bar, report)["payout"], "3000.00")
        self.assertEqual(self.member(self.waiter, report)["payout"], "2500.00")

    def test_short_shift_is_cheaper(self):
        add_member(self.bar, self.today, shift_type=self.short)
        m = self.member(self.bar)
        self.assertEqual(m["payout"], "1600.00")
        self.assertEqual(m["hours"], "6.00")

    def test_empty_cell_falls_back_to_daily_rate(self):
        """Клетку «повар × короткая» не заполнили — человек не выходит на ноль."""
        add_member(self.cook, self.today, shift_type=self.short)
        self.assertEqual(self.member(self.cook)["payout"], "2000.00")

    def test_first_type_is_the_default(self):
        add_member(self.bar, self.today)
        m = self.member(self.bar)
        self.assertEqual(m["shift_type_name"], "Полная")
        self.assertEqual((m["starts_at"], m["ends_at"]), ("08:00", "20:00"))

    def test_no_percent_of_revenue(self):
        add_member(self.bar, self.today, shift_type=self.full)
        Order.objects.create(
            table="1", status=Order.Status.PAID, closed_at=timezone.now(), total=Decimal("10000")
        )
        report = shift_report(shift=get_shift(self.today))
        self.assertEqual(report["bonus_pool"], "0.00")
        self.assertEqual(self.member(self.bar, report)["payout"], "3000.00")

    def test_senior_gets_extra(self):
        add_member(self.bar, self.today, shift_type=self.full, is_senior=True)
        m = self.member(self.bar)
        self.assertEqual(m["senior_bonus"], "300.00")
        self.assertEqual(m["payout"], "3300.00")

    def test_kpi_roles_by_default(self):
        add_member(self.bar, self.today)
        add_member(self.cook, self.today)
        report = shift_report(shift=get_shift(self.today))
        self.assertTrue(self.member(self.bar, report)["in_kpi"])
        self.assertFalse(self.member(self.cook, report)["in_kpi"])

    def test_even_scheme_is_untouched(self):
        """Хуму пока платит по-старому — ставки по типам на него не влияют."""
        cfg = ShiftSettings.load()
        cfg.scheme = "even"
        cfg.save()
        add_member(self.bar, self.today, shift_type=self.full, is_senior=True)
        self.assertEqual(self.member(self.bar)["payout"], "2000.00")

    def test_night_shift_crosses_midnight(self):
        from datetime import time

        night = ShiftType.objects.create(name="Ночь", starts_at=time(20), ends_at=time(4))
        self.assertEqual(night.hours, Decimal("8.00"))

    # ——— менеджер правит человека в смене ———

    def update(self, **body):
        self.client.force_authenticate(self.manager)
        return self.client.post(
            "/api/shifts/update_member/", {"date": self.today.isoformat(), **body}, format="json"
        )

    def test_manager_changes_type_and_rate_follows(self):
        add_member(self.bar, self.today, shift_type=self.full)
        res = self.update(user=self.bar.id, shift_type=self.short.id)
        self.assertEqual(res.status_code, 200)
        m = self.member(self.bar, res.data)
        self.assertEqual(m["payout"], "1600.00")
        self.assertEqual(m["starts_at"], "14:00")

    def test_manager_marks_early_leave(self):
        add_member(self.bar, self.today, shift_type=self.full)
        res = self.update(user=self.bar.id, ends_at="17:30")
        self.assertEqual(self.member(self.bar, res.data)["hours"], "9.50")

    def test_role_in_shift_differs_from_profile(self):
        """Официант сегодня за стойкой — ставка бариста."""
        add_member(self.waiter, self.today, shift_type=self.full)
        res = self.update(user=self.waiter.id, role="bar")
        m = self.member(self.waiter, res.data)
        self.assertEqual(m["role"], "bar")
        self.assertEqual(m["payout"], "3000.00")

    def test_manager_marks_senior(self):
        add_member(self.bar, self.today, shift_type=self.full)
        res = self.update(user=self.bar.id, is_senior=True)
        self.assertEqual(self.member(self.bar, res.data)["payout"], "3300.00")

    def test_add_member_with_type_via_api(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post(
            "/api/shifts/add_member/",
            {"user": self.bar.id, "shift_type": self.short.id, "is_senior": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.member(self.bar, res.data)["payout"], "1900.00")

    def test_bad_input_is_refused(self):
        add_member(self.bar, self.today)
        self.assertEqual(self.update(user=self.bar.id, ends_at="25:99").status_code, 400)
        self.assertEqual(self.update(user=self.bar.id, role="client").status_code, 400)
        self.assertEqual(self.update(user=self.bar.id, shift_type=999999).status_code, 400)
        self.assertEqual(self.update(user=self.cook.id, is_senior=True).status_code, 404)

    def test_worker_cannot_update(self):
        add_member(self.bar, self.today)
        self.client.force_authenticate(self.bar)
        res = self.client.post(
            "/api/shifts/update_member/",
            {"date": self.today.isoformat(), "user": self.bar.id, "is_senior": True},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    # ——— владелец задаёт типы и ставки ———

    def test_owner_creates_type_with_rates(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            "/api/shifts/types/",
            {"name": "Дневная", "starts_at": "10:00", "ends_at": "18:00", "rates": {"bar": "2200"}},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        created = next(t for t in res.data["shift_types"] if t["name"] == "Дневная")
        self.assertEqual(created["hours"], "8.00")
        self.assertEqual(created["rates"], {"bar": "2200.00"})

    def test_rate_change_reaches_today_but_not_yesterday(self):
        yesterday = self.today - timedelta(days=1)
        add_member(self.bar, yesterday, shift_type=self.full)
        add_member(self.bar, self.today, shift_type=self.full)
        self.client.force_authenticate(self.owner)
        self.client.patch(
            f"/api/shifts/types/{self.full.id}/", {"rates": {"bar": "3500"}}, format="json"
        )
        self.assertEqual(self.member(self.bar)["payout"], "3500.00")
        past = shift_report(shift=get_shift(yesterday))
        self.assertEqual(self.member(self.bar, past)["payout"], "3000.00")

    def test_deleted_type_keeps_history(self):
        yesterday = self.today - timedelta(days=1)
        add_member(self.bar, yesterday, shift_type=self.full)
        self.client.force_authenticate(self.owner)
        self.client.delete(f"/api/shifts/types/{self.full.id}/")
        m = self.member(self.bar, shift_report(shift=get_shift(yesterday)))
        self.assertEqual(m["shift_type_name"], "Полная")
        self.assertEqual(m["payout"], "3000.00")

    def test_rate_cell_can_be_cleared(self):
        self.client.force_authenticate(self.owner)
        res = self.client.patch(
            f"/api/shifts/types/{self.full.id}/", {"rates": {"waiter": ""}}, format="json"
        )
        full = next(t for t in res.data["shift_types"] if t["id"] == self.full.id)
        self.assertNotIn("waiter", full["rates"])

    def test_type_validation(self):
        self.client.force_authenticate(self.owner)
        bad = [
            {"name": "", "starts_at": "08:00", "ends_at": "20:00"},
            {"name": "X", "starts_at": "08:00", "ends_at": "08:00"},
            {"name": "X", "starts_at": "утро", "ends_at": "20:00"},
            {"name": "X", "starts_at": "08:00", "ends_at": "20:00", "rates": {"bar": "-1"}},
        ]
        for body in bad:
            self.assertEqual(self.client.post("/api/shifts/types/", body, format="json").status_code, 400)

    def test_manager_cannot_touch_types(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post(
            "/api/shifts/types/", {"name": "X", "starts_at": "08:00", "ends_at": "20:00"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_owner_switches_scheme_and_sets_senior(self):
        self.client.force_authenticate(self.owner)
        res = self.client.patch(
            "/api/shifts/settings/",
            {"scheme": "even", "senior_bonus": "500", "kpi_roles": ["bar"]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["scheme"], "even")
        self.assertEqual(res.data["kpi_roles"], ["bar"])
        self.assertEqual(
            self.client.patch("/api/shifts/settings/", {"scheme": "piecework"}, format="json").status_code,
            400,
        )

    def test_payroll_sums_individual_rates(self):
        yesterday = self.today - timedelta(days=1)
        add_member(self.bar, yesterday, shift_type=self.full, is_senior=True)
        add_member(self.bar, self.today, shift_type=self.short)
        rows = payroll(Shift.objects.all())
        self.assertEqual(rows[0]["base"], "4600.00")
        self.assertEqual(rows[0]["bonus"], "300.00")
        self.assertEqual(rows[0]["hours"], "18.00")
        self.assertEqual(rows[0]["total"], "4900.00")

    # ——— что видит менеджер ———

    def test_manager_gets_types_without_rates(self):
        self.client.force_authenticate(self.manager)
        res = self.client.get("/api/shifts/options/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([t["name"] for t in res.data["shift_types"]], ["Полная", "Короткая"])
        self.assertNotIn("rates", res.data["shift_types"][0])

    def test_worker_has_no_options(self):
        self.client.force_authenticate(self.bar)
        self.assertEqual(self.client.get("/api/shifts/options/").status_code, 403)

    # ——— найдено на вычитке ———

    def test_fixing_yesterdays_type_fixes_its_rate(self):
        """Вчера забыли поставить короткую — поправили, и платим как за короткую."""
        yesterday = self.today - timedelta(days=1)
        add_member(self.bar, yesterday, shift_type=self.full)
        self.client.force_authenticate(self.manager)
        res = self.client.post(
            "/api/shifts/update_member/",
            {"date": yesterday.isoformat(), "user": self.bar.id, "shift_type": self.short.id},
            format="json",
        )
        self.assertEqual(self.member(self.bar, res.data)["payout"], "1600.00")

    def test_marking_senior_yesterday_keeps_old_rate(self):
        """Старший и время ставку не пересчитывают: прошлая ставка — история."""
        yesterday = self.today - timedelta(days=1)
        add_member(self.bar, yesterday, shift_type=self.full)
        ShiftRate.objects.filter(shift_type=self.full, role="bar").update(rate=Decimal("9999"))
        self.client.force_authenticate(self.manager)
        res = self.client.post(
            "/api/shifts/update_member/",
            {"date": yesterday.isoformat(), "user": self.bar.id, "is_senior": True},
            format="json",
        )
        self.assertEqual(self.member(self.bar, res.data)["payout"], "3300.00")

    def test_type_in_open_shift_cannot_be_deleted(self):
        """Иначе ставка людей в сегодняшней смене молча упала бы до общей."""
        add_member(self.bar, self.today, shift_type=self.full)
        self.client.force_authenticate(self.owner)
        res = self.client.delete(f"/api/shifts/types/{self.full.id}/")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(ShiftType.objects.filter(pk=self.full.id).exists())

    def test_new_type_time_reaches_today_but_not_manual_marks(self):
        from datetime import time

        add_member(self.bar, self.today, shift_type=self.full)
        _, early = add_member(self.waiter, self.today, shift_type=self.full)
        from .services import update_member

        update_member(early, ends_at=time(17))
        self.client.force_authenticate(self.owner)
        self.client.patch(
            f"/api/shifts/types/{self.full.id}/",
            {"starts_at": "09:00", "ends_at": "21:00"},
            format="json",
        )
        report = shift_report(shift=get_shift(self.today))
        bar = self.member(self.bar, report)
        waiter = self.member(self.waiter, report)
        self.assertEqual((bar["starts_at"], bar["ends_at"]), ("09:00", "21:00"))
        # ушёл в 17:00 — это факт, правка типа его не перетирает
        self.assertEqual((waiter["starts_at"], waiter["ends_at"]), ("09:00", "17:00"))

    def test_equal_times_are_refused(self):
        add_member(self.bar, self.today, shift_type=self.full)
        self.assertEqual(
            self.update(user=self.bar.id, starts_at="10:00", ends_at="10:00").status_code, 400
        )


class KpiTests(APITestCase):
    """Бонус за КПД — этап 2. Цифры из ТЗ Монти.

    Сетка Монти: до 2 500 ₽/ч — 0, от 2 500 — 500, от 3 500 — 700,
    от 5 000 — 900, от 6 500 — 1 100, от 8 000 — 1 300, от 10 000 — 1 500.
    """

    GRID = [
        {"from": "2500", "bonus": "500"},
        {"from": "3500", "bonus": "700"},
        {"from": "5000", "bonus": "900"},
        {"from": "6500", "bonus": "1100"},
        {"from": "8000", "bonus": "1300"},
        {"from": "10000", "bonus": "1500"},
    ]

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-k", password="demo12345", role=User.Role.ADMIN)
        self.vika = User.objects.create_user("vika-k", password="demo12345", role=User.Role.BAR)
        self.dima = User.objects.create_user("dima-k", password="demo12345", role=User.Role.BAR)
        self.gena = User.objects.create_user("gena-k", password="demo12345", role=User.Role.COOK)
        self.olga = User.objects.create_user("olga-k", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.daily_rate = Decimal("2000")
        cfg.kpi_grid = self.GRID
        cfg.penalty_table = Table.objects.create(name="Штраф")
        cfg.save()
        self.shift_type = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))
        self.day = timezone.localdate() - timedelta(days=1)

    def at(self, hh, mm=0):
        from datetime import datetime, time

        return timezone.make_aware(datetime.combine(self.day, time(hh, mm)))

    def put(self, user, start, end):
        from datetime import time

        from .services import update_member

        _, m = add_member(user, self.day, shift_type=self.shift_type)
        return update_member(m, starts_at=time(*start), ends_at=time(*end))

    def sale(self, total, hh, mm=0, **kw):
        return Order.objects.create(
            table=kw.pop("table", "1"),
            status=kw.pop("status", Order.Status.PAID),
            closed_at=self.at(hh, mm),
            total=Decimal(total),
            **kw,
        )

    def report(self):
        return shift_report(shift=get_shift(self.day))

    def row(self, user, report=None):
        report = report or self.report()
        return next(m for m in report["members"] if m["user"] == user.id)

    def test_example_from_the_spec(self):
        """ТЗ: 3 часа один — 15 000, потом 2 часа вдвоём — 10 000.

        Личная выручка 15 000 + 5 000 = 20 000, за 5 часов КПД 4 000 → 700 ₽.
        """
        self.put(self.vika, (9, 0), (14, 0))
        self.put(self.dima, (12, 0), (20, 0))
        for hh in (9, 10, 11):
            self.sale("5000", hh, 30)
        self.sale("6000", 12, 30)
        self.sale("4000", 13, 30)

        vika = self.row(self.vika)
        self.assertEqual(vika["kpi_revenue"], "20000.00")
        self.assertEqual(vika["kpi"], "4000.00")
        self.assertEqual(vika["kpi_bonus"], "700.00")
        self.assertEqual(vika["payout"], "2700.00")

    def test_three_on_shift_split_in_thirds(self):
        for u in (self.vika, self.dima, self.olga):
            self.put(u, (8, 0), (20, 0))
        self.sale("3000", 12)
        report = self.report()
        self.assertEqual({self.row(u, report)["kpi_revenue"] for u in (self.vika, self.dima, self.olga)}, {"1000.00"})

    def test_step_starts_inclusive(self):
        """Ровно 3 500 ₽/ч — это уже ступень 700, а не 500."""
        self.put(self.vika, (8, 0), (10, 0))
        self.sale("7000", 9)
        self.assertEqual(self.row(self.vika)["kpi_bonus"], "700.00")

    def test_below_first_step_is_zero(self):
        self.put(self.vika, (8, 0), (20, 0))
        self.sale("1000", 9)
        vika = self.row(self.vika)
        self.assertEqual(vika["kpi_bonus"], "0.00")
        self.assertEqual(vika["kpi_next"], {"from": "2500.00", "bonus": "500.00"})

    def test_top_step_has_no_next(self):
        self.put(self.vika, (8, 0), (9, 0))
        self.sale("12000", 8, 30)
        vika = self.row(self.vika)
        self.assertEqual(vika["kpi_bonus"], "1500.00")
        self.assertIsNone(vika["kpi_next"])

    def test_cook_is_not_in_kpi_and_takes_no_share(self):
        """Повар получает ставку, выручку барист не разбавляет."""
        self.put(self.vika, (8, 0), (20, 0))
        self.put(self.gena, (8, 0), (20, 0))
        self.sale("6000", 12)
        report = self.report()
        self.assertEqual(self.row(self.vika, report)["kpi_revenue"], "6000.00")
        self.assertIsNone(self.row(self.gena, report)["kpi"])
        self.assertEqual(self.row(self.gena, report)["payout"], "2000.00")

    def test_shift_end_is_exclusive(self):
        """Заказ закрыт в 14:00 ровно — он того, кто пришёл, а не того, кто ушёл."""
        self.put(self.vika, (8, 0), (14, 0))
        self.put(self.dima, (14, 0), (20, 0))
        self.sale("1000", 14)
        report = self.report()
        self.assertEqual(self.row(self.vika, report)["kpi_revenue"], "0.00")
        self.assertEqual(self.row(self.dima, report)["kpi_revenue"], "1000.00")

    def test_nobody_on_shift_is_unassigned(self):
        self.put(self.vika, (8, 0), (14, 0))
        self.sale("1000", 16)
        report = self.report()
        self.assertEqual(report["kpi_unassigned"], "1000.00")
        self.assertEqual(self.row(self.vika, report)["kpi_revenue"], "0.00")

    def test_night_shift_takes_orders_after_midnight(self):
        from datetime import datetime, time

        self.put(self.vika, (20, 0), (2, 0))
        Order.objects.create(
            table="1",
            status=Order.Status.PAID,
            closed_at=timezone.make_aware(datetime.combine(self.day + timedelta(days=1), time(1))),
            total=Decimal("3000"),
        )
        vika = self.row(self.vika)
        self.assertEqual(vika["kpi_revenue"], "3000.00")
        self.assertEqual(vika["kpi"], "500.00")

    def test_same_day_refund_is_out(self):
        self.put(self.vika, (8, 0), (20, 0))
        self.sale("5000", 12, status=Order.Status.REFUNDED, refunded_at=self.at(13))
        self.assertEqual(self.row(self.vika)["kpi_revenue"], "0.00")

    def test_later_refund_does_not_rewrite_the_shift(self):
        self.put(self.vika, (8, 0), (20, 0))
        self.sale(
            "5000", 12, status=Order.Status.REFUNDED,
            refunded_at=self.at(12) + timedelta(days=1),
        )
        self.assertEqual(self.row(self.vika)["kpi_revenue"], "5000.00")

    def test_penalty_table_is_not_revenue(self):
        self.put(self.vika, (8, 0), (20, 0))
        self.sale("5000", 12, table="Штраф")
        self.assertEqual(self.row(self.vika)["kpi_revenue"], "0.00")

    def test_open_orders_do_not_count(self):
        self.put(self.vika, (8, 0), (20, 0))
        self.sale("5000", 12, status=Order.Status.OPEN)
        self.assertEqual(self.row(self.vika)["kpi_revenue"], "0.00")

    def test_early_leave_raises_kpi(self):
        """Ушла раньше — часов меньше, выручка та же: КПД честно выше."""
        self.put(self.vika, (8, 0), (12, 0))
        self.sale("10000", 9)
        self.assertEqual(self.row(self.vika)["kpi"], "2500.00")

    def test_kpi_goes_into_payroll(self):
        self.put(self.vika, (8, 0), (10, 0))
        self.sale("7000", 9)
        rows = payroll(Shift.objects.all())
        self.assertEqual(rows[0]["bonus"], "700.00")
        self.assertEqual(rows[0]["total"], "2700.00")

    def test_even_scheme_has_no_kpi(self):
        cfg = ShiftSettings.load()
        cfg.scheme = "even"
        cfg.save()
        self.put(self.vika, (8, 0), (10, 0))
        Shift.objects.update(scheme="even")
        self.sale("7000", 9)
        vika = self.row(self.vika)
        self.assertIsNone(vika["kpi"])

    def test_grid_is_snapshotted(self):
        """Правка сетки не переписывает вчерашний бонус."""
        self.put(self.vika, (8, 0), (10, 0))
        self.sale("7000", 9)
        self.client.force_authenticate(self.owner)
        self.client.patch(
            "/api/shifts/settings/", {"kpi_grid": [{"from": "1", "bonus": "9999"}]}, format="json"
        )
        self.assertEqual(self.row(self.vika)["kpi_bonus"], "700.00")

    def test_grid_api_sorts_and_validates(self):
        self.client.force_authenticate(self.owner)
        res = self.client.patch(
            "/api/shifts/settings/",
            {"kpi_grid": [{"from": "5000", "bonus": "900"}, {"from": "2500", "bonus": "500"}]},
            format="json",
        )
        self.assertEqual([s["from"] for s in res.data["kpi_grid"]], ["2500.00", "5000.00"])
        for bad in (
            [{"from": "2500", "bonus": "500"}, {"from": "2500", "bonus": "700"}],
            [{"from": "-1", "bonus": "500"}],
            [{"from": "NaN", "bonus": "500"}],
            "сетка",
        ):
            res = self.client.patch("/api/shifts/settings/", {"kpi_grid": bad}, format="json")
            self.assertEqual(res.status_code, 400, bad)

    def test_payroll_itemizes_bonuses(self):
        """По ведомости выдают деньги — КПД и старшему видны по отдельности."""
        from .services import update_member

        member = self.put(self.vika, (8, 0), (10, 0))
        update_member(member, is_senior=True)
        Shift.objects.update(senior_bonus=Decimal("300"))
        self.sale("7000", 9)
        row = payroll(Shift.objects.all())[0]
        self.assertEqual((row["kpi_bonus"], row["senior_bonus"], row["bonus"]), ("700.00", "300.00", "1000.00"))


class FocusTests(APITestCase):
    """Фокусные позиции — этап 3: надбавка исполнителю за каждую штуку."""

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-f", password="demo12345", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("manager-f", password="demo12345", role=User.Role.WAREHOUSE)
        self.vika = User.objects.create_user("vika-f", password="demo12345", role=User.Role.BAR)
        self.dima = User.objects.create_user("dima-f", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.daily_rate = Decimal("2000")
        cfg.penalty_table = Table.objects.create(name="Штраф")
        cfg.save()
        self.shift_type = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))
        cat = Category.objects.create(name="Кофе", station="bar")
        self.raf = Product.objects.create(category=cat, name="Раф")
        self.raf_s = ProductVariant.objects.create(product=self.raf, label="0,3", price=Decimal("250"))
        self.raf_l = ProductVariant.objects.create(product=self.raf, label="0,4", price=Decimal("300"))
        latte = Product.objects.create(category=cat, name="Латте")
        self.latte = ProductVariant.objects.create(product=latte, price=Decimal("240"))
        self.day = timezone.localdate() - timedelta(days=1)
        for u in (self.vika, self.dima):
            add_member(u, self.day, shift_type=self.shift_type)

    def at(self, hh, day=None):
        from datetime import datetime, time

        return timezone.make_aware(datetime.combine(day or self.day, time(hh)))

    def focus(self, bonus, variant=None, start=8, end=20, product=None):
        from .models import FocusItem

        return FocusItem.objects.create(
            product=product or self.raf, variant=variant, bonus=Decimal(bonus),
            starts_at=self.at(start), ends_at=self.at(end),
        )

    def sell(self, variant, qty=1, performer=None, hh=12, **kw):
        from orders.models import OrderItem

        order = Order.objects.create(
            table=kw.pop("table", "1"), status=kw.pop("status", Order.Status.PAID),
            closed_at=self.at(hh), total=variant.price * qty,
            performer=performer if performer is not None else self.vika, **kw,
        )
        OrderItem.objects.create(order=order, variant=variant, quantity=qty, unit_price=variant.price)
        return order

    def row(self, user):
        report = shift_report(shift=get_shift(self.day))
        return next(m for m in report["members"] if m["user"] == user.id)

    def test_each_piece_pays(self):
        self.focus("50")
        self.sell(self.raf_s, qty=2)
        self.sell(self.raf_l)
        self.sell(self.latte)
        vika = self.row(self.vika)
        self.assertEqual((vika["focus_count"], vika["focus_bonus"]), (3, "150.00"))
        self.assertEqual(vika["payout"], "2150.00")
        self.assertEqual(vika["focus_items"], [{"title": "Раф", "count": 3}])

    def test_goes_to_the_performer(self):
        self.focus("50")
        self.sell(self.raf_s, performer=self.dima)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)
        self.assertEqual(self.row(self.dima)["focus_bonus"], "50.00")

    def test_no_performer_no_bonus(self):
        self.focus("50")
        order = self.sell(self.raf_s)
        Order.objects.filter(pk=order.pk).update(performer=None)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)

    def test_specific_volume_wins(self):
        """«Раф любой» по 30 и «Раф 0,4» по 80 — за большой платим 80."""
        self.focus("30")
        self.focus("80", variant=self.raf_l)
        self.sell(self.raf_l)
        self.sell(self.raf_s)
        self.assertEqual(self.row(self.vika)["focus_bonus"], "110.00")

    def test_only_this_volume(self):
        self.focus("80", variant=self.raf_l)
        self.sell(self.raf_s)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)

    def test_bonus_at_the_moment_of_sale(self):
        """До 14:00 — 50, после — 70: утренняя продажа остаётся по 50."""
        self.focus("50", end=14)
        self.focus("70", start=14)
        self.sell(self.raf_s, hh=10)
        self.sell(self.raf_s, hh=15)
        self.assertEqual(self.row(self.vika)["focus_bonus"], "120.00")

    def test_outside_period_not_counted(self):
        self.focus("50", start=13)
        self.sell(self.raf_s, hh=10)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)

    def test_refund_same_day_and_penalty_table_are_out(self):
        self.focus("50")
        self.sell(self.raf_s, status=Order.Status.REFUNDED, refunded_at=self.at(13))
        self.sell(self.raf_s, table="Штраф")
        self.sell(self.raf_s, status=Order.Status.OPEN)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)

    def test_in_payroll_itemized(self):
        self.focus("50")
        self.sell(self.raf_s, qty=2)
        row = next(r for r in payroll(Shift.objects.all()) if r["user"] == self.vika.id)
        self.assertEqual((row["focus_bonus"], row["focus_count"], row["bonus"]), ("100.00", 2, "100.00"))

    def test_even_scheme_ignores_focus(self):
        Shift.objects.update(scheme="even", bonus_percent=Decimal("0"))
        self.focus("50")
        self.sell(self.raf_s)
        self.assertEqual(self.row(self.vika)["focus_count"], 0)

    # ——— API ———

    def api(self, user, method, url, body=None):
        self.client.force_authenticate(user)
        return getattr(self.client, method)(url, body, format="json")

    def create(self, **body):
        today = timezone.localdate()
        data = {
            "product": self.raf.id, "bonus": "50",
            "date_from": today.isoformat(), "date_to": (today + timedelta(days=6)).isoformat(),
            **body,
        }
        return self.api(self.manager, "post", "/api/shifts/focus/", data)

    def test_manager_creates_and_staff_sees(self):
        res = self.create()
        self.assertEqual(res.status_code, 201)
        res = self.api(self.vika, "get", "/api/shifts/focus/")
        self.assertEqual([i["title"] for i in res.data["items"]], ["Раф"])
        self.assertNotIn("products", res.data)
        self.assertFalse(res.data["can_edit"])

    def test_staff_cannot_create(self):
        self.client.force_authenticate(self.vika)
        res = self.client.post("/api/shifts/focus/", {"product": self.raf.id}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_today_start_is_not_retroactive(self):
        """Завели в обед — утренние продажи сегодня надбавку не получают."""
        from .models import FocusItem

        self.create()
        self.assertGreaterEqual(FocusItem.objects.get().starts_at, timezone.now() - timedelta(minutes=1))

    def test_bonus_change_splits_the_record(self):
        from .models import FocusItem

        self.create()
        item = FocusItem.objects.get()
        FocusItem.objects.filter(pk=item.pk).update(starts_at=timezone.now() - timedelta(hours=3))
        res = self.api(self.manager, "patch", f"/api/shifts/focus/{item.id}/", {"bonus": "80"})
        self.assertEqual(res.status_code, 200)
        old, new = FocusItem.objects.order_by("starts_at")
        self.assertEqual((old.bonus, new.bonus), (Decimal("50"), Decimal("80")))
        self.assertEqual(old.ends_at, new.starts_at)
        self.assertEqual([i["bonus"] for i in res.data["items"]], ["80.00"])

    def test_future_item_is_edited_in_place(self):
        from .models import FocusItem

        tomorrow = timezone.localdate() + timedelta(days=1)
        self.create(date_from=tomorrow.isoformat())
        item = FocusItem.objects.get()
        self.api(self.manager, "patch", f"/api/shifts/focus/{item.id}/", {"bonus": "80"})
        self.assertEqual(FocusItem.objects.count(), 1)
        self.assertEqual(FocusItem.objects.get().bonus, Decimal("80"))

    def test_remove_ends_now_or_deletes_future(self):
        from .models import FocusItem

        self.create()
        running = FocusItem.objects.get()
        FocusItem.objects.filter(pk=running.pk).update(starts_at=timezone.now() - timedelta(hours=1))
        res = self.api(self.manager, "delete", f"/api/shifts/focus/{running.id}/")
        self.assertEqual(res.data["items"], [])
        self.assertTrue(FocusItem.objects.filter(pk=running.pk).exists())  # история цела

        tomorrow = timezone.localdate() + timedelta(days=1)
        self.create(product=self.raf.id, variant=self.raf_l.id, date_from=tomorrow.isoformat())
        future = FocusItem.objects.get(variant=self.raf_l)
        self.api(self.manager, "delete", f"/api/shifts/focus/{future.id}/")
        self.assertFalse(FocusItem.objects.filter(pk=future.pk).exists())

    def test_finished_item_is_history(self):
        item = self.focus("50")
        res = self.api(self.manager, "patch", f"/api/shifts/focus/{item.id}/", {"bonus": "999"})
        self.assertEqual(res.status_code, 400)
        res = self.api(self.manager, "delete", f"/api/shifts/focus/{item.id}/")
        self.assertEqual(res.status_code, 400)

    def test_validation(self):
        today = timezone.localdate()
        self.assertEqual(self.create(bonus="0").status_code, 400)
        self.assertEqual(self.create(bonus="-5").status_code, 400)
        self.assertEqual(self.create(product=999999).status_code, 400)
        self.assertEqual(self.create(variant=self.latte.id).status_code, 400)
        self.assertEqual(
            self.create(date_to=(today - timedelta(days=1)).isoformat()).status_code, 400
        )
        self.assertEqual(self.create(date_from="завтра").status_code, 400)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 400)  # то же на те же дни
        self.assertEqual(self.create(variant=self.raf_l.id).status_code, 201)  # конкретный объём — можно

    def test_products_for_the_picker(self):
        res = self.api(self.manager, "get", "/api/shifts/focus/")
        raf = next(p for p in res.data["products"] if p["name"] == "Раф")
        self.assertEqual([v["label"] for v in raf["variants"]], ["0,3", "0,4"])


class ShiftsAdminTests(APITestCase):
    """Django-админка не должна обходить правила приложения."""

    def setUp(self):
        from datetime import time

        from django.contrib.admin.sites import site as admin_site

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.site = admin_site
        self.bar = User.objects.create_user("bar-adm", password="demo12345", role=User.Role.BAR)
        self.full = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))

    def test_bad_grid_is_refused(self):
        from .admin import ShiftSettingsForm

        cfg = ShiftSettings.load()
        data = {
            "organization": cfg.organization_id, "scheme": "result", "senior_bonus": "0",
            "kpi_roles": '["bar"]', "daily_rate": "2000", "bonus_percent": "0",
            "kpi_grid": '[{"from": "2500", "bonus": "500"}, {"from": "2500", "bonus": "700"}]',
        }
        form = ShiftSettingsForm(data, instance=cfg)
        self.assertFalse(form.is_valid())
        self.assertIn("kpi_grid", form.errors)

    def test_type_in_open_shift_cannot_be_deleted(self):
        from .admin import ShiftTypeAdmin

        add_member(self.bar, timezone.localdate(), shift_type=self.full)
        admin = ShiftTypeAdmin(ShiftType, self.site)
        self.assertFalse(admin.has_delete_permission(None, self.full))

    def test_started_focus_item_is_view_only(self):
        from catalog.models import Category, Product
        from django.test import RequestFactory

        from .admin import FocusItemAdmin
        from .models import FocusItem

        owner = User.objects.create_superuser("root-adm", password="demo12345")
        request = RequestFactory().get("/")
        request.user = owner
        product = Product.objects.create(category=Category.objects.create(name="К"), name="Раф")
        now = timezone.now()
        started = FocusItem.objects.create(
            product=product, bonus=Decimal("50"), starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=1),
        )
        future = FocusItem.objects.create(
            product=product, bonus=Decimal("50"), starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=3),
        )
        admin = FocusItemAdmin(FocusItem, self.site)
        self.assertFalse(admin.has_change_permission(request, started))
        self.assertFalse(admin.has_delete_permission(request, started))
        self.assertTrue(admin.has_change_permission(request, future))
        self.assertNotIn("delete_selected", admin.get_actions(request))

    def test_equal_type_times_are_invalid(self):
        from datetime import time

        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ShiftType(name="X", starts_at=time(10), ends_at=time(10)).clean()

    def test_settings_form_builds_without_selected_organization(self):
        """На проде заведений несколько, а admin.py грузится до запроса.

        Форма с fields="__all__" строила при импорте поля для внешних
        ключей — запрос через тенантный менеджер без выбранного заведения,
        и backend падал при старте (30.09.2026). Локально заведение одно,
        и старые тесты этого не видели.
        """
        from core.tenancy import _current

        from .admin import ShiftSettingsForm

        Organization.objects.create(name="Второе", slug="vtoroe-adm", domain="vtoroe.example.com")
        token = _current.set(None)
        try:
            type("Again", (ShiftSettingsForm,), {"Meta": ShiftSettingsForm.Meta})
        finally:
            _current.reset(token)


class UpsellTests(APITestCase):
    """Допродажи — этап 4: надбавка исполнителю за платную опцию."""

    def setUp(self):
        from datetime import time

        from catalog.models import Modifier, ModifierGroup

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-u", password="demo12345", role=User.Role.ADMIN)
        self.vika = User.objects.create_user("vika-u", password="demo12345", role=User.Role.BAR, first_name="Вика")
        self.dima = User.objects.create_user("dima-u", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.daily_rate = Decimal("2000")
        cfg.penalty_table = Table.objects.create(name="Штраф")
        cfg.save()
        shift_type = ShiftType.objects.create(name="Полная", starts_at=time(0, 1), ends_at=time(23, 59))
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte_p = Product.objects.create(category=cat, name="Латте")
        self.latte = ProductVariant.objects.create(product=self.latte_p, price=Decimal("240"))
        extras = ModifierGroup.objects.create(name="Добавки", max_choices=3)
        extras.products.add(self.latte_p)
        self.syrup = Modifier.objects.create(group=extras, name="Сироп", price_delta=Decimal("50"), upsell_bonus=Decimal("20"))
        self.oat = Modifier.objects.create(group=extras, name="Овсяное", price_delta=Decimal("60"), upsell_bonus=Decimal("30"))
        self.nosugar = Modifier.objects.create(group=extras, name="Без сахара", price_delta=Decimal("0"))
        self.today = timezone.localdate()
        for u in (self.vika, self.dima):
            add_member(u, self.today, shift_type=shift_type)
        # заказ оформляет касса, исполнитель — бариста
        self.cashier = User.objects.create_user("cashier-u", password="demo12345", role=User.Role.WAITER)
        self.client.force_authenticate(self.cashier)

    def order(self, mods, qty=1, performer=None, close=True):
        res = self.client.post(
            "/api/orders/",
            {
                "items": [{"variant": self.latte.id, "quantity": qty, "modifiers": [m.id for m in mods]}],
                "performer": (performer or self.vika).id,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        if close:
            self.client.post(f"/api/orders/{res.data['id']}/close/", {"pay_method": "cash"}, format="json")
        return res.data["id"]

    def row(self, user):
        report = shift_report(shift=get_shift(self.today))
        return next(m for m in report["members"] if m["user"] == user.id)

    def test_each_option_and_portion_pays(self):
        self.order([self.syrup, self.oat], qty=2)
        self.order([self.nosugar])
        vika = self.row(self.vika)
        self.assertEqual(vika["upsell_count"], 4)
        self.assertEqual(vika["upsell_bonus"], "100.00")  # (20 + 30) × 2
        self.assertEqual(
            vika["upsell_items"], [{"title": "Овсяное", "count": 2}, {"title": "Сироп", "count": 2}]
        )
        self.assertEqual(vika["payout"], "2100.00")

    def test_goes_to_the_performer(self):
        self.order([self.syrup], performer=self.dima)
        self.assertEqual(self.row(self.vika)["upsell_count"], 0)
        self.assertEqual(self.row(self.dima)["upsell_bonus"], "20.00")

    def test_bonus_as_it_was_when_chosen(self):
        """Подняли надбавку — проданное раньше остаётся по старой."""
        self.order([self.syrup])
        self.syrup.upsell_bonus = Decimal("99")
        self.syrup.save()
        self.order([self.syrup])
        self.assertEqual(self.row(self.vika)["upsell_bonus"], "119.00")

    def test_open_orders_do_not_count(self):
        self.order([self.syrup], close=False)
        self.assertEqual(self.row(self.vika)["upsell_count"], 0)

    def test_same_day_refund_is_out(self):
        order_id = self.order([self.syrup])
        Order.objects.filter(pk=order_id).update(
            status=Order.Status.REFUNDED, refunded_at=timezone.now()
        )
        self.assertEqual(self.row(self.vika)["upsell_count"], 0)

    def test_in_payroll_itemized(self):
        self.order([self.oat])
        row = next(r for r in payroll(Shift.objects.all()) if r["user"] == self.vika.id)
        self.assertEqual((row["upsell_bonus"], row["upsell_count"], row["bonus"]), ("30.00", 1, "30.00"))

    def test_even_scheme_ignores_upsell(self):
        Shift.objects.update(scheme="even", bonus_percent=Decimal("0"))
        self.order([self.syrup])
        self.assertEqual(self.row(self.vika)["upsell_count"], 0)

    # ——— меню ———

    def test_guest_does_not_see_the_bonus(self):
        self.client.force_authenticate(None)
        product = self.client.get(f"/api/products/{self.latte_p.id}/").data
        mod = product["modifier_groups"][0]["modifiers"][0]
        self.assertNotIn("upsell_bonus", mod)

    def test_staff_does_not_see_the_bonus_but_owner_does(self):
        product = self.client.get(f"/api/products/{self.latte_p.id}/").data
        self.assertNotIn("upsell_bonus", product["modifier_groups"][0]["modifiers"][0])
        self.client.force_authenticate(self.owner)
        product = self.client.get(f"/api/products/{self.latte_p.id}/").data
        self.assertIn("upsell_bonus", product["modifier_groups"][0]["modifiers"][0])

    def test_owner_sets_bonus_in_the_menu_editor(self):
        from catalog.models import ModifierGroup

        self.client.force_authenticate(self.owner)
        group = ModifierGroup.objects.get()
        mods = [
            {"id": self.syrup.id, "name": "Сироп", "price_delta": "50", "upsell_bonus": "25"},
            {"id": self.oat.id, "name": "Овсяное", "price_delta": "60"},  # без поля — не трогаем
            {"id": self.nosugar.id, "name": "Без сахара", "price_delta": "0"},
        ]
        res = self.client.patch(f"/api/modifier-groups/{group.id}/", {"modifiers": mods}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.syrup.refresh_from_db()
        self.oat.refresh_from_db()
        self.assertEqual((self.syrup.upsell_bonus, self.oat.upsell_bonus), (Decimal("25"), Decimal("30")))

    def test_negative_bonus_is_refused(self):
        from catalog.models import ModifierGroup

        self.client.force_authenticate(self.owner)
        group = ModifierGroup.objects.get()
        mods = [{"id": self.syrup.id, "name": "Сироп", "upsell_bonus": "-5"}]
        res = self.client.patch(f"/api/modifier-groups/{group.id}/", {"modifiers": mods}, format="json")
        self.assertEqual(res.status_code, 400)


class PayPrivacyTests(APITestCase):
    """При оплате за результат сотрудник не видит денег коллег."""

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.manager = User.objects.create_user("man-p", password="demo12345", role=User.Role.WAREHOUSE)
        self.vika = User.objects.create_user("vika-p", password="demo12345", role=User.Role.BAR)
        self.dima = User.objects.create_user("dima-p", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.save()
        full = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))
        ShiftRate.objects.create(shift_type=full, role="bar", rate=Decimal("3000"))
        for u in (self.vika, self.dima):
            add_member(u, timezone.localdate(), shift_type=full)

    def rows(self, data):
        return {m["user"]: m for m in data["members"]}

    def test_worker_sees_own_money_not_colleagues(self):
        self.client.force_authenticate(self.vika)
        for data in (
            self.client.get("/api/shifts/day/").data,
            self.client.get("/api/shifts/").data[0],
            self.client.get("/api/shifts/month/").data["days"][0],
        ):
            rows = self.rows(data)
            self.assertEqual(rows[self.vika.id]["payout"], "3000.00")
            self.assertIsNone(rows[self.dima.id]["payout"])
            self.assertIsNone(rows[self.dima.id]["kpi"])
            self.assertIsNone(data["payout_total"])
            self.assertIsNone(data["revenue"])
            # имя, роль и время коллеги видны — с кем работаешь, знать нужно
            self.assertEqual(rows[self.dima.id]["starts_at"], "08:00")

    def test_manager_sees_everything(self):
        self.client.force_authenticate(self.manager)
        data = self.client.get("/api/shifts/day/").data
        self.assertEqual(self.rows(data)[self.dima.id]["payout"], "3000.00")
        self.assertEqual(data["payout_total"], "6000.00")

    def test_even_scheme_unchanged(self):
        """При оплате поровну сумма у всех одна — скрывать нечего."""
        Shift.objects.update(scheme="even")
        self.client.force_authenticate(self.vika)
        data = self.client.get("/api/shifts/day/").data
        self.assertIsNotNone(self.rows(data)[self.dima.id]["payout"])


class ProrateTests(APITestCase):
    """Этап 5: ставка по отработанным часам — как считает Монти.

    1 100 за 6 ч и 2 200 за 12 ч — одна цена часа, 183,33. Старший —
    2 500 за 12 ч, то есть +25 ₽ в час. Часы к оплате — до ближайшего часа.
    """

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-h", password="demo12345", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("man-h", password="demo12345", role=User.Role.WAREHOUSE)
        self.bar = User.objects.create_user("bar-h", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.prorate = True
        cfg.senior_bonus = Decimal("25")
        cfg.daily_rate = Decimal("2000")
        cfg.save()
        self.full = ShiftType.objects.create(name="Полная", starts_at=time(8), ends_at=time(20))
        self.short = ShiftType.objects.create(name="Короткая", starts_at=time(14), ends_at=time(20))
        ShiftRate.objects.create(shift_type=self.full, role="bar", rate=Decimal("2200"))
        ShiftRate.objects.create(shift_type=self.short, role="bar", rate=Decimal("1100"))
        self.day = timezone.localdate() - timedelta(days=1)

    def put(self, shift_type, end=None, start=None, senior=False):
        from .services import update_member

        _, m = add_member(self.bar, self.day, shift_type=shift_type, is_senior=senior)
        kw = {}
        if end is not None:
            kw["ends_at"] = end
        if start is not None:
            kw["starts_at"] = start
        if kw:
            update_member(m, **kw)
        return m

    def row(self):
        return shift_report(shift=get_shift(self.day))["members"][0]

    def test_on_plan_pays_the_shift_rate(self):
        self.put(self.full)
        r = self.row()
        self.assertEqual((r["base"], r["paid_hours"], r["planned_hours"], r["hourly"]), ("2200.00", "12", "12.00", "183.33"))

    def test_hour_late_pays_one_more_hour(self):
        from datetime import time

        self.put(self.full, end=time(21))
        self.assertEqual(self.row()["base"], "2383.33")  # не 183,33 × 13 = 2 383,29

    def test_left_an_hour_early(self):
        from datetime import time

        self.put(self.short, end=time(19))
        self.assertEqual(self.row()["base"], "916.67")

    def test_rounds_to_nearest_hour(self):
        from datetime import time

        self.put(self.full, end=time(20, 40))
        self.assertEqual(self.row()["paid_hours"], "13")
        ShiftMember.objects.update(ends_at=time(20, 20))
        self.assertEqual(self.row()["paid_hours"], "12")
        ShiftMember.objects.update(ends_at=time(20, 30))
        self.assertEqual(self.row()["paid_hours"], "13")  # ровно полчаса — вверх

    def test_senior_paid_per_hour(self):
        from datetime import time

        self.put(self.full, senior=True)
        self.assertEqual(self.row()["senior_bonus"], "300.00")  # 2 500 − 2 200
        ShiftMember.objects.update(ends_at=time(21))
        r = self.row()
        self.assertEqual((r["base"], r["senior_bonus"], r["payout"]), ("2383.33", "325.00", "2708.33"))

    def test_senior_on_short_shift_gets_half(self):
        self.put(self.short, senior=True)
        self.assertEqual(self.row()["senior_bonus"], "150.00")  # 1 250 − 1 100

    def test_night_shift(self):
        from datetime import time

        night = ShiftType.objects.create(name="Ночь", starts_at=time(20), ends_at=time(8))
        ShiftRate.objects.create(shift_type=night, role="bar", rate=Decimal("2400"))
        self.put(night, end=time(9))
        self.assertEqual(self.row()["base"], "2600.00")  # 200 × 13

    def test_off_pays_the_whole_shift(self):
        from datetime import time

        self.put(self.full, end=time(21), senior=True)
        Shift.objects.update(prorate=False)
        r = self.row()
        self.assertEqual((r["base"], r["senior_bonus"], r["paid_hours"]), ("2200.00", "25.00", None))

    def test_no_time_marked_pays_the_shift(self):
        """Время стёрли — платить «за 0 часов» нельзя: ставка за смену."""
        m = self.put(self.full)
        ShiftMember.objects.filter(pk=m.pk).update(starts_at=None, ends_at=None)
        self.assertEqual(self.row()["base"], "2200.00")

    def test_switch_does_not_rewrite_the_past(self):
        from datetime import time

        self.put(self.full, end=time(21))
        self.client.force_authenticate(self.owner)
        self.client.patch("/api/shifts/settings/", {"prorate": False}, format="json")
        self.assertEqual(self.row()["base"], "2383.33")  # вчера — по правилам вчера

    def test_payroll_and_ledger(self):
        from datetime import time

        self.put(self.full, end=time(21), senior=True)
        row = payroll(Shift.objects.all())[0]
        self.assertEqual((row["base"], row["paid_hours"], row["total"]), ("2383.33", "13.00", "2708.33"))

    def test_owner_toggles_it(self):
        self.client.force_authenticate(self.owner)
        res = self.client.patch("/api/shifts/settings/", {"prorate": False}, format="json")
        self.assertFalse(res.data["prorate"])
        self.assertFalse(ShiftSettings.load().prorate)

    def test_leave_before_arrival_is_refused(self):
        """11:30 при приходе в 14:00 — не смена через полночь на 21,5 часа."""
        self.put(self.short)
        self.client.force_authenticate(self.manager)
        res = self.client.post(
            "/api/shifts/update_member/",
            {"date": self.day.isoformat(), "user": self.bar.id, "ends_at": "11:30"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.row()["base"], "1100.00")

    def test_type_retime_updates_the_plan(self):
        """Тип «Полная» стал 08–21 — план у людей в сменах тоже 13 ч."""
        from datetime import time

        today = timezone.localdate()
        _, m = add_member(self.bar, today, shift_type=self.full)
        self.client.force_authenticate(self.owner)
        self.client.patch(f"/api/shifts/types/{self.full.id}/", {"ends_at": "21:00"}, format="json")
        m.refresh_from_db()
        self.assertEqual((m.planned_hours, m.ends_at), (Decimal("13.00"), time(21)))
        r = shift_report(shift=get_shift(today))["members"][0]
        self.assertEqual((r["base"], r["hourly"]), ("2200.00", "169.23"))

    def test_senior_without_time_paid_by_plan(self):
        m = self.put(self.full, senior=True)
        ShiftMember.objects.filter(pk=m.pk).update(starts_at=None, ends_at=None)
        r = self.row()
        self.assertEqual((r["base"], r["senior_bonus"], r["time_missing"]), ("2200.00", "300.00", True))

    def test_no_type_no_time_senior_is_zero_and_flagged(self):
        _, m = add_member(self.bar, self.day, is_senior=True)
        ShiftMember.objects.filter(pk=m.pk).update(
            shift_type=None, planned_hours=None, starts_at=None, ends_at=None, rate=None
        )
        r = self.row()
        self.assertEqual((r["base"], r["senior_bonus"], r["time_missing"]), ("2000.00", "0.00", True))

    def test_manual_times_without_type_pay_default_rate(self):
        """Без типа плана нет — ставка по умолчанию целиком, старшему по часам."""
        from datetime import time

        _, m = add_member(self.bar, self.day, is_senior=True)
        ShiftMember.objects.filter(pk=m.pk).update(
            shift_type=None, planned_hours=None, starts_at=time(9), ends_at=time(15), rate=None
        )
        r = self.row()
        self.assertEqual((r["base"], r["senior_bonus"], r["paid_hours"]), ("2000.00", "150.00", "6"))

    def test_admin_refuses_leave_before_arrival(self):
        from datetime import time

        from django.core.exceptions import ValidationError

        m = self.put(self.short)
        m.ends_at = time(11, 30)
        with self.assertRaises(ValidationError):
            m.clean()


class ApplyRulesToPastShiftTests(APITestCase):
    """Правила поменяли сегодня, а вчерашнюю смену надо посчитать по ним.

    Случай Монти 03.10: включили оплату по часам, а Савелий вчера пришёл
    в 14:00 на смену 10–22 — время правили, а сумма не менялась, потому
    что вчерашняя смена хранит правила дня открытия.
    """

    def setUp(self):
        from datetime import time

        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.owner = User.objects.create_user("owner-ar", password="demo12345", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("man-ar", password="demo12345", role=User.Role.WAREHOUSE)
        self.sava = User.objects.create_user("sava-ar", password="demo12345", role=User.Role.BAR)
        self.anna = User.objects.create_user("anna-ar", password="demo12345", role=User.Role.BAR)
        cfg = ShiftSettings.load()
        cfg.scheme = "result"
        cfg.prorate = False
        cfg.senior_bonus = Decimal("300")
        cfg.save()
        self.full = ShiftType.objects.create(name="Полная", starts_at=time(10), ends_at=time(22))
        ShiftRate.objects.create(shift_type=self.full, role="bar", rate=Decimal("2200"))
        self.y = timezone.localdate() - timedelta(days=1)
        from .services import update_member

        _, m = add_member(self.sava, self.y, shift_type=self.full)
        update_member(m, starts_at=time(14))
        add_member(self.anna, self.y, shift_type=self.full, is_senior=True)
        # сегодня владелец включил оплату по часам, старшему — 25 ₽/ч
        cfg.prorate = True
        cfg.senior_bonus = Decimal("25")
        cfg.save()

    def rows(self, data):
        return {m["user"]: m for m in data["members"]}

    def day(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/shifts/day/?date={self.y.isoformat()}").data

    def test_yesterday_keeps_its_rules_until_owner_decides(self):
        data = self.day(self.owner)
        self.assertEqual(self.rows(data)[self.sava.id]["base"], "2200.00")
        self.assertIn("оплата по отработанным часам включена", data["rules_diff"])
        self.assertIn("надбавка старшему: 300 → 25", data["rules_diff"])

    def test_owner_recalculates_yesterday(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        self.assertEqual(res.status_code, 200)
        rows = self.rows(res.data)
        self.assertEqual(rows[self.sava.id]["base"], "1466.67")  # 8 ч × 183,33
        self.assertEqual(rows[self.anna.id]["senior_bonus"], "300.00")  # 25 × 12
        self.assertEqual(res.data["rules_diff"], [])
        # факты не тронуты: пришёл всё так же в 14:00
        self.assertEqual(rows[self.sava.id]["starts_at"], "14:00")

    def test_only_owner(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.day(self.manager)["rules_diff"], [])

    def test_today_never_shows_diff(self):
        add_member(self.sava, timezone.localdate(), shift_type=self.full)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get("/api/shifts/day/").data["rules_diff"], [])

    def test_rate_change_is_listed(self):
        ShiftRate.objects.filter(shift_type=self.full).update(rate=Decimal("2400"))
        self.assertIn("ставки по типам смен и ролям", self.day(self.owner)["rules_diff"])

    def test_deleted_type_keeps_the_rate(self):
        """Тип смены удалили — пересчёт не роняет ставку до ставки по умолчанию."""
        ShiftType.objects.filter(pk=self.full.pk).delete()
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        rows = self.rows(res.data)
        # план 12 ч сохранён снимком, ставка 2 200 — тоже: 8 ч × 183,33
        self.assertEqual(rows[self.sava.id]["base"], "1466.67")

    def test_other_past_shifts_untouched(self):
        """Пересчёт одной смены не трогает остальные прошлые."""
        from datetime import time

        from .services import update_member

        before = self.y - timedelta(days=1)
        _, m = add_member(self.sava, before, shift_type=self.full)
        Shift.objects.filter(date=before).update(prorate=False, senior_bonus=Decimal("300"))
        update_member(m, starts_at=time(14))
        self.client.force_authenticate(self.owner)
        self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        other = shift_report(shift=Shift.objects.get(date=before))["members"][0]
        self.assertEqual(other["base"], "2200.00")
        self.assertFalse(Shift.objects.get(date=before).prorate)

    def test_payroll_follows_the_recalc(self):
        self.client.force_authenticate(self.owner)
        self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        rows = {r["user"]: r for r in payroll(Shift.objects.all())}
        self.assertEqual(rows[self.sava.id]["total"], "1466.67")
        self.assertEqual(rows[self.anna.id]["total"], "2500.00")

    def test_bad_requests(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.post("/api/shifts/apply_rules/", {"date": "вчера"}, format="json").status_code, 400
        )
        empty_day = (self.y - timedelta(days=30)).isoformat()
        self.assertEqual(
            self.client.post("/api/shifts/apply_rules/", {"date": empty_day}, format="json").status_code, 400
        )

    def test_worker_cannot_recalc(self):
        self.client.force_authenticate(self.sava)
        res = self.client.post("/api/shifts/apply_rules/", {"date": self.y.isoformat()}, format="json")
        self.assertEqual(res.status_code, 403)

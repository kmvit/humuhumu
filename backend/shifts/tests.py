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

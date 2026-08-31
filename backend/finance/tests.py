from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order, Table
from shifts.models import ShiftSettings
from users.models import User

from .models import Expense, ExpenseCategory, PayrollPayout


class PayrollStatementTests(APITestCase):
    """Ведомость: начисления берутся из смен, выплаты копятся отдельно."""

    def setUp(self):
        self.cook = User.objects.create_user(
            username="cook", password="demo12345", role=User.Role.COOK, first_name="Повар"
        )
        self.waiter = User.objects.create_user(
            username="waiter", password="demo12345", role=User.Role.WAITER
        )
        self.manager = User.objects.create_user(
            username="manager", password="demo12345", role=User.Role.WAREHOUSE
        )
        penalty, _ = Table.objects.get_or_create(name="Штраф")
        cfg = ShiftSettings.load()
        cfg.daily_rate = Decimal("2000")
        cfg.bonus_percent = Decimal("10")
        cfg.penalty_table = penalty
        cfg.save()
        self.today = timezone.localdate()
        self.month = self.today.strftime("%Y-%m")

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def sale(self, total, table="1"):
        Order.objects.create(
            table=table, status=Order.Status.PAID,
            closed_at=timezone.now(), total=Decimal(total),
        )

    def put_in_shift(self, *users):
        self.auth(self.manager)
        for u in users:
            self.client.post("/api/shifts/add_member/", {"user": u.id}, format="json")

    def statement(self):
        return self.client.get(f"/api/finance/payroll/?month={self.month}").data

    # ——— начисления ———

    def test_accrued_matches_shift_formula(self):
        """Двое в смене, выручка 10 000, бонус 10% → по 2 000 + 500 каждому."""
        self.sale("10000")
        self.put_in_shift(self.cook, self.waiter)
        data = self.statement()

        self.assertEqual(len(data["rows"]), 2)
        for row in data["rows"]:
            self.assertEqual(row["accrued"], "2500.00")
            self.assertEqual(row["paid"], "0.00")
            self.assertEqual(row["left"], "2500.00")
            self.assertFalse(row["settled"])
        # ФОТ за месяц — то, чего не видно в «Сменах»
        self.assertEqual(data["totals"]["accrued"], "5000.00")
        self.assertEqual(data["totals"]["left"], "5000.00")
        self.assertEqual(data["totals"]["people"], 2)

    def test_empty_month_is_zero_not_error(self):
        self.auth(self.manager)
        data = self.statement()
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["totals"]["accrued"], "0.00")

    # ——— выплаты ———

    def test_partial_payment_leaves_remainder(self):
        """Аванс и окончательный расчёт — две записи, остаток считается."""
        self.sale("10000")
        self.put_in_shift(self.cook)
        self.client.post(
            "/api/finance/payroll/pay/",
            {"user": self.cook.id, "amount": "1000", "month": self.month},
            format="json",
        )
        row = self.statement()["rows"][0]
        self.assertEqual(row["accrued"], "3000.00")
        self.assertEqual(row["paid"], "1000.00")
        self.assertEqual(row["left"], "2000.00")
        self.assertFalse(row["settled"])

        self.client.post(
            "/api/finance/payroll/pay/",
            {"user": self.cook.id, "amount": "2000", "month": self.month},
            format="json",
        )
        row = self.statement()["rows"][0]
        self.assertEqual(row["paid"], "3000.00")
        self.assertEqual(row["left"], "0.00")
        self.assertTrue(row["settled"])
        self.assertEqual(PayrollPayout.objects.count(), 2)

    def test_unpay_rolls_back_last_payment(self):
        self.sale("10000")
        self.put_in_shift(self.cook)
        for amount in ("1000", "500"):
            self.client.post(
                "/api/finance/payroll/pay/",
                {"user": self.cook.id, "amount": amount, "month": self.month},
                format="json",
            )
        self.client.post(
            "/api/finance/payroll/unpay/",
            {"user": self.cook.id, "month": self.month},
            format="json",
        )
        self.assertEqual(PayrollPayout.objects.count(), 1)
        self.assertEqual(self.statement()["rows"][0]["paid"], "1000.00")

    def test_zero_amount_rejected(self):
        self.auth(self.manager)
        res = self.client.post(
            "/api/finance/payroll/pay/",
            {"user": self.cook.id, "amount": "0", "month": self.month},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(PayrollPayout.objects.count(), 0)

    # ——— права ———

    def test_worker_has_no_access_to_section(self):
        """«Финансы» — управленческий раздел: работника не пускаем вовсе."""
        self.sale("10000")
        self.put_in_shift(self.cook, self.waiter)
        self.auth(self.cook)
        self.assertEqual(
            self.client.get(f"/api/finance/payroll/?month={self.month}").status_code, 403
        )

    def test_worker_cannot_mark_payment(self):
        self.put_in_shift(self.cook)
        self.auth(self.cook)
        res = self.client.post(
            "/api/finance/payroll/pay/",
            {"user": self.cook.id, "amount": "100", "month": self.month},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_worker_cannot_read_days(self):
        self.put_in_shift(self.cook, self.waiter)
        self.auth(self.cook)
        res = self.client.get(
            f"/api/finance/payroll/days/?user={self.waiter.id}&month={self.month}"
        )
        self.assertEqual(res.status_code, 403)

    # ——— расшифровка ———

    def test_days_breakdown_explains_the_sum(self):
        self.sale("10000")
        self.put_in_shift(self.cook)
        res = self.client.get(
            f"/api/finance/payroll/days/?user={self.cook.id}&month={self.month}"
        )
        days = res.data["days"]
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0]["daily_rate"], "2000.00")
        self.assertEqual(days[0]["bonus_share"], "1000.00")
        self.assertEqual(days[0]["payout"], "3000.00")


class ExpenseTests(APITestCase):
    """Прочие расходы: аренда и всё, что не зарплата и не закуп."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username="manager", password="demo12345", role=User.Role.WAREHOUSE
        )
        self.cook = User.objects.create_user(
            username="cook", password="demo12345", role=User.Role.COOK
        )
        self.today = timezone.localdate()
        self.month = self.today.strftime("%Y-%m")
        # «Аренда» уже засеяна миграцией стартовых статей — берём её
        self.rent, _ = ExpenseCategory.objects.get_or_create(
            name="Аренда", defaults={"sort_order": 10}
        )
        self.utils, _ = ExpenseCategory.objects.get_or_create(
            name="Коммуналка", defaults={"sort_order": 20}
        )

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def add(self, category, amount, comment=""):
        return self.client.post(
            "/api/finance/expenses/",
            {
                "date": self.today.isoformat(),
                "category": category.id,
                "amount": amount,
                "comment": comment,
            },
            format="json",
        )

    def test_month_total_and_breakdown_by_category(self):
        self.auth(self.manager)
        self.add(self.rent, "80000", "август")
        self.add(self.utils, "12000")
        self.add(self.utils, "3000")

        data = self.client.get(f"/api/finance/expenses/?month={self.month}").data
        self.assertEqual(data["total"], "95000.00")
        self.assertEqual(len(data["rows"]), 3)
        by_cat = {c["name"]: c["total"] for c in data["by_category"]}
        self.assertEqual(by_cat["Аренда"], "80000.00")
        self.assertEqual(by_cat["Коммуналка"], "15000.00")

    def test_other_month_is_not_counted(self):
        self.auth(self.manager)
        self.add(self.rent, "80000")
        other = (self.today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        data = self.client.get(f"/api/finance/expenses/?month={other}").data
        self.assertEqual(data["total"], "0.00")
        self.assertEqual(data["rows"], [])

    def test_zero_amount_rejected(self):
        self.auth(self.manager)
        res = self.add(self.rent, "0")
        self.assertEqual(res.status_code, 400)

    def test_expense_can_be_deleted(self):
        self.auth(self.manager)
        created = self.add(self.rent, "5000").data
        res = self.client.delete(f"/api/finance/expenses/{created['id']}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(Expense.objects.count(), 0)

    def test_worker_has_no_access(self):
        self.auth(self.cook)
        self.assertEqual(
            self.client.get(f"/api/finance/expenses/?month={self.month}").status_code, 403
        )
        self.assertEqual(self.add(self.rent, "1000").status_code, 403)


class ProfitReportTests(APITestCase):
    """Отчёт о прибыли: выручка − себестоимость − ФОТ − прочие расходы."""

    def setUp(self):
        from catalog.models import Category, Product
        from inventory.models import (
            Receipt,
            ReceiptItem,
            RecipeItem,
            StockCategory,
            StockItem,
        )

        self.manager = User.objects.create_user(
            username="manager", password="demo12345", role=User.Role.WAREHOUSE
        )
        self.today = timezone.localdate()
        self.month = self.today.strftime("%Y-%m")

        cat = Category.objects.create(name="Кухня", station="kitchen")
        self.burger = Product.objects.create(
            category=cat, name="Бургер", price=Decimal("500")
        )
        self.salad = Product.objects.create(
            category=cat, name="Салат", price=Decimal("300")
        )

        # у бургера тех. карта и известная цена закупа, у салата — ничего
        stock_cat = StockCategory.objects.create(name="Продукты")
        self.meat = StockItem.objects.create(
            name="Мясо", unit="g", category=stock_cat
        )
        RecipeItem.objects.create(
            product=self.burger, item=self.meat, quantity=Decimal("100")
        )
        receipt = Receipt.objects.create()
        ReceiptItem.objects.create(
            receipt=receipt, item=self.meat,
            quantity=Decimal("1000"), unit_cost=Decimal("1.50"),
        )

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def sell(self, product, qty, method="cash"):
        from orders.models import Order, OrderItem

        order = Order.objects.create(
            table="1", status=Order.Status.PAID,
            closed_at=timezone.now(), pay_method=method,
        )
        OrderItem.objects.create(
            order=order, product=product, quantity=qty, unit_price=product.price
        )
        order.total = product.price * qty
        order.save(update_fields=["total"])
        return order

    def report(self):
        return self.client.get(f"/api/finance/payroll/report/?month={self.month}").data

    def test_revenue_checks_and_payment_split(self):
        self.auth(self.manager)
        self.sell(self.burger, 2, "cash")   # 1000
        self.sell(self.salad, 1, "card")    # 300
        r = self.report()
        self.assertEqual(r["revenue"], "1300.00")
        self.assertEqual(r["checks"], 2)
        self.assertEqual(r["avg_check"], "650.00")
        self.assertEqual(r["cash"], "1000.00")
        self.assertEqual(r["card"], "300.00")

    def test_cogs_counts_only_dishes_with_full_recipe(self):
        """Салат без тех. карты в себестоимость не попадает — и это видно."""
        self.auth(self.manager)
        self.sell(self.burger, 2)   # 100 г × 1.50 = 150 ₽ за порцию → 300
        self.sell(self.salad, 1)
        r = self.report()
        self.assertEqual(r["cogs"], "300.00")
        self.assertEqual(r["gross"], "1000.00")
        # себестоимость известна только по выручке бургера: 1000 из 1300
        self.assertAlmostEqual(r["cost_coverage"], 76.9, places=1)
        self.assertTrue(r["is_estimate"])

    def test_full_coverage_marks_report_as_fact(self):
        self.auth(self.manager)
        self.sell(self.burger, 1)
        r = self.report()
        self.assertEqual(r["cost_coverage"], 100.0)
        self.assertFalse(r["is_estimate"])

    def test_profit_subtracts_payroll_and_expenses(self):
        from shifts.models import Shift, ShiftMember

        self.auth(self.manager)
        self.sell(self.burger, 10)  # выручка 5000, себестоимость 1500

        cfg = ShiftSettings.load()
        cfg.daily_rate = Decimal("2000")
        cfg.bonus_percent = Decimal("0")
        cfg.save()
        shift = Shift.objects.create(
            date=self.today, daily_rate=Decimal("2000"), bonus_percent=Decimal("0")
        )
        ShiftMember.objects.create(shift=shift, user=self.manager, role="warehouse")

        cat, _ = ExpenseCategory.objects.get_or_create(name="Аренда")
        Expense.objects.create(date=self.today, category=cat, amount=Decimal("500"))

        r = self.report()
        self.assertEqual(r["revenue"], "5000.00")
        self.assertEqual(r["cogs"], "1500.00")
        self.assertEqual(r["gross"], "3500.00")
        self.assertEqual(r["payroll"], "2000.00")
        self.assertEqual(r["expenses"], "500.00")
        self.assertEqual(r["profit"], "1000.00")

    def test_purchases_are_informational_not_subtracted(self):
        """Закуп показывается справочно и не участвует в прибыли."""
        self.auth(self.manager)
        self.sell(self.burger, 1)
        r = self.report()
        self.assertEqual(r["purchases"], "1500.00")   # 1000 г × 1.50
        # прибыль = 500 − 150 − 0 − 0, закуп не вычитается
        self.assertEqual(r["profit"], "350.00")

    def test_empty_month_does_not_divide_by_zero(self):
        self.auth(self.manager)
        r = self.report()
        self.assertEqual(r["revenue"], "0.00")
        self.assertEqual(r["avg_check"], "0.00")
        self.assertEqual(r["margin"], 0.0)
        self.assertEqual(r["profit"], "0.00")

    def test_worker_has_no_access_to_report(self):
        cook = User.objects.create_user(
            username="cook2", password="demo12345", role=User.Role.COOK
        )
        self.auth(cook)
        res = self.client.get(f"/api/finance/payroll/report/?month={self.month}")
        self.assertEqual(res.status_code, 403)

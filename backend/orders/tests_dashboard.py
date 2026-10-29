from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import Category, Product, ProductVariant
from core.models import SiteSettings
from inventory.models import StockCategory, StockItem
from payments.models import Payment
from shifts.models import ShiftSettings
from users.models import User

from .dashboard import summary, windows
from .models import Order, OrderItem, Table


class DashboardTests(APITestCase):
    """«Сводка»: деньги за период по факту оплаты, сравнение и внимание."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.admin = User.objects.create_user(
            username="owner", password="demo12345", role=User.Role.ADMIN
        )
        cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = ProductVariant.objects.create(
            product=Product.objects.create(category=cat, name="Латте"),
            label="0,3", price=Decimal("240"),
        )
        self.tea = ProductVariant.objects.create(
            product=Product.objects.create(category=cat, name="Чай"),
            price=Decimal("150"),
        )
        # Среда, 14:00 — середина дня и недели, чтобы окна были не пустые
        self.now = timezone.make_aware(datetime(2026, 10, 28, 14, 0))

    def sale(self, total, at, method="cash", bonus="0", table="1", item=None, qty=1,
             status=Order.Status.PAID):
        order = Order.objects.create(
            table=table, status=status, pay_method=method, total=Decimal(total),
            bonus_spent=Decimal(bonus), paid_at=at, closed_at=at,
        )
        if item:
            OrderItem.objects.create(
                order=order, variant=item, quantity=qty, unit_price=item.price
            )
        return order

    def test_windows_compare_same_time_week_ago(self):
        start, end, prev_start, prev_end = windows("today", self.now)
        self.assertEqual(timezone.localtime(start).hour, 0)
        self.assertEqual(end - start, prev_end - prev_start)
        self.assertEqual(start - prev_start, timedelta(days=7))

    def test_month_window_does_not_overlap_current(self):
        # 31 марта против февраля: прошлое окно обрезается началом марта
        now = timezone.make_aware(datetime(2026, 3, 31, 20, 0))
        start, _, prev_start, prev_end = windows("month", now)
        self.assertEqual(timezone.localtime(prev_start).date().isoformat(), "2026-02-01")
        self.assertEqual(prev_end, start)

    def test_revenue_is_money_received_minus_refunds(self):
        today = self.now - timedelta(hours=2)
        self.sale("500", today, method="cash")
        self.sale("300", today, method="card", bonus="100")
        # возврат сегодня вчерашней продажи: вычитается сегодня
        old = self.sale("200", self.now - timedelta(days=1), method="card")
        old.status = Order.Status.REFUNDED
        old.refunded_at = today
        old.save()
        # отменённый и штрафной стол — не выручка
        self.sale("999", today, status=Order.Status.CANCELLED)
        penalty, _ = Table.objects.get_or_create(name="Штраф")
        cfg = ShiftSettings.load()
        cfg.penalty_table = penalty
        cfg.save()
        self.sale("777", today, table="Штраф")

        cur = summary("today", self.now)["current"]
        self.assertEqual(cur["cash"], Decimal("500.00"))
        self.assertEqual(cur["card"], Decimal("200.00"))
        self.assertEqual(cur["sales"], Decimal("700.00"))
        self.assertEqual(cur["refunds"], Decimal("200.00"))
        self.assertEqual(cur["revenue"], Decimal("500.00"))
        self.assertEqual(cur["checks"], 2)
        self.assertEqual(cur["avg_check"], Decimal("350.00"))
        self.assertEqual(cur["bonuses"], Decimal("100.00"))

    def test_prepaid_order_in_work_counts(self):
        """Стойка с предоплатой: заказ ещё готовится, но деньги уже получены."""
        self.sale("240", self.now - timedelta(minutes=5), status=Order.Status.OPEN)
        data = summary("today", self.now)
        self.assertEqual(data["current"]["revenue"], Decimal("240.00"))
        self.assertEqual(data["in_work"], 1)

    def test_online_payment_is_its_own_channel(self):
        order = self.sale("400", self.now - timedelta(hours=1), method="card")
        Payment.objects.create(
            order=order, purpose=Payment.Purpose.ORDER, status=Payment.Status.SUCCEEDED,
            provider="tbank", amount=Decimal("400"),
        )
        cur = summary("today", self.now)["current"]
        self.assertEqual(cur["online"], Decimal("400.00"))
        self.assertEqual(cur["card"], Decimal("0.00"))

    def test_previous_window_is_cut_at_same_time(self):
        week_ago = self.now - timedelta(days=7)
        self.sale("100", week_ago - timedelta(hours=1))  # до 14:00 — в сравнении
        self.sale("900", week_ago + timedelta(hours=3))  # после — нет
        data = summary("today", self.now)
        self.assertEqual(data["previous"]["revenue"], Decimal("100.00"))
        # а на графике прошлый день целиком — видно, куда он шёл дальше
        prev_total = sum(Decimal(p["prev"]) for p in data["series"])
        self.assertEqual(prev_total, Decimal("1000.00"))
        self.assertEqual(len(data["series"]), 24)
        self.assertIsNone(data["series"][20]["value"])  # будущий час не рисуем

    def test_top_items_by_revenue(self):
        at = self.now - timedelta(hours=1)
        self.sale("480", at, item=self.latte, qty=2)
        self.sale("450", at, item=self.tea, qty=3)
        self.sale("240", at, item=self.latte, qty=1)
        top = summary("week", self.now)["top"]
        self.assertEqual([t["name"] for t in top], ["Латте", "Чай"])
        self.assertEqual(top[0]["qty"], 3)
        self.assertEqual(top[0]["variant"], "0,3")
        self.assertEqual(top[0]["sum"], Decimal("720.00"))

    def test_attention_lists_what_needs_owner(self):
        site = SiteSettings.load()
        site.ordering_paused = True
        site.save()
        stuck = Order.objects.create(table="2", status=Order.Status.REQUESTED)
        Order.objects.filter(pk=stuck.pk).update(created_at=self.now - timedelta(minutes=30))
        cat = StockCategory.objects.create(name="Молоко")
        StockItem.objects.create(
            category=cat, name="Молоко 3,2%", unit=StockItem.Unit.PIECE,
            quantity=Decimal("1"), min_quantity=Decimal("5"),
        )
        keys = {a["key"] for a in summary("today", self.now)["attention"]}
        self.assertEqual(keys, {"paused", "waiting", "stock"})

    def test_nothing_to_report_is_empty(self):
        self.assertEqual(summary("today", self.now)["attention"], [])

    def test_admin_only(self):
        waiter = User.objects.create_user(
            username="w", password="demo12345", role=User.Role.WAITER
        )
        for user, code in ((waiter, 403), (self.admin, 200)):
            res = self.client.post(
                "/api/auth/token/",
                {"username": user.username, "password": "demo12345"}, format="json",
            )
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
            self.assertEqual(self.client.get("/api/dashboard/?period=month").status_code, code)

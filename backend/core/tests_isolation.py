"""Сплошная проверка изоляции заведений по всему API.

Отдельные тесты проверяют «работает ли фича». Этот — обратное: что
фича НЕ работает через границу заведения. Он заводит два кафе, набивает
второе данными и от имени первого пытается их достать всеми способами,
какими предоставляет API: списком, по прямому id, правкой и ссылкой из
чужого объекта.

Так и ловятся дыры вроде той, что нашлась в панели владельца: список
сотрудников собирался по всей базе, потому что у пользователей менеджер
намеренно не фильтрует. Глазами такое видно только если открыть нужную
страницу; тест открывает все.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, Product
from core.models import Organization, SiteSettings
from core.tenancy import organization_context
from finance.models import Expense, ExpenseCategory
from inventory.models import Receipt, StockCategory, StockItem
from loyalty.models import LoyaltyMember
from orders.models import Order, OrderItem, Table
from shifts.models import Shift
from users.models import User


def build_cafe(org, marker: str):
    """Набить заведение данными всех видов, какие показывает панель."""
    with organization_context(org):
        site = SiteSettings.load()
        site.name = f"Кафе {marker}"
        site.plan = SiteSettings.Plan.MAX
        site.save()

        category = Category.objects.create(name=f"Категория {marker}")
        product = Product.objects.create(
            name=f"Блюдо {marker}", price=100, category=category
        )
        table = Table.objects.create(name=f"Стол {marker}")
        order = Order.objects.create(total=100, table=table.name)
        OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=100)

        stock_category = StockCategory.objects.create(name=f"Склад {marker}")
        stock_item = StockItem.objects.create(
            name=f"Позиция {marker}", category=stock_category, unit="g"
        )
        receipt = Receipt.objects.create(supplier=f"Поставщик {marker}")

        expense_category = ExpenseCategory.objects.create(name=f"Расход {marker}")
        expense = Expense.objects.create(
            date=timezone.localdate(), category=expense_category, amount=1000
        )

        staff = User.objects.create_user(
            f"повар-{marker}", role=User.Role.COOK, organization=org
        )
        guest = User.objects.create_user(
            f"гость-{marker}", role=User.Role.CLIENT, organization=org,
            phone=f"+7999000{marker[-4:]}",
        )
        member = LoyaltyMember.objects.create(user=guest, balance=500)
        shift = Shift.objects.create(date=timezone.localdate() - timedelta(days=1))

    return {
        "category": category, "product": product, "table": table, "order": order,
        "stock_category": stock_category, "stock_item": stock_item,
        "receipt": receipt, "expense_category": expense_category,
        "expense": expense, "staff": staff, "guest": guest,
        "member": member, "shift": shift,
    }


#: (адрес списка, ключ объекта, как называется в выдаче) — покрывает все
#: разделы панели владельца.
RESOURCES = [
    ("/api/categories/", "category", "name"),
    ("/api/products/", "product", "name"),
    ("/api/tables/", "table", "name"),
    ("/api/orders/", "order", None),
    ("/api/staff/", "staff", "username"),
    ("/api/inventory/categories/", "stock_category", "name"),
    ("/api/inventory/items/", "stock_item", "name"),
    ("/api/inventory/receipts/", "receipt", None),
    ("/api/finance/expenses/", "expense", None),
    ("/api/finance/expense-categories/", "expense_category", "name"),
]


@override_settings(ALLOWED_HOSTS=["*"])
class CrossTenantApiTests(TestCase):
    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(
            name="Бета", slug="beta", domain=self.HOST_B
        )
        self.data_a = build_cafe(self.a, "альфы")
        self.data_b = build_cafe(self.b, "беты")

        with organization_context(self.a):
            self.owner = User.objects.create_user(
                "owner", password="Sh4-alpha-owner", role=User.Role.ADMIN,
                organization=self.a,
            )
        self.client = APIClient()
        token = self.client.post(
            "/api/auth/token/",
            {"username": "owner", "password": "Sh4-alpha-owner"},
            format="json",
            HTTP_HOST=self.HOST_A,
        ).json()["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_HOST": self.HOST_A}

    # ── чтение ───────────────────────────────────────────────────────────
    def test_lists_never_contain_foreign_objects(self):
        for url, key, label in RESOURCES:
            with self.subTest(url=url):
                res = self.client.get(url, **self.auth)
                self.assertEqual(res.status_code, 200, f"{url}: {res.data}")
                body = res.json()
                # Разделы отдают по-разному: список, страницу или сводку
                # с ключом «rows» (расходы считают ещё и итог за месяц).
                if isinstance(body, list):
                    rows = body
                else:
                    rows = body.get("results") or body.get("rows") or body.get("items") or []
                ids = {row.get("id") for row in rows}
                self.assertNotIn(
                    self.data_b[key].pk, ids,
                    f"{url} показал объект соседнего заведения",
                )
                # своё при этом на месте — иначе тест «проходит» на пустоте
                self.assertIn(self.data_a[key].pk, ids, f"{url} не показал своё")

    def test_foreign_object_is_not_reachable_by_id(self):
        for url, key, _ in RESOURCES:
            with self.subTest(url=url):
                foreign = self.data_b[key].pk
                res = self.client.get(f"{url}{foreign}/", **self.auth)
                self.assertIn(
                    res.status_code, (403, 404, 405),
                    f"{url}{foreign}/ отдал чужой объект ({res.status_code})",
                )

    # ── запись ───────────────────────────────────────────────────────────
    def test_foreign_object_cannot_be_edited(self):
        for url, key in (
            ("/api/products/", "product"),
            ("/api/staff/", "staff"),
            ("/api/inventory/items/", "stock_item"),
            ("/api/finance/expense-categories/", "expense_category"),
        ):
            with self.subTest(url=url):
                res = self.client.patch(
                    f"{url}{self.data_b[key].pk}/", {}, format="json", **self.auth
                )
                self.assertIn(res.status_code, (403, 404, 405), url)

    def test_cannot_attach_foreign_category_to_own_product(self):
        """Ссылку на чужой объект подсунуть нельзя.

        Это тоньше, чем чтение: список фильтруется, а вот поле «категория»
        принимает id — и без фильтра приняло бы чужой, связав два кафе.
        """
        res = self.client.post(
            "/api/products/",
            {
                "name": "Подсадка",
                "price": "100.00",
                "category": self.data_b["category"].pk,
            },
            format="json",
            **self.auth,
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("category", res.data)

    def test_cannot_attach_foreign_stock_item_to_own_receipt(self):
        res = self.client.post(
            "/api/inventory/receipts/",
            {
                "supplier": "Подсадка",
                "items": [{"item": self.data_b["stock_item"].pk, "quantity": "1"}],
            },
            format="json",
            **self.auth,
        )
        self.assertNotEqual(res.status_code, 201, "чужая позиция склада прошла в приход")

    def test_cannot_put_foreign_employee_into_own_shift(self):
        res = self.client.post(
            "/api/shifts/add_member/",
            {"user": self.data_b["staff"].pk, "date": timezone.localdate().isoformat()},
            format="json",
            **self.auth,
        )
        self.assertEqual(res.status_code, 404, "чужой сотрудник попал в смену")

    # ── гости и бонусы ───────────────────────────────────────────────────
    def test_guest_of_another_cafe_is_not_found_by_phone(self):
        """Бонусы ищут гостя по телефону — и не должны находить чужого."""
        res = self.client.get(
            "/api/loyalty/lookup/",
            {"phone": self.data_b["guest"].phone},
            **self.auth,
        )
        if res.status_code == 200:
            self.assertNotEqual(
                res.json().get("balance"), 500,
                "нашёлся гость соседнего заведения",
            )
        else:
            self.assertIn(res.status_code, (400, 404))

    # ── настройки ────────────────────────────────────────────────────────
    def test_site_settings_are_per_domain(self):
        own = self.client.get("/api/site/", HTTP_HOST=self.HOST_A).json()
        foreign = self.client.get("/api/site/", HTTP_HOST=self.HOST_B).json()
        self.assertEqual(own["name"], "Кафе альфы")
        self.assertEqual(foreign["name"], "Кафе беты")

    def test_owner_cannot_reconfigure_another_cafe(self):
        """Токен альфы на домене беты не должен ничего менять."""
        res = self.client.patch(
            "/api/site/",
            {"theme": "island"},
            format="json",
            HTTP_HOST=self.HOST_B,
            HTTP_AUTHORIZATION=self.auth["HTTP_AUTHORIZATION"],
        )
        self.assertIn(res.status_code, (401, 403))
        with organization_context(self.b):
            self.assertNotEqual(SiteSettings.load().theme, "island")


@override_settings(ALLOWED_HOSTS=["*"])
class CrossTenantReportsTests(TestCase):
    """Отчёты и сводки: там суммируют, а не показывают строки.

    Утечка здесь тише всего: чужие заказы не видно списком, но они могут
    попасть в выручку дня или в ведомость — и владелец увидит неверные
    деньги, не поняв почему.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)

        for org, marker, total in ((self.a, "альфы", 100), (self.b, "беты", 999999)):
            with organization_context(org):
                site = SiteSettings.load()
                site.plan = SiteSettings.Plan.MAX
                site.save()
                category = Category.objects.create(name=f"Кат {marker}")
                product = Product.objects.create(
                    name=f"Блюдо {marker}", price=total, category=category
                )
                order = Order.objects.create(
                    total=total, status=Order.Status.PAID,
                    closed_at=timezone.now(), pay_method="cash",
                )
                OrderItem.objects.create(
                    order=order, product=product, quantity=1, unit_price=total
                )
                worker = User.objects.create_user(
                    f"работник-{marker}", role=User.Role.WAITER, organization=org
                )
                shift = Shift.objects.create(date=timezone.localdate())
                shift.members.create(user=worker, organization=org)

        with organization_context(self.a):
            User.objects.create_user(
                "owner", password="Sh4-alpha-owner", role=User.Role.ADMIN,
                organization=self.a,
            )
        self.client = APIClient()
        token = self.client.post(
            "/api/auth/token/",
            {"username": "owner", "password": "Sh4-alpha-owner"},
            format="json",
            HTTP_HOST=self.HOST_A,
        ).json()["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_HOST": self.HOST_A}

    def test_shift_revenue_counts_only_own_orders(self):
        res = self.client.get(
            "/api/shifts/day/", {"date": timezone.localdate().isoformat()}, **self.auth
        )
        self.assertEqual(res.status_code, 200, res.data)
        body = res.json()
        revenue = str(body.get("revenue", ""))
        self.assertNotIn("999999", revenue, "в выручку смены попал чужой заказ")

    def test_shift_members_are_own_only(self):
        res = self.client.get(
            "/api/shifts/day/", {"date": timezone.localdate().isoformat()}, **self.auth
        )
        names = str(res.json().get("members", []))
        self.assertIn("альфы", names)
        self.assertNotIn("беты", names, "в смене показан работник соседнего кафе")

    def test_staff_picker_for_shift_is_own_only(self):
        res = self.client.get("/api/shifts/staff/", **self.auth)
        usernames = {row["name"] for row in res.json()}
        self.assertFalse(
            any("беты" in n for n in usernames),
            "в выбор состава смены попали чужие сотрудники",
        )

    def test_payroll_covers_own_staff_only(self):
        res = self.client.get("/api/shifts/payroll/", **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn("беты", str(res.json()), "в ведомость попал чужой работник")


@override_settings(ALLOWED_HOSTS=["*"])
class GuestSideIsolationTests(TestCase):
    """Гостевая часть — самая доступная: заказ создаётся без входа.

    Значит и проверять её надо строже: гость с одного домена не должен
    ни попасть в чужое меню, ни заказать чужое блюдо, ни отследить чужой
    заказ по токену из ссылки.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)
        for org, marker in ((self.a, "альфы"), (self.b, "беты")):
            with organization_context(org):
                site = SiteSettings.load()
                site.name = f"Кафе {marker}"
                site.save()
                cat = Category.objects.create(name=f"Кат {marker}")
                Product.objects.create(name=f"Блюдо {marker}", price=100, category=cat)
        import uuid

        with organization_context(self.b):
            self.foreign_product = Product.objects.get(name="Блюдо беты")
            # Токен как у настоящего заказа по QR — его гость получает в ссылке.
            self.foreign_order = Order.objects.create(
                total=100, public_token=uuid.uuid4()
            )
        self.client = APIClient()

    def test_menu_is_per_domain_without_login(self):
        names = [
            p["name"]
            for p in self.client.get("/api/products/", HTTP_HOST=self.HOST_A).json()
        ]
        self.assertEqual(names, ["Блюдо альфы"])

    def test_guest_cannot_order_foreign_dish(self):
        res = self.client.post(
            "/api/orders/place/",
            {
                "name": "Гость",
                "items": [{"product": self.foreign_product.pk, "quantity": 1}],
            },
            format="json",
            HTTP_HOST=self.HOST_A,
        )
        self.assertNotEqual(
            res.status_code, 201, "гость заказал блюдо соседнего заведения"
        )

    def test_guest_cannot_track_foreign_order(self):
        """Токен отслеживания — случайный, но и по нему чужой заказ
        не должен открываться с чужого домена."""
        token = self.foreign_order.public_token
        res = self.client.get(
            "/api/orders/track/", {"token": str(token)}, HTTP_HOST=self.HOST_A
        )
        self.assertIn(res.status_code, (400, 403, 404), "открылся чужой заказ")

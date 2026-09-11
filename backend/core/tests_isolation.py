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


@override_settings(ALLOWED_HOSTS=["*"])
class BoardsIsolationTests(TestCase):
    """Боевые доски: официант, кухня, бар, стойка.

    Здесь ошибка стоит дороже всего: повар видит доску весь день и
    действует по ней не думая. Чужой заказ на экране — это не только
    утечка, это ещё и еда, приготовленная не тому.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"

    def _cafe(self, org, marker):
        """Кафе с кухней, баром и живым заказом на «Столе 5»."""
        with organization_context(org):
            site = SiteSettings.load()
            site.plan = SiteSettings.Plan.MAX
            site.save()
            kitchen = Category.objects.create(name=f"Кухня {marker}", station="kitchen")
            bar = Category.objects.create(name=f"Бар {marker}", station="bar")
            food = Product.objects.create(name=f"Суп {marker}", price=300, category=kitchen)
            drink = Product.objects.create(name=f"Чай {marker}", price=100, category=bar)
            Table.objects.create(name="Стол 5")
            order = Order.objects.create(total=400, table="Стол 5")
            OrderItem.objects.create(order=order, product=food, quantity=1, unit_price=300)
            OrderItem.objects.create(order=order, product=drink, quantity=1, unit_price=100)
            staff = {
                role: User.objects.create_user(
                    f"{role}-{marker}", password="Sh4-board-pass",
                    role=role, organization=org,
                )
                for role in ("waiter", "cook", "bar")
            }
        return {"order": order, "staff": staff, "food": food, "drink": drink}

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)
        self.data_a = self._cafe(self.a, "альфы")
        self.data_b = self._cafe(self.b, "беты")
        self.client = APIClient()

    def _as(self, role):
        token = self.client.post(
            "/api/auth/token/",
            {"username": f"{role}-альфы", "password": "Sh4-board-pass"},
            format="json",
            HTTP_HOST=self.HOST_A,
        ).json()["access"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_HOST": self.HOST_A}

    def _ids(self, res):
        body = res.json()
        rows = body if isinstance(body, list) else body.get("results", [])
        return {row["id"] for row in rows}

    # ── что видно на досках ──────────────────────────────────────────────
    def test_boards_show_only_own_orders(self):
        cases = [
            ("официант", "waiter", {}),
            ("кухня", "cook", {"station": "kitchen"}),
            ("бар", "bar", {"station": "bar"}),
            ("к подаче", "waiter", {"serve": "1"}),
        ]
        for title, role, params in cases:
            with self.subTest(доска=title):
                res = self.client.get("/api/orders/", params, **self._as(role))
                self.assertEqual(res.status_code, 200, res.data)
                ids = self._ids(res)
                self.assertNotIn(
                    self.data_b["order"].pk, ids,
                    f"на доске «{title}» показался заказ соседнего кафе",
                )

    def test_own_order_is_on_the_board(self):
        """Обратная проверка: доска не пустая, иначе тест ничего не значит."""
        res = self.client.get("/api/orders/", {"station": "kitchen"}, **self._as("cook"))
        self.assertIn(self.data_a["order"].pk, self._ids(res))

    # ── действия по чужому заказу ────────────────────────────────────────
    def test_station_actions_on_foreign_order_are_refused(self):
        foreign = self.data_b["order"].pk
        cases = [
            ("cook", f"/api/orders/{foreign}/food_status/", "patch", {"status": "ready"}),
            ("bar", f"/api/orders/{foreign}/drinks_status/", "patch", {"status": "ready"}),
            ("waiter", f"/api/orders/{foreign}/serve/", "patch", {"station": "kitchen"}),
            ("waiter", f"/api/orders/{foreign}/close/", "post", {"pay_method": "cash"}),
            ("waiter", f"/api/orders/{foreign}/cancel/", "patch", {}),
            ("waiter", f"/api/orders/{foreign}/work_status/", "patch", {"status": "ready"}),
        ]
        for role, url, method, payload in cases:
            with self.subTest(url=url):
                res = getattr(self.client, method)(
                    url, payload, format="json", **self._as(role)
                )
                self.assertIn(
                    res.status_code, (403, 404),
                    f"{url} принят ({res.status_code}) — чужой заказ изменён",
                )
        self.data_b["order"].refresh_from_db()
        self.assertEqual(
            self.data_b["order"].status, Order.Status.OPEN,
            "заказ соседнего кафе изменил состояние",
        )

    def test_closing_table_touches_only_own_orders(self):
        """«Стол 5» есть у обоих кафе — закрыть надо ровно свой."""
        res = self.client.post(
            "/api/orders/close_table/",
            {"table": "Стол 5", "pay_method": "cash"},
            format="json",
            **self._as("waiter"),
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.json()["closed"], 1, "закрыто не одно заведение")

        self.data_a["order"].refresh_from_db()
        self.data_b["order"].refresh_from_db()
        self.assertEqual(self.data_a["order"].status, Order.Status.PAID)
        self.assertEqual(
            self.data_b["order"].status, Order.Status.OPEN,
            "закрыт счёт стола соседнего кафе",
        )

    def test_cannot_add_foreign_dish_to_own_order(self):
        res = self.client.post(
            f"/api/orders/{self.data_a['order'].pk}/add_items/",
            {"items": [{"product": self.data_b["food"].pk, "quantity": 1}]},
            format="json",
            **self._as("waiter"),
        )
        self.assertNotEqual(res.status_code, 200, "в заказ добавлено чужое блюдо")


@override_settings(ALLOWED_HOSTS=["*"])
class RegistrationAndLoyaltyIsolationTests(TestCase):
    """Гости, регистрация и бонусы.

    Один и тот же человек может ходить в оба кафе — и в каждом он свой
    гость со своим балансом. Проверяем, что один телефон не мешает
    зарегистрироваться в соседнем и что бонусы не перетекают.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"
    PHONE = "+79990001122"

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)
        for org in (self.a, self.b):
            with organization_context(org):
                site = SiteSettings.load()
                site.plan = SiteSettings.Plan.MAX
                site.bonus_enabled = True
                site.save()
        self.client = APIClient()

    def _register(self, host):
        return self.client.post(
            "/api/auth/register/",
            {"name": "Гость", "phone": self.PHONE},
            format="json",
            HTTP_HOST=host,
        )

    def test_same_phone_can_register_in_both_cafes(self):
        """Телефон уникален внутри кафе, а не на всю базу: иначе гость
        одного заведения не смог бы зарегистрироваться в другом."""
        self.assertEqual(self._register(self.HOST_A).status_code, 201)
        self.assertEqual(self._register(self.HOST_B).status_code, 201)

        self.assertEqual(User.objects.filter(phone=self.PHONE).count(), 2)
        for org, host in ((self.a, self.HOST_A), (self.b, self.HOST_B)):
            self.assertTrue(
                User.objects.filter(phone=self.PHONE, organization=org).exists(),
                f"регистрация на {host} ушла не в то заведение",
            )

    def test_repeat_registration_in_same_cafe_is_refused(self):
        self.assertEqual(self._register(self.HOST_A).status_code, 201)
        self.assertEqual(self._register(self.HOST_A).status_code, 400)

    def test_bonus_balance_does_not_leak_between_cafes(self):
        self._register(self.HOST_A)
        self._register(self.HOST_B)
        with organization_context(self.b):
            guest_b = User.objects.get(phone=self.PHONE, organization=self.b)
            LoyaltyMember.objects.create(user=guest_b, balance=5000)

        with organization_context(self.a):
            guest_a = User.objects.get(phone=self.PHONE, organization=self.a)
            member_a, _ = LoyaltyMember.objects.get_or_create(
                user=guest_a, defaults={"balance": 0}
            )
        self.assertEqual(member_a.balance, 0, "баланс пришёл из соседнего кафе")


@override_settings(ALLOWED_HOSTS=["*"])
class PaymentIsolationTests(TestCase):
    """Оплаты: деньги не должны попасть в чужой отчёт.

    Самое опасное место — уведомление банка: оно приходит извне, и если
    платёж ищется по всей базе, оплата одного кафе закроет заказ другого.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"

    def setUp(self):
        from payments.models import Payment

        self.Payment = Payment
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)

        self.orders, self.payments = {}, {}
        for key, org in (("a", self.a), ("b", self.b)):
            with organization_context(org):
                SiteSettings.load()
                order = Order.objects.create(total=500)
                self.orders[key] = order
                # Внешний id банка намеренно ОДИНАКОВЫЙ: банки нумеруют
                # платежи по своему терминалу, и совпадение возможно.
                self.payments[key] = Payment.objects.create(
                    order=order, amount=500, provider="tbank",
                    external_id="SAME-BANK-ID", status=Payment.Status.PENDING,
                )
        self.client = APIClient()

    def test_bank_callback_applies_to_the_right_cafe(self):
        """Уведомление приходит на домен кафе — там платёж и должен найтись."""
        with organization_context(self.a):
            own = self.Payment.objects.filter(external_id="SAME-BANK-ID").first()
        self.assertEqual(own.pk, self.payments["a"].pk)
        with organization_context(self.b):
            other = self.Payment.objects.filter(external_id="SAME-BANK-ID").first()
        self.assertEqual(other.pk, self.payments["b"].pk)

    def test_guest_cannot_pay_foreign_order_by_token(self):
        """Оплата по токену: с чужого домена заказ найтись не должен."""
        import uuid

        with organization_context(self.b):
            foreign = self.orders["b"]
            foreign.public_token = uuid.uuid4()
            foreign.save(update_fields=["public_token"])
        res = self.client.post(
            "/api/orders/pay_online/",
            {"token": str(foreign.public_token)},
            format="json",
            HTTP_HOST=self.HOST_A,
        )
        self.assertEqual(res.status_code, 404, "чужой заказ найден по токену")

    def test_payments_of_other_cafe_are_invisible(self):
        with organization_context(self.a):
            self.assertEqual(self.Payment.objects.count(), 1)
        self.assertEqual(self.Payment.all_objects.count(), 2)


@override_settings(ALLOWED_HOSTS=["*"])
class ReportsIsolationTests(TestCase):
    """Отчёты: прибыль, склад, закуп, выручка.

    Здесь утечка не выглядит утечкой — она выглядит неверными цифрами.
    Владелец увидит чужую выручку в своей прибыли и будет принимать
    решения по ней, не подозревая, откуда она взялась.
    """

    HOST_A = "alpha.padacha.ru"
    HOST_B = "beta.padacha.ru"
    HUGE = 999999

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST_A, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(name="Бета", slug="beta", domain=self.HOST_B)

        for org, marker, amount in ((self.a, "альфы", 100), (self.b, "беты", self.HUGE)):
            with organization_context(org):
                site = SiteSettings.load()
                site.plan = SiteSettings.Plan.MAX
                site.save()
                category = Category.objects.create(name=f"Кат {marker}")
                product = Product.objects.create(
                    name=f"Блюдо {marker}", price=amount, category=category
                )
                order = Order.objects.create(
                    total=amount, status=Order.Status.PAID,
                    closed_at=timezone.now(), pay_method="cash",
                )
                OrderItem.objects.create(
                    order=order, product=product, quantity=1, unit_price=amount
                )
                stock_category = StockCategory.objects.create(name=f"Склад {marker}")
                StockItem.objects.create(
                    name=f"Позиция {marker}", category=stock_category,
                    unit="g", quantity=amount, min_quantity=amount * 2,
                )
                ExpenseCategory.objects.create(name=f"Статья {marker}")

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

    def _clean(self, url, params=None):
        res = self.client.get(url, params or {}, **self.auth)
        self.assertEqual(res.status_code, 200, f"{url}: {res.data}")
        body = str(res.json())
        self.assertNotIn(
            str(self.HUGE), body, f"{url}: в отчёт попали данные соседнего кафе"
        )
        return res.json()

    def test_profit_report_counts_own_money_only(self):
        self._clean("/api/finance/payroll/report/")

    def test_payroll_days_of_foreign_employee_are_empty(self):
        """Расшифровку просят по id работника — чужой id не должен ничего
        показать, даже если его угадали."""
        with organization_context(self.b):
            stranger = User.objects.create_user(
                "работник-беты", role=User.Role.WAITER, organization=self.b
            )
            shift = Shift.objects.create(date=timezone.localdate())
            shift.members.create(user=stranger, organization=self.b)
        res = self.client.get(
            "/api/finance/payroll/days/", {"user": stranger.pk}, **self.auth
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            res.json()["days"], [], "показаны смены работника соседнего кафе"
        )

    def test_payroll_payout_to_foreign_employee_is_refused(self):
        with organization_context(self.b):
            stranger = User.objects.create_user(
                "чужой-работник", role=User.Role.WAITER, organization=self.b
            )
        res = self.client.post(
            "/api/finance/payroll/pay/",
            {"user": stranger.pk, "amount": "1000"},
            format="json",
            **self.auth,
        )
        self.assertEqual(res.status_code, 400, "выплата ушла работнику чужого кафе")

    def test_stock_list_is_own(self):
        body = self._clean("/api/inventory/items/")
        rows = body if isinstance(body, list) else body.get("results", [])
        names = str([r.get("name") for r in rows])
        self.assertIn("альфы", names)
        self.assertNotIn("беты", names)

    def test_purchase_list_is_own(self):
        """Закуп собирается из позиций ниже минимума — у обоих кафе они есть."""
        self._clean("/api/inventory/purchases/day/")

    def test_expense_summary_is_own(self):
        self._clean("/api/finance/expenses/")

    def test_orders_feed_for_admin_panel_is_own(self):
        """Плитки админ-панели считаются по этому же списку заказов."""
        self._clean("/api/orders/")


@override_settings(ALLOWED_HOSTS=["*"])
class AdminFullIsolationTests(TestCase):
    """Django-админка целиком: проходим ВСЕ зарегистрированные модели.

    Точечные тесты проверяют разделы, о которых вспомнили. Этот берёт
    список из самой админки, поэтому новая модель попадает под проверку
    сама — а не тогда, когда кто-то вспомнит дописать тест.

    Надтенантные разделы (заведения, клиенты, подписки, платежи «Падачи»)
    исключены осознанно: они и должны показывать всё — это инструмент
    владельца продукта, а не заведения.
    """

    HOST = "alpha.padacha.ru"

    #: Разделы, которые смотрят поверх заведений — им фильтр не нужен.
    AUTHORITY = {
        "core.Organization",
        "billing.Client",
        "billing.Subscription",
        "billing.Payment",
    }

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name, self.a.slug = self.HOST, "Альфа", "alpha"
        self.a.save()
        self.b = Organization.objects.create(
            name="Бета", slug="beta", domain="beta.padacha.ru"
        )
        self.data_a = build_cafe(self.a, "альфы")
        self.data_b = build_cafe(self.b, "беты")
        with organization_context(self.a):
            self.root = User.objects.create_user(
                "root", password="Sh4-root-pass", role=User.Role.ADMIN,
                organization=self.a,
            )
            self.root.is_staff = self.root.is_superuser = True
            self.root.save()
        self.client.force_login(self.root)

    def _tenant_admins(self):
        """Зарегистрированные админки моделей, принадлежащих заведению."""
        from django.contrib import admin as dj_admin

        from core.tenancy import TenantMixin

        for model, model_admin in dj_admin.site._registry.items():
            label = f"{model._meta.app_label}.{model.__name__}"
            if label in self.AUTHORITY or not issubclass(model, TenantMixin):
                continue
            yield label, model, model_admin

    def test_every_admin_queryset_is_limited_to_current_cafe(self):
        """Белым ящиком: выборка каждой админки не выходит за заведение.

        Проверяем именно queryset, а не страницу: так под контроль
        попадают и модели, у которых сейчас нет данных, — завтра будут.
        """
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.tenancy import set_current_organization

        request = RequestFactory().get("/admin/", HTTP_HOST=self.HOST)
        request.user = self.root
        set_current_organization(self.a)

        for label, model, model_admin in self._tenant_admins():
            with self.subTest(model=label):
                foreign = model.all_objects.filter(organization=self.b) if hasattr(
                    model, "all_objects"
                ) else model.objects.filter(organization=self.b)
                if not foreign.exists():
                    continue  # данных нет — проверять нечего, но и дыры нет
                shown = model_admin.get_queryset(request)
                leaked = shown.filter(organization=self.b).count()
                self.assertEqual(
                    leaked, 0,
                    f"{label}: админка показывает {leaked} записей соседнего кафе",
                )
        del AnonymousUser

    def test_changelists_open_and_do_not_show_foreign_rows(self):
        """Чёрным ящиком: открываем каждую страницу списка по-настоящему."""
        for label, model, model_admin in self._tenant_admins():
            app, name = label.split(".")
            url = f"/admin/{app.lower()}/{name.lower()}/"
            with self.subTest(url=url):
                res = self.client.get(url, HTTP_HOST=self.HOST)
                if res.status_code == 403:
                    continue  # раздел закрыт правами — тем более не утечёт
                self.assertEqual(res.status_code, 200, f"{url} не открылся")
                html = res.content.decode()
                foreign_qs = (
                    model.all_objects if hasattr(model, "all_objects") else model.objects
                ).filter(organization=self.b)
                for obj in foreign_qs[:5]:
                    self.assertNotIn(
                        f"/{obj.pk}/change/", html,
                        f"{url}: в списке оказалась запись соседнего кафе",
                    )

    def test_foreign_object_page_is_not_found(self):
        """Прямая ссылка на карточку чужого объекта не должна открываться."""
        cases = [
            ("catalog/product", self.data_b["product"]),
            ("orders/order", self.data_b["order"]),
            ("users/user", self.data_b["staff"]),
            ("inventory/stockitem", self.data_b["stock_item"]),
            ("core/sitesettings", None),
        ]
        for path, obj in cases:
            if obj is None:
                continue
            with self.subTest(path=path):
                res = self.client.get(
                    f"/admin/{path}/{obj.pk}/change/", HTTP_HOST=self.HOST
                )
                self.assertIn(
                    res.status_code, (302, 403, 404),
                    f"/admin/{path}/{obj.pk}/ открыл чужой объект",
                )

    def test_own_rows_are_visible(self):
        """Обратная проверка: свои записи в админке видны.

        Без неё тест прошёл бы и на полностью пустой админке.
        """
        res = self.client.get("/admin/catalog/product/", HTTP_HOST=self.HOST)
        self.assertEqual(res.status_code, 200)
        self.assertIn(f"/{self.data_a['product'].pk}/change/", res.content.decode())

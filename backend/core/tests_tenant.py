"""Изоляция заведений в общей базе.

Ради этих тестов всё и затевалось. Пока заведение одно, ошибиться негде;
опасность появляется ровно тогда, когда в базе их два — и проверять надо
именно этот случай, а не «работает как раньше».
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from catalog.models import Category, Product
from core.models import Organization, SiteSettings
from core.tenancy import (
    NoOrganizationSelected,
    current_organization,
    organization_context,
    set_current_organization,
)
from orders.models import Order
from users.models import User


def make_org(name, domain, slug):
    return Organization.objects.create(name=name, slug=slug, domain=domain)


class TwoOrganizationsTests(TestCase):
    def setUp(self):
        # Первая организация уже есть — её создала миграция тенантности.
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain = "alpha.padacha.ru"
        self.a.name = "Альфа"
        self.a.save()
        self.b = make_org("Бета", "beta.padacha.ru", "beta")

        with organization_context(self.a):
            self.cat_a = Category.objects.create(name="Кофе Альфы")
            self.product_a = Product.objects.create(
                name="Латте Альфы", price=300, category=self.cat_a
            )
        with organization_context(self.b):
            self.cat_b = Category.objects.create(name="Кофе Беты")
            self.product_b = Product.objects.create(
                name="Латте Беты", price=400, category=self.cat_b
            )

    def test_each_sees_only_its_own(self):
        with organization_context(self.a):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Латте Альфы"]
            )
        with organization_context(self.b):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Латте Беты"]
            )
        # без фильтра видно оба — это escape hatch поддержки
        self.assertEqual(Product.all_objects.count(), 2)

    def test_ambiguous_context_raises_instead_of_guessing(self):
        """Без выбранного заведения код обязан упасть, а не выбрать первое.

        Молчаливый выбор «первого» — это и есть та самая утечка: отчёт
        одного кафе посчитался бы по данным другого.
        """
        set_current_organization(None)
        with self.assertRaises(NoOrganizationSelected):
            current_organization()

    def test_settings_are_separate(self):
        with organization_context(self.a):
            site = SiteSettings.load()
            site.name = "Кафе Альфа"
            site.save()
        with organization_context(self.b):
            self.assertNotEqual(SiteSettings.load().name, "Кафе Альфа")

    def test_same_login_in_different_organizations(self):
        """«owner» есть у каждого кафе — иначе имена раздавались бы в порядке
        живой очереди."""
        with organization_context(self.a):
            User.objects.create_user("owner", password="Sh4-pass-alpha", role="admin")
        with organization_context(self.b):
            User.objects.create_user("owner", password="Sh4-pass-beta", role="admin")
        self.assertEqual(User.objects.filter(username="owner").count(), 2)


@override_settings(ALLOWED_HOSTS=["*"])
class TenantByDomainTests(TestCase):
    """Опознание заведения по домену — через настоящие HTTP-запросы."""

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain, self.a.name = "alpha.padacha.ru", "Альфа"
        self.a.save()
        self.b = make_org("Бета", "beta.padacha.ru", "beta")
        for org, title in ((self.a, "Альфа"), (self.b, "Бета")):
            with organization_context(org):
                site = SiteSettings.load()
                site.name = title
                site.save()
        self.client = APIClient()

    def test_site_api_answers_per_domain(self):
        for host, expected in (
            ("alpha.padacha.ru", "Альфа"),
            ("beta.padacha.ru", "Бета"),
            ("www.beta.padacha.ru", "Бета"),  # www и порт отбрасываются
            ("beta.padacha.ru:8000", "Бета"),
        ):
            res = self.client.get("/api/site/", HTTP_HOST=host)
            self.assertEqual(res.status_code, 200, host)
            self.assertEqual(res.json()["name"], expected, host)

    def test_unknown_domain_is_not_served(self):
        res = self.client.get("/api/site/", HTTP_HOST="unknown.example.com")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "unknown_tenant")

    def test_disabled_organization_is_closed(self):
        self.b.is_active = False
        self.b.save()
        res = self.client.get("/api/site/", HTTP_HOST="beta.padacha.ru")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "tenant_disabled")

    def test_menu_is_per_domain(self):
        with organization_context(self.a):
            cat = Category.objects.create(name="Кофе")
            Product.objects.create(name="Латте Альфы", price=300, category=cat)
        names = [
            p["name"]
            for p in self.client.get("/api/products/", HTTP_HOST="alpha.padacha.ru").json()
        ]
        self.assertEqual(names, ["Латте Альфы"])
        self.assertEqual(
            self.client.get("/api/products/", HTTP_HOST="beta.padacha.ru").json(), []
        )


@override_settings(ALLOWED_HOSTS=["*"])
class CrossTenantAuthTests(TestCase):
    """Токен и вход не должны работать в чужом заведении."""

    def setUp(self):
        self.a = Organization.objects.order_by("pk").first()
        self.a.domain = "alpha.padacha.ru"
        self.a.save()
        self.b = make_org("Бета", "beta.padacha.ru", "beta")
        with organization_context(self.a):
            self.user_a = User.objects.create_user(
                "owner", password="Sh4-pass-alpha", role="admin"
            )
        with organization_context(self.b):
            User.objects.create_user("owner", password="Sh4-pass-beta", role="admin")
        self.client = APIClient()

    def _token(self, host, password="Sh4-pass-alpha"):
        res = self.client.post(
            "/api/auth/token/",
            {"username": "owner", "password": password},
            format="json",
            HTTP_HOST=host,
        )
        return res

    def test_login_resolves_user_within_its_domain(self):
        ok = self._token("alpha.padacha.ru")
        self.assertEqual(ok.status_code, 200)
        # пароль соседнего «owner» на этом домене не подходит
        wrong = self._token("alpha.padacha.ru", password="Sh4-pass-beta")
        self.assertEqual(wrong.status_code, 401)

    def test_token_does_not_work_on_another_domain(self):
        token = self._token("alpha.padacha.ru").json()["access"]
        mine = self.client.get(
            "/api/users/me/",
            HTTP_HOST="alpha.padacha.ru",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(mine.status_code, 200)

        stolen = self.client.get(
            "/api/users/me/",
            HTTP_HOST="beta.padacha.ru",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(stolen.status_code, 401)

    def test_orders_of_another_cafe_are_invisible(self):
        with organization_context(self.b):
            cat = Category.objects.create(name="Кофе Беты")
            product = Product.objects.create(name="Латте", price=100, category=cat)
            Order.objects.create(total=100, client=None)
        token = self._token("alpha.padacha.ru").json()["access"]
        res = self.client.get(
            "/api/orders/",
            HTTP_HOST="alpha.padacha.ru",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])
        del product


class ImportInstanceTests(TestCase):
    """Переезд отдельной установки в общую.

    Главная опасность переезда — ключи: в отдельных базах у каждой точки
    есть заказ №1 и товар №1. Проверяем, что данные не перетирают друг
    друга, ссылки внутри дампа остаются связными, а счётчики id после
    вставки с явными ключами не начинают выдавать занятые номера.
    """

    #: Дамп «старой установки»: поля organization в нём нет — такие
    #: установки живут на коде старше тенантности.
    DUMP = [
        {"model": "catalog.category", "pk": 1,
         "fields": {"name": "Кофе", "station": "bar", "sort_order": 0,
                    "is_active": True, "icon": ""}},
        {"model": "catalog.product", "pk": 1,
         "fields": {"name": "Раф", "price": "350.00", "category": 1,
                    "is_available": True, "sort_order": 0, "description": "",
                    "weight_grams": None, "image": "", "thumbnail": "",
                    "prep_minutes": None, "is_stopped": False}},
        {"model": "core.sitesettings", "pk": 1,
         "fields": {"name": "Переехавшее кафе", "theme": "neutral",
                    "service_mode": "counter", "plan": "max"}},
    ]

    def setUp(self):
        self.home = Organization.objects.order_by("pk").first()
        self.home.domain = "home.padacha.ru"
        self.home.save()
        with organization_context(self.home):
            cat = Category.objects.create(name="Свой кофе")
            self.own = Product.objects.create(name="Свой раф", price=300, category=cat)

    def _import(self, domain="pereezd.padacha.ru"):
        import json
        import tempfile

        from django.core.management import call_command

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(self.DUMP, fh)
            path = fh.name
        call_command("import_instance", path, domain=domain, verbosity=0)
        return Organization.objects.get(domain=domain)

    def test_import_does_not_overwrite_existing_rows(self):
        """У приезжающих те же id, что у своих, — но перетереть не должны."""
        org = self._import()
        self.own.refresh_from_db()
        self.assertEqual(self.own.name, "Свой раф")  # своё на месте

        with organization_context(org):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Раф"]
            )
            # связь внутри дампа осталась связной
            self.assertEqual(Product.objects.first().category.name, "Кофе")
            # настройки переехали вместе с данными
            self.assertEqual(SiteSettings.load().service_mode, "counter")

        with organization_context(self.home):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Свой раф"]
            )

    def test_sequences_are_reset_after_import(self):
        """Строки вставлены с явными ключами выше счётчика — без сброса
        следующая запись столкнулась бы с занятым id."""
        self._import()
        with organization_context(self.home):
            cat = Category.objects.first()
            fresh = Product.objects.create(name="Новый", price=100, category=cat)
        self.assertTrue(Product.all_objects.filter(pk=fresh.pk).count() == 1)

    def test_repeat_import_is_refused(self):
        from django.core.management.base import CommandError

        self._import()
        with self.assertRaises(CommandError):
            self._import()

class ImportInstanceTests(TestCase):
    """Переезд отдельной установки в общую.

    Главная опасность переезда — ключи: в отдельных базах у каждой точки
    есть заказ №1 и товар №1. Проверяем, что данные не перетирают друг
    друга, ссылки внутри дампа остаются связными, а счётчики id после
    вставки с явными ключами не начинают выдавать занятые номера.
    """

    #: Дамп «старой установки»: поля organization в нём нет — такие
    #: установки живут на коде старше тенантности.
    DUMP = [
        {"model": "catalog.category", "pk": 1,
         "fields": {"name": "Кофе", "station": "bar", "sort_order": 0,
                    "is_active": True, "icon": ""}},
        {"model": "catalog.product", "pk": 1,
         "fields": {"name": "Раф", "price": "350.00", "category": 1,
                    "is_available": True, "sort_order": 0, "description": "",
                    "weight_grams": None, "image": "", "thumbnail": "",
                    "prep_minutes": None, "is_stopped": False}},
        {"model": "core.sitesettings", "pk": 1,
         "fields": {"name": "Переехавшее кафе", "theme": "neutral",
                    "service_mode": "counter", "plan": "max"}},
    ]

    def setUp(self):
        self.home = Organization.objects.order_by("pk").first()
        self.home.domain = "home.padacha.ru"
        self.home.save()
        with organization_context(self.home):
            cat = Category.objects.create(name="Свой кофе")
            self.own = Product.objects.create(name="Свой раф", price=300, category=cat)

    def _import(self, domain="pereezd.padacha.ru"):
        import json
        import tempfile

        from django.core.management import call_command

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(self.DUMP, fh)
            path = fh.name
        call_command("import_instance", path, domain=domain, verbosity=0)
        return Organization.objects.get(domain=domain)

    def test_import_does_not_overwrite_existing_rows(self):
        """У приезжающих те же id, что у своих, — но перетереть не должны."""
        org = self._import()
        self.own.refresh_from_db()
        self.assertEqual(self.own.name, "Свой раф")  # своё на месте

        with organization_context(org):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Раф"]
            )
            # связь внутри дампа осталась связной
            self.assertEqual(Product.objects.first().category.name, "Кофе")
            # настройки переехали вместе с данными
            self.assertEqual(SiteSettings.load().service_mode, "counter")

        with organization_context(self.home):
            self.assertEqual(
                list(Product.objects.values_list("name", flat=True)), ["Свой раф"]
            )

    def test_sequences_are_reset_after_import(self):
        """Строки вставлены с явными ключами выше счётчика — без сброса
        следующая запись столкнулась бы с занятым id."""
        self._import()
        with organization_context(self.home):
            cat = Category.objects.first()
            fresh = Product.objects.create(name="Новый", price=100, category=cat)
        self.assertTrue(Product.all_objects.filter(pk=fresh.pk).count() == 1)

    def test_repeat_import_is_refused(self):
        from django.core.management.base import CommandError

        self._import()
        with self.assertRaises(CommandError):
            self._import()

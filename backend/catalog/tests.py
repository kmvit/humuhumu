import tempfile
from decimal import Decimal
from io import BytesIO

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from orders.models import Order, OrderItem
from users.models import User

from .models import Category, Product, ProductVariant


def image_file(name="dish.png"):
    """Настоящий PNG: ImageField проверяет содержимое, заглушка не пройдёт."""
    buf = BytesIO()
    Image.new("RGB", (40, 40), "red").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class CatalogAdminBase(APITestCase):
    """Владелец правит каталог с фронта, без Django-админки."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="boss", password="demo12345", role=User.Role.ADMIN
        )
        self.waiter = User.objects.create_user(
            username="barista", password="demo12345", role=User.Role.WAITER
        )
        self.cat = Category.objects.create(name="Кофе", station="bar")
        self.latte = Product.objects.create(category=self.cat, name="Латте")
        self.latte_v = ProductVariant.objects.create(
            product=self.latte, price=Decimal("240")
        )

    def auth(self, user):
        res = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": "demo12345"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")


class ProductCrudTests(CatalogAdminBase):
    def test_admin_creates_product(self):
        self.auth(self.admin)
        res = self.client.post(
            "/api/products/",
            {"category": self.cat.id, "name": "Раф",
             "variants": [{"price": "320"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        raf = Product.objects.get(name="Раф")
        self.assertEqual(raf.variants.get().price, Decimal("320"))

    def test_product_needs_a_price(self):
        """Товар без единой цены — сломанная карточка, не создаём."""
        self.auth(self.admin)
        res = self.client.post(
            "/api/products/",
            {"category": self.cat.id, "name": "Раф", "variants": []},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_admin_adds_sizes(self):
        """Один товар — несколько объёмов со своими ценами."""
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/",
            {"variants": [
                {"id": self.latte_v.id, "label": "0,3 л", "price": "240"},
                {"label": "0,4 л", "price": "290"},
            ]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        labels = list(
            self.latte.variants.order_by("sort_order").values_list("label", flat=True)
        )
        self.assertEqual(labels, ["0,3 л", "0,4 л"])

    def test_sold_size_is_hidden_not_deleted(self):
        """Убрали проданный размер из формы — он прячется, история цела."""
        order = Order.objects.create(status=Order.Status.OPEN, table="5")
        OrderItem.objects.create(
            order=order, variant=self.latte_v, quantity=1, unit_price=Decimal("240")
        )
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/",
            {"variants": [{"label": "0,4 л", "price": "290"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.latte_v.refresh_from_db()
        self.assertFalse(self.latte_v.is_active)  # скрыт, но существует

    def test_guest_does_not_see_hidden_sizes(self):
        self.latte_v.is_active = False
        self.latte_v.save(update_fields=["is_active"])
        ProductVariant.objects.create(
            product=self.latte, label="0,4 л", price=Decimal("290")
        )
        res = self.client.get("/api/products/")
        card = [x for x in res.data if x["id"] == self.latte.id][0]
        self.assertEqual([v["label"] for v in card["variants"]], ["0,4 л"])

    def test_admin_edits_price(self):
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/",
            {"variants": [{"id": self.latte_v.id, "price": "260"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.latte_v.refresh_from_db()
        self.assertEqual(self.latte_v.price, Decimal("260"))

    def test_admin_deletes_unused_product(self):
        self.auth(self.admin)
        res = self.client.delete(f"/api/products/{self.latte.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=self.latte.pk).exists())

    def test_product_in_order_cannot_be_deleted(self):
        """История заказов важнее: вместо 500 объясняем и подсказываем выход."""
        order = Order.objects.create(status=Order.Status.OPEN, table="5")
        OrderItem.objects.create(
            order=order, variant=self.latte_v, quantity=1, unit_price=Decimal("240")
        )
        self.auth(self.admin)
        res = self.client.delete(f"/api/products/{self.latte.id}/")
        self.assertEqual(res.status_code, 409)
        self.assertIn("В меню", res.data["detail"])
        self.assertTrue(Product.objects.filter(pk=self.latte.pk).exists())

    def test_waiter_cannot_edit_catalog(self):
        self.auth(self.waiter)
        self.assertEqual(
            self.client.patch(
                f"/api/products/{self.latte.id}/", {"name": "X"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/products/{self.latte.id}/").status_code, 403
        )

    def test_guest_cannot_edit_catalog(self):
        res = self.client.post(
            "/api/products/",
            {"category": self.cat.id, "name": "X", "variants": [{"price": "1"}]},
            format="json",
        )
        self.assertIn(res.status_code, (401, 403))


class LegacyClientTests(CatalogAdminBase):
    """Планшет с закэшированным старым бандлом должен доработать смену.

    PWA держит свой js в кэше, и сразу после деплоя официант какое-то время
    сидит на прежней версии: она читает price у товара и отправляет заказ по
    product, ничего не зная о вариантах. Пока такие бандлы не вымоются,
    ломать их нельзя — это живая смена в кафе.
    """

    def test_menu_still_exposes_price_on_product(self):
        res = self.client.get("/api/products/")
        card = [x for x in res.data if x["id"] == self.latte.id][0]
        self.assertEqual(card["price"], "240.00")
        self.assertEqual(card["is_stopped"], False)

    def test_price_comes_from_the_first_size(self):
        ProductVariant.objects.filter(pk=self.latte_v.pk).update(label="0,3 л")
        ProductVariant.objects.create(
            product=self.latte, label="0,5 л", price=Decimal("300"), sort_order=1
        )
        res = self.client.get("/api/products/")
        card = [x for x in res.data if x["id"] == self.latte.id][0]
        self.assertEqual(card["price"], "240.00")  # цена «от», а не последняя

    def test_product_is_stopped_only_when_every_size_is(self):
        """Иначе старый бандл спрячет блюдо, у которого встал один объём."""
        ProductVariant.objects.create(
            product=self.latte, label="0,5 л", price=Decimal("300"),
            is_stopped=True, sort_order=1,
        )
        res = self.client.get("/api/products/")
        card = [x for x in res.data if x["id"] == self.latte.id][0]
        self.assertFalse(card["is_stopped"])  # 0,3 л продаётся — блюдо живо

        self.latte.variants.update(is_stopped=True)
        res = self.client.get("/api/products/")
        card = [x for x in res.data if x["id"] == self.latte.id][0]
        self.assertTrue(card["is_stopped"])

    def test_old_form_creates_product_with_price(self):
        """Старый бандл админки шлёт price у товара — сохранение должно пройти."""
        self.auth(self.admin)
        res = self.client.post(
            "/api/products/",
            {"category": self.cat.id, "name": "Раф", "price": "320",
             "weight_grams": 300, "is_stopped": False},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        v = Product.objects.get(name="Раф").variants.get()
        self.assertEqual(v.price, Decimal("320"))
        self.assertEqual(v.weight_grams, 300)
        self.assertEqual(v.label, "")

    def test_old_form_edits_price(self):
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/", {"price": "265"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.latte_v.refresh_from_db()
        self.assertEqual(self.latte_v.price, Decimal("265"))

    def test_old_form_stop_covers_every_size(self):
        """Старая форма знает один стоп на товар — гасим все объёмы разом."""
        big = ProductVariant.objects.create(
            product=self.latte, label="0,5 л", price=Decimal("300"), sort_order=1
        )
        self.auth(self.admin)
        self.client.patch(
            f"/api/products/{self.latte.id}/", {"is_stopped": True}, format="json"
        )
        self.latte_v.refresh_from_db(); big.refresh_from_db()
        self.assertTrue(self.latte_v.is_stopped)
        self.assertTrue(big.is_stopped)  # иначе «стоп» нажат, а блюдо продаётся

    def test_old_form_does_not_drop_other_sizes(self):
        """Правка ценой из старой формы не должна снести объёмы, которых она не видит."""
        big = ProductVariant.objects.create(
            product=self.latte, label="0,5 л", price=Decimal("300"), sort_order=1
        )
        self.auth(self.admin)
        self.client.patch(
            f"/api/products/{self.latte.id}/", {"price": "265"}, format="json"
        )
        self.assertEqual(self.latte.variants.count(), 2)
        big.refresh_from_db()
        self.assertEqual(big.price, Decimal("300"))  # второй объём цел

    def test_edit_without_price_keeps_variants(self):
        """Правка одного описания не должна трогать цены."""
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/", {"description": "Мягкий"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.latte.variants.count(), 1)
        self.latte_v.refresh_from_db()
        self.assertEqual(self.latte_v.price, Decimal("240"))

    def test_order_by_product_is_still_accepted(self):
        """Старый бандл шлёт product без варианта — заказ должен пройти."""
        res = self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "table": "5",
             "items": [{"product": self.latte.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        item = Order.objects.latest("id").items.get()
        self.assertEqual(item.variant_id, self.latte_v.id)
        self.assertEqual(item.unit_price, Decimal("240"))

    def test_order_by_product_picks_the_first_size(self):
        ProductVariant.objects.create(
            product=self.latte, label="0,5 л", price=Decimal("300"), sort_order=1
        )
        self.client.post(
            "/api/orders/place/",
            {"customer_name": "Гость", "table": "5",
             "items": [{"product": self.latte.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(Order.objects.latest("id").items.get().variant_id, self.latte_v.id)


# Картинки пишутся на диск: без подмены MEDIA_ROOT тесты засоряют media/
# заведения файлами dish_*.png
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProductImageTests(CatalogAdminBase):
    """Картинку блюда владелец загружает с фронта — раньше поле было только на чтение."""

    def test_upload_image_creates_thumbnail(self):
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/",
            {"image": image_file()},
            format="multipart",
        )
        self.assertEqual(res.status_code, 200)
        self.latte.refresh_from_db()
        self.assertTrue(self.latte.image)
        self.assertTrue(self.latte.thumbnail)  # превью собирается само
        self.assertTrue(res.data["image"].startswith("/media/"))  # относительный URL

    def test_create_product_with_image(self):
        self.auth(self.admin)
        res = self.client.post(
            "/api/products/",
            {"category": self.cat.id, "name": "Раф",
             "variants": '[{"price": "320"}]', "image": image_file()},
            format="multipart",
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Product.objects.get(name="Раф").image)

    def test_image_can_be_cleared(self):
        self.auth(self.admin)
        self.client.patch(
            f"/api/products/{self.latte.id}/", {"image": image_file()}, format="multipart"
        )
        res = self.client.patch(
            f"/api/products/{self.latte.id}/", {"image": None}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.latte.refresh_from_db()
        self.assertFalse(self.latte.image)


class CategoryCrudTests(CatalogAdminBase):
    def test_admin_creates_and_edits_category(self):
        self.auth(self.admin)
        res = self.client.post(
            "/api/categories/", {"name": "Десерты", "station": "kitchen"}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        cid = res.data["id"]
        res = self.client.patch(
            f"/api/categories/{cid}/", {"name": "Сладкое"}, format="json"
        )
        self.assertEqual(res.data["name"], "Сладкое")

    def test_admin_sees_inactive_category(self):
        """Иначе выключенную категорию нечем включить обратно."""
        Category.objects.create(name="Сезонное", station="bar", is_active=False)
        self.auth(self.admin)
        names = [c["name"] for c in self.client.get("/api/categories/").data]
        self.assertIn("Сезонное", names)

    def test_guest_does_not_see_inactive_category(self):
        Category.objects.create(name="Сезонное", station="bar", is_active=False)
        names = [c["name"] for c in self.client.get("/api/categories/").data]
        self.assertNotIn("Сезонное", names)

    def test_category_with_products_cannot_be_deleted(self):
        self.auth(self.admin)
        res = self.client.delete(f"/api/categories/{self.cat.id}/")
        self.assertEqual(res.status_code, 409)
        self.assertIn("товары", res.data["detail"])

    def test_empty_category_deletes(self):
        empty = Category.objects.create(name="Пусто", station="bar")
        self.auth(self.admin)
        self.assertEqual(
            self.client.delete(f"/api/categories/{empty.id}/").status_code, 204
        )

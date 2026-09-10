import tempfile
from decimal import Decimal
from io import BytesIO

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from orders.models import Order, OrderItem
from users.models import User

from .models import Category, Product


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
        self.latte = Product.objects.create(
            category=self.cat, name="Латте", price=Decimal("240")
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
            {"category": self.cat.id, "name": "Раф", "price": "320"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Product.objects.filter(name="Раф").exists())

    def test_admin_edits_product(self):
        self.auth(self.admin)
        res = self.client.patch(
            f"/api/products/{self.latte.id}/", {"price": "260"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.latte.refresh_from_db()
        self.assertEqual(self.latte.price, Decimal("260"))

    def test_admin_deletes_unused_product(self):
        self.auth(self.admin)
        res = self.client.delete(f"/api/products/{self.latte.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=self.latte.pk).exists())

    def test_product_in_order_cannot_be_deleted(self):
        """История заказов важнее: вместо 500 объясняем и подсказываем выход."""
        order = Order.objects.create(status=Order.Status.OPEN, table="5")
        OrderItem.objects.create(
            order=order, product=self.latte, quantity=1, unit_price=Decimal("240")
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
                f"/api/products/{self.latte.id}/", {"price": "1"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/products/{self.latte.id}/").status_code, 403
        )

    def test_guest_cannot_edit_catalog(self):
        res = self.client.post(
            "/api/products/", {"category": self.cat.id, "name": "X", "price": "1"},
            format="json",
        )
        self.assertIn(res.status_code, (401, 403))


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
            {"category": self.cat.id, "name": "Раф", "price": "320", "image": image_file()},
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

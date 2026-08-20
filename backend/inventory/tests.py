from decimal import Decimal

from rest_framework.test import APITestCase

from catalog.models import Category, Product
from users.models import User

from .models import RecipeItem, StockCategory, StockItem, StockMovement


class StockItemDeleteTests(APITestCase):
    """Удаление товара склада: чистый — насовсем, с историей — прячем."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username="manager", password="pw", role=User.Role.WAREHOUSE
        )
        self.cat = StockCategory.objects.create(name="Молоко")
        res = self.client.post(
            "/api/auth/token/",
            {"username": "manager", "password": "pw"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def _item(self, name="Тест"):
        return StockItem.objects.create(
            category=self.cat, name=name, unit=StockItem.Unit.PIECE
        )

    def test_clean_item_is_hard_deleted(self):
        item = self._item()
        res = self.client.delete(f"/api/inventory/items/{item.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(StockItem.objects.filter(id=item.id).exists())

    def test_item_with_movements_is_deactivated(self):
        item = self._item()
        item.apply_movement(
            Decimal("5"), StockMovement.Kind.ADJUST, user=self.manager
        )
        res = self.client.delete(f"/api/inventory/items/{item.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["deactivated"])
        item.refresh_from_db()
        self.assertFalse(item.is_active)  # остался в базе, но скрыт

    def test_item_in_recipe_is_deactivated(self):
        item = self._item()
        menu_cat = Category.objects.create(name="Кофе")
        product = Product.objects.create(
            category=menu_cat, name="Латте", price=Decimal("300")
        )
        RecipeItem.objects.create(product=product, item=item, quantity=Decimal("50"))
        res = self.client.delete(f"/api/inventory/items/{item.id}/")
        self.assertEqual(res.status_code, 200)
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        # тех карта не пострадала
        self.assertTrue(RecipeItem.objects.filter(item=item).exists())

    def test_waiter_cannot_delete(self):
        item = self._item()
        waiter = User.objects.create_user(
            username="w", password="pw", role=User.Role.WAITER
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "w", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.assertEqual(
            self.client.delete(f"/api/inventory/items/{item.id}/").status_code, 403
        )
        self.assertTrue(StockItem.objects.filter(id=item.id).exists())

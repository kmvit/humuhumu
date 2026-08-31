from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APITestCase

from catalog.models import Category, Product
from users.models import User

from .models import Receipt, RecipeItem, StockCategory, StockItem, StockMovement


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


class ReceiptDeleteTests(APITestCase):
    """Удаление прихода менеджером с откатом остатков."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username="manager", password="pw", role=User.Role.WAREHOUSE
        )
        self.cat = StockCategory.objects.create(name="Крупы")
        self.item = StockItem.objects.create(
            category=self.cat, name="Мука", unit=StockItem.Unit.GRAM
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "manager", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def _receipt(self, qty="5000", cost="0.05"):
        return self.client.post(
            "/api/inventory/receipts/",
            {"items": [{"item": self.item.id, "quantity": qty, "unit_cost": cost}]},
            format="json",
        )

    def test_delete_receipt_rolls_back_stock(self):
        self._receipt(qty="5000")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("5000.000"))

        receipt = Receipt.objects.get()
        res = self.client.delete(f"/api/inventory/receipts/{receipt.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Receipt.objects.filter(id=receipt.id).exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("0.000"))  # остаток откачен
        # движения прихода тоже убраны
        self.assertFalse(
            self.item.movements.filter(kind=StockMovement.Kind.RECEIPT).exists()
        )

    def test_delete_keeps_other_receipts(self):
        self._receipt(qty="5000")
        self._receipt(qty="3000")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("8000.000"))

        first = Receipt.objects.order_by("id").first()
        self.client.delete(f"/api/inventory/receipts/{first.id}/")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("3000.000"))  # остался второй

    def test_waiter_cannot_delete_receipt(self):
        self._receipt()
        receipt = Receipt.objects.get()
        waiter = User.objects.create_user(
            username="w", password="pw", role=User.Role.WAITER
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "w", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.assertEqual(
            self.client.delete(f"/api/inventory/receipts/{receipt.id}/").status_code,
            403,
        )
        self.assertTrue(Receipt.objects.filter(id=receipt.id).exists())


class ReceiptScanUnitsTests(TestCase):
    """Распознавание чека: цена должна приводиться к базовой единице.

    Это была ошибка в тысячу раз: количество из килограммов переводилось в
    граммы, а цена «180 ₽ за кг» переносилась как 180 ₽ за грамм.
    """

    def setUp(self):
        self.cat = StockCategory.objects.create(name="Продукты")
        self.tomato = StockItem.objects.create(
            name="Помидоры", unit="g", category=self.cat
        )
        self.milk = StockItem.objects.create(name="Молоко", unit="ml", category=self.cat)
        self.cup = StockItem.objects.create(name="Стакан", unit="pcs", category=self.cat)

    def draft(self, lines):
        from inventory.receipt_ai import build_draft

        return build_draft({"supplier": "МЕТРО", "date": None, "total": None, "lines": lines})

    def test_price_per_kg_becomes_price_per_gram(self):
        d = self.draft([
            {"name": "Помидоры", "quantity": 2.73, "unit": "кг",
             "unit_cost": 180, "line_total": None}
        ])
        line = d["lines"][0]
        self.assertEqual(line["base_quantity"], 2730)
        self.assertAlmostEqual(line["unit_cost"], 0.18, places=4)

    def test_line_total_wins_over_unit_price(self):
        """Сумма по строке точнее: она уже со скидкой."""
        d = self.draft([
            {"name": "Помидоры", "quantity": 2.0, "unit": "кг",
             "unit_cost": 200, "line_total": 300}
        ])
        # 300 ₽ за 2000 г = 0.15, а не 0.20 из цены за кг
        self.assertAlmostEqual(d["lines"][0]["unit_cost"], 0.15, places=4)

    def test_litres_convert_to_millilitres(self):
        d = self.draft([
            {"name": "Молоко", "quantity": 1.5, "unit": "л",
             "unit_cost": 90, "line_total": None}
        ])
        line = d["lines"][0]
        self.assertEqual(line["base_quantity"], 1500)
        self.assertAlmostEqual(line["unit_cost"], 0.09, places=4)

    def test_pieces_keep_price_as_is(self):
        d = self.draft([
            {"name": "Стакан", "quantity": 100, "unit": "шт",
             "unit_cost": 7, "line_total": None}
        ])
        line = d["lines"][0]
        self.assertEqual(line["base_quantity"], 100)
        self.assertAlmostEqual(line["unit_cost"], 7, places=4)

    def test_missing_price_stays_empty(self):
        d = self.draft([
            {"name": "Помидоры", "quantity": 1, "unit": "кг",
             "unit_cost": None, "line_total": None}
        ])
        self.assertIsNone(d["lines"][0]["unit_cost"])

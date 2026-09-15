from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from catalog.models import (
    Category,
    Modifier,
    ModifierEffect,
    ModifierGroup,
    Product,
    ProductVariant,
)
from orders.models import Order, OrderItem, OrderItemModifier

from .services import return_order_item, write_off_order_item
from core.models import SiteSettings
from users.models import User

from .models import (
    Receipt,
    ReceiptScan,
    RecipeItem,
    ScanQuota,
    StockCategory,
    StockItem,
    StockMovement,
)


class StockItemDeleteTests(APITestCase):
    """Удаление товара склада: чистый — насовсем, с историей — прячем."""

    def setUp(self):
        # тесты писались до тарифов и проверяют функционал «Максимума»
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
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
        product = Product.objects.create(category=menu_cat, name="Латте")
        variant = ProductVariant.objects.create(product=product, price=Decimal("300"))
        RecipeItem.objects.create(variant=variant, item=item, quantity=Decimal("50"))
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
        # тесты писались до тарифов и проверяют функционал «Максимума»
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
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


class RecipeApiTests(APITestCase):
    """Тех карта блюда: сохранили — значит она есть и в списке, и в блюде."""

    def setUp(self):
        # тесты писались до тарифов и проверяют функционал «Максимума»
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        User.objects.create_user(
            username="manager", password="pw", role=User.Role.WAREHOUSE
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "manager", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

        self.item = StockItem.objects.create(
            category=StockCategory.objects.create(name="Бакалея"),
            name="Рис",
            unit=StockItem.Unit.GRAM,
        )
        self.product = Product.objects.create(
            category=Category.objects.create(name="Горячее"), name="Боул"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, price=Decimal("500")
        )

    def _save(self, lines, variant=None):
        vid = (variant or self.variant).id
        return self.client.put(
            f"/api/inventory/recipes/{vid}/", {"lines": lines}, format="json"
        )

    def _card(self, data, variant=None):
        vid = (variant or self.variant).id
        return next(c for c in data if c["variant"] == vid)

    def test_saved_card_comes_back_everywhere(self):
        res = self._save([{"item": self.item.id, "quantity": "150"}])
        self.assertEqual(res.status_code, 200)
        # ответ на сохранение, список и само блюдо обязаны совпадать: карта
        # «сохранилась», но нигде не показалась — это и был баг bulk_create
        self.assertEqual(len(res.data["lines"]), 1)
        self.assertEqual(RecipeItem.objects.count(), 1)

        listed = self._card(self.client.get("/api/inventory/recipes/").data)
        self.assertEqual(len(listed["lines"]), 1)
        one = self.client.get(f"/api/inventory/recipes/{self.variant.id}/")
        self.assertEqual(len(one.data["lines"]), 1)
        self.assertEqual(Decimal(one.data["lines"][0]["quantity"]), Decimal("150"))

    def test_saving_again_replaces_the_composition(self):
        self._save([{"item": self.item.id, "quantity": "150"}])
        other = StockItem.objects.create(
            category=self.item.category, name="Креветки", unit=StockItem.Unit.GRAM
        )
        res = self._save([{"item": other.id, "quantity": "80"}])
        self.assertEqual(
            [line["item"] for line in res.data["lines"]], [other.id]
        )
        self.assertEqual(RecipeItem.objects.count(), 1)

    def test_sizes_keep_separate_cards(self):
        """У объёмов состав свой: правка 0,5 не трогает 0,33."""
        big = ProductVariant.objects.create(
            product=self.product, label="0,5", price=Decimal("700")
        )
        self._save([{"item": self.item.id, "quantity": "150"}])
        self._save([{"item": self.item.id, "quantity": "250"}], variant=big)

        listed = self.client.get("/api/inventory/recipes/").data
        small_card = self._card(listed)
        big_card = self._card(listed, variant=big)
        self.assertEqual(Decimal(small_card["lines"][0]["quantity"]), Decimal("150"))
        self.assertEqual(Decimal(big_card["lines"][0]["quantity"]), Decimal("250"))
        # обе карты принадлежат одной карточке меню — фронт их сгруппирует
        self.assertEqual(small_card["product"], big_card["product"])

    def test_empty_lines_clear_the_card(self):
        self._save([{"item": self.item.id, "quantity": "150"}])
        res = self._save([])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["lines"], [])
        self.assertEqual(RecipeItem.objects.count(), 0)


class ReceiptScanUnitsTests(TestCase):
    """Распознавание чека: цена должна приводиться к базовой единице.

    Это была ошибка в тысячу раз: количество из килограммов переводилось в
    граммы, а цена «180 ₽ за кг» переносилась как 180 ₽ за грамм.
    """

    def setUp(self):
        # тесты писались до тарифов и проверяют функционал «Максимума»
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
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

class ScanQuotaTests(APITestCase):
    """Лимит распознаваний: 30 чеков в месяц на заведение.

    Распознавание — единственная функция, за каждое обращение к которой мы
    платим деньгами, и она входит в оба тарифа. Без лимита самая дешёвая
    точка могла бы выбрать всю маржу одна.
    """

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.COUNTER  # распознавание есть и на стойке
        site.save()
        self.manager = User.objects.create_user(
            username="manager", password="pw", role=User.Role.WAREHOUSE
        )
        self.client.force_authenticate(self.manager)

    def _photo(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (4, 4), "white").save(buf, "JPEG")
        return SimpleUploadedFile("chek.jpg", buf.getvalue(), content_type="image/jpeg")

    def _upload(self):
        # распознавание мокаем: проверяем учёт, а не работу модели
        with mock.patch("inventory.views.process_receipt_scan"):
            return self.client.post(
                "/api/inventory/receipt-scans/",
                {"image": self._photo()},
                format="multipart",
            )

    def _seed(self, used, extra=0):
        return ScanQuota.objects.create(
            month=timezone.localdate().replace(day=1), used=used, extra=extra
        )

    def test_upload_spends_one_scan(self):
        self.assertEqual(self._upload().status_code, 201)
        row = ScanQuota.objects.get(month=timezone.localdate().replace(day=1))
        self.assertEqual(row.used, 1)

    def test_last_scan_passes_and_the_next_is_refused(self):
        self._seed(used=ScanQuota.MONTHLY_LIMIT - 1)
        self.assertEqual(self._upload().status_code, 201)

        res = self._upload()
        self.assertEqual(res.status_code, 402)
        self.assertIn("лимит", res.json()["detail"].lower())
        # отказ — до создания записи: списка «сканов, за которые отругали» нет
        self.assertEqual(ReceiptScan.objects.count(), 1)

    def test_deleting_scans_does_not_return_the_limit(self):
        """Считаем отдельным счётчиком именно поэтому: сканы можно удалять,
        и уборка в списке молча возвращала бы оплаченные распознавания."""
        self._seed(used=ScanQuota.MONTHLY_LIMIT - 1)
        scan_id = self._upload().json()["id"]
        self.client.delete(f"/api/inventory/receipt-scans/{scan_id}/")
        self.assertEqual(self._upload().status_code, 402)

    def test_paid_pack_raises_the_limit(self):
        self._seed(used=ScanQuota.MONTHLY_LIMIT, extra=50)
        self.assertEqual(self._upload().status_code, 201)

    def test_quota_endpoint_tells_what_is_left(self):
        self._seed(used=4)
        data = self.client.get("/api/inventory/receipt-scans/quota/").json()
        self.assertEqual(data["used"], 4)
        self.assertEqual(data["limit"], ScanQuota.MONTHLY_LIMIT)
        self.assertEqual(data["left"], ScanQuota.MONTHLY_LIMIT - 4)


class ModifierWriteOffTests(APITestCase):
    """Списание с опциями: добавка, снятие и замена.

    Проверяем главное обещание модели: «на овсяном» описано ОДИН раз и
    остаётся верным для любого объёма, потому что количество берётся из
    тех карты проданного варианта, а не из самой опции.
    """

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        cat = StockCategory.objects.create(name="Бар")
        self.milk = StockItem.objects.create(category=cat, name="Молоко коровье", unit="ml")
        self.oat = StockItem.objects.create(category=cat, name="Молоко овсяное", unit="ml")
        self.beans = StockItem.objects.create(category=cat, name="Зерно", unit="g")
        self.syrup = StockItem.objects.create(category=cat, name="Сироп", unit="ml")
        for item in (self.milk, self.oat, self.beans, self.syrup):
            item.apply_movement(Decimal("10000"), StockMovement.Kind.RECEIPT)

        menu = Category.objects.create(name="Кофе", station="bar")
        self.product = Product.objects.create(category=menu, name="Латте")
        self.small = ProductVariant.objects.create(
            product=self.product, label="0,3 л", price=Decimal("300")
        )
        self.big = ProductVariant.objects.create(
            product=self.product, label="0,5 л", price=Decimal("400"), sort_order=1
        )
        # расход нелинейный — ровно поэтому опции не могут хранить количество
        for variant, milk_ml, syrup_ml in ((self.small, 200, 20), (self.big, 350, 35)):
            RecipeItem.objects.create(variant=variant, item=self.milk, quantity=Decimal(milk_ml))
            RecipeItem.objects.create(variant=variant, item=self.beans, quantity=Decimal("18"))
            RecipeItem.objects.create(variant=variant, item=self.syrup, quantity=Decimal(syrup_ml))

        self.group = ModifierGroup.objects.create(name="Молоко", min_choices=0, max_choices=1)
        self.group.products.add(self.product)
        self.oat_mod = Modifier.objects.create(
            group=self.group, name="Овсяное", price_delta=Decimal("60")
        )
        ModifierEffect.objects.create(
            modifier=self.oat_mod, kind=ModifierEffect.Kind.SWAP,
            item=self.milk, replacement=self.oat,
        )
        extras = ModifierGroup.objects.create(name="Добавки", max_choices=3)
        extras.products.add(self.product)
        self.shot = Modifier.objects.create(
            group=extras, name="+ шот эспрессо", price_delta=Decimal("80")
        )
        ModifierEffect.objects.create(
            modifier=self.shot, kind=ModifierEffect.Kind.ADD,
            item=self.beans, quantity=Decimal("18"),
        )
        self.no_syrup = Modifier.objects.create(group=extras, name="Без сиропа")
        ModifierEffect.objects.create(
            modifier=self.no_syrup, kind=ModifierEffect.Kind.REMOVE, item=self.syrup
        )

    def _sell(self, variant, modifiers=(), quantity=1):
        order = Order.objects.create(status=Order.Status.OPEN, table="5")
        item = OrderItem.objects.create(
            order=order, variant=variant, quantity=quantity, unit_price=variant.price
        )
        for m in modifiers:
            OrderItemModifier.objects.create(
                order_item=item, modifier=m, name=m.name, price_delta=m.price_delta
            )
        return item

    def _left(self, item):
        item.refresh_from_db()
        return item.quantity

    def test_without_options_card_rules(self):
        write_off_order_item(self._sell(self.small))
        self.assertEqual(self._left(self.milk), Decimal("9800.000"))
        self.assertEqual(self._left(self.oat), Decimal("10000.000"))

    def test_swap_takes_quantity_from_the_sold_size(self):
        """Одна опция «Овсяное» — верна и для 0,3, и для 0,5."""
        write_off_order_item(self._sell(self.small, [self.oat_mod]))
        self.assertEqual(self._left(self.milk), Decimal("10000.000"))  # коровье не тронуто
        self.assertEqual(self._left(self.oat), Decimal("9800.000"))    # 200 мл — из карты 0,3

        write_off_order_item(self._sell(self.big, [self.oat_mod]))
        self.assertEqual(self._left(self.oat), Decimal("9450.000"))    # ещё 350 мл — из карты 0,5

    def test_add_is_a_fixed_amount(self):
        write_off_order_item(self._sell(self.small, [self.shot]))
        self.assertEqual(self._left(self.beans), Decimal("9964.000"))  # 18 из карты + 18 опции

    def test_remove_drops_the_ingredient(self):
        write_off_order_item(self._sell(self.small, [self.no_syrup]))
        self.assertEqual(self._left(self.syrup), Decimal("10000.000"))
        self.assertEqual(self._left(self.milk), Decimal("9800.000"))  # остальное на месте

    def test_options_stack(self):
        write_off_order_item(self._sell(self.big, [self.oat_mod, self.shot, self.no_syrup]))
        self.assertEqual(self._left(self.milk), Decimal("10000.000"))
        self.assertEqual(self._left(self.oat), Decimal("9650.000"))    # 350 вместо коровьего
        self.assertEqual(self._left(self.beans), Decimal("9964.000"))  # 18 + 18
        self.assertEqual(self._left(self.syrup), Decimal("10000.000"))

    def test_quantity_multiplies_options_too(self):
        write_off_order_item(self._sell(self.small, [self.shot], quantity=3))
        self.assertEqual(self._left(self.beans), Decimal("9892.000"))  # (18+18) × 3

    def test_swap_of_missing_ingredient_adds_nothing(self):
        """Заменять нечего — подставлять замену «из воздуха» нельзя."""
        RecipeItem.objects.filter(variant=self.small, item=self.milk).delete()
        write_off_order_item(self._sell(self.small, [self.oat_mod]))
        self.assertEqual(self._left(self.oat), Decimal("10000.000"))

    def test_return_gives_back_exactly_what_was_taken(self):
        item = self._sell(self.big, [self.oat_mod, self.shot])
        write_off_order_item(item)
        return_order_item(item)
        for stock in (self.milk, self.oat, self.beans, self.syrup):
            self.assertEqual(self._left(stock), Decimal("10000.000"), stock.name)

    def test_movement_comment_names_the_options(self):
        write_off_order_item(self._sell(self.small, [self.oat_mod]))
        comment = StockMovement.objects.filter(kind="sale").latest("id").comment
        self.assertIn("Латте 0,3 л", comment)
        self.assertIn("Овсяное", comment)

class StockCategoryCrudTests(APITestCase):
    """Категории склада: переименование и удаление прямо в интерфейсе."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        User.objects.create_user(
            username="manager", password="pw", role=User.Role.WAREHOUSE
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "manager", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.cat = StockCategory.objects.create(name="Бакалея")

    def test_rename(self):
        res = self.client.patch(
            f"/api/inventory/categories/{self.cat.id}/", {"name": "Крупы"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.name, "Крупы")

    def test_empty_category_is_deleted(self):
        res = self.client.delete(f"/api/inventory/categories/{self.cat.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(StockCategory.objects.filter(id=self.cat.id).exists())

    def test_category_with_items_is_refused(self):
        """Прятать её вместо удаления нельзя: товары исчезли бы из остатков."""
        StockItem.objects.create(
            category=self.cat, name="Рис", unit=StockItem.Unit.GRAM
        )
        res = self.client.delete(f"/api/inventory/categories/{self.cat.id}/")
        self.assertEqual(res.status_code, 409)
        self.assertIn("перенесите", res.data["detail"])
        self.assertTrue(StockCategory.objects.filter(id=self.cat.id).exists())

    def test_list_reports_how_many_items_inside(self):
        StockItem.objects.create(
            category=self.cat, name="Рис", unit=StockItem.Unit.GRAM
        )
        res = self.client.get("/api/inventory/categories/")
        row = [c for c in res.data if c["id"] == self.cat.id][0]
        self.assertEqual(row["items_count"], 1)

    def test_item_can_be_moved_to_another_category(self):
        """Путь, который предлагает сообщение об отказе, должен работать."""
        other = StockCategory.objects.create(name="Напитки")
        item = StockItem.objects.create(
            category=self.cat, name="Рис", unit=StockItem.Unit.GRAM
        )
        res = self.client.patch(
            f"/api/inventory/items/{item.id}/", {"category": other.id}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.client.delete(f"/api/inventory/categories/{self.cat.id}/").status_code,
            204,
        )

    def test_waiter_cannot_touch_categories(self):
        waiter = User.objects.create_user(
            username="w", password="pw", role=User.Role.WAITER
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "w", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.assertEqual(
            self.client.patch(
                f"/api/inventory/categories/{self.cat.id}/", {"name": "X"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/inventory/categories/{self.cat.id}/").status_code,
            403,
        )


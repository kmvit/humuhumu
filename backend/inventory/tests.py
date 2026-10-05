from datetime import timedelta
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
    ReceiptItem,
    ReceiptScan,
    RecipeItem,
    ScanQuota,
    StockCategory,
    StockItem,
    StockMovement,
    WriteOff,
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

    def test_delete_keeps_stock_set_outside_the_journal(self):
        self._receipt(qty="5000")
        # инвентаризацию провели в админке, мимо движений: 5000 → 7000
        StockItem.objects.filter(pk=self.item.pk).update(quantity=Decimal("7000"))
        self.client.delete(f"/api/inventory/receipts/{Receipt.objects.get().id}/")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("2000"))

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

    def test_item_list_has_last_unit_cost(self):
        """Форма прихода подставляет сумму по цене из последнего прихода."""
        def cost():
            res = self.client.get("/api/inventory/items/")
            row = next(r for r in res.data if r["id"] == self.item.id)
            return row["last_unit_cost"]

        self.assertIsNone(cost())  # приходов ещё не было
        self._receipt(cost="0.05")
        self._receipt(cost="0.314700")
        self._receipt(cost=None)  # приход без цены прошлую не затирает
        self.assertEqual(Decimal(cost()), Decimal("0.3147"))


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



class ReceiptPriceScaleTests(APITestCase):
    """Цена за грамм и миллилитр в копейки не укладывается.

    Боевой случай Монти: канистра концентрата 5 л за 1 148 ₽ — это
    0,2296 ₽ за мл. Приход не сохранялся вовсе: «убедитесь, что вы ввели
    не более 2 цифр после запятой». Склад в граммах и миллилитрах, так
    что это был не редкий случай, а почти любой приход с ценой.
    """

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        self.manager = User.objects.create_user(
            username="manager-price", password="pw", role=User.Role.WAREHOUSE
        )
        cat = StockCategory.objects.create(name="Сиропы")
        self.item = StockItem.objects.create(
            category=cat, name="Концентрат клубника", unit=StockItem.Unit.MILLILITER
        )
        res = self.client.post(
            "/api/auth/token/", {"username": "manager-price", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def receipt(self, quantity, unit_cost):
        return self.client.post(
            "/api/inventory/receipts/",
            {"items": [{"item": self.item.id, "quantity": quantity, "unit_cost": unit_cost}]},
            format="json",
        )

    def test_price_per_millilitre_is_accepted(self):
        res = self.receipt("5000", "0.2296")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(ReceiptItem.objects.get().unit_cost, Decimal("0.229600"))

    def test_cheap_goods_keep_their_price(self):
        """Бутыль воды 19 л за 200 ₽ — 0,010526 ₽ за мл; округление до копейки
        превратило бы её в 0,01 и соврало бы в себестоимости на проценты."""
        res = self.receipt("19000", "0.010526")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(ReceiptItem.objects.get().unit_cost, Decimal("0.010526"))

    def test_sum_of_receipt_stays_correct(self):
        self.receipt("5000", "0.2296")
        self.assertEqual(Receipt.objects.get().total_cost, Decimal("1148.0000000"))

    def test_endless_fraction_is_rounded_not_refused(self):
        """Фронт шлёт частное как есть: 200 ₽ ÷ 19 000 мл = 0,010526315789…

        Отказ здесь означал бы «приход провести нельзя» — деление почти
        всегда даёт длинный хвост.
        """
        res = self.receipt("19000", "0.010526315789473684")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(ReceiptItem.objects.get().unit_cost, Decimal("0.010526"))

    def test_price_still_cannot_be_negative(self):
        self.assertEqual(self.receipt("5000", "-1").status_code, 400)


class WriteOffTests(APITestCase):
    """Списания мимо продажи: заготовка, порча, взяли сотрудники."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        User.objects.create_user(
            username="sklad", password="pw", role=User.Role.WAREHOUSE
        )
        cat = StockCategory.objects.create(name="Молочка")
        self.cream = StockItem.objects.create(
            category=cat, name="Сливки 33%", unit=StockItem.Unit.MILLILITER
        )
        self.sugar = StockItem.objects.create(
            category=cat, name="Сахарная пудра", unit=StockItem.Unit.GRAM
        )
        self._login("sklad")
        # приход с ценой: 1000 мл сливок за 500 ₽, пудра без цены
        self.client.post(
            "/api/inventory/receipts/",
            {
                "items": [
                    {"item": self.cream.id, "quantity": "1000", "unit_cost": "0.5"},
                    {"item": self.sugar.id, "quantity": "500"},
                ]
            },
            format="json",
        )

    def _login(self, username):
        res = self.client.post(
            "/api/auth/token/", {"username": username, "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def _write_off(self, lines, title="Сливочная шапка", reason="не продали"):
        return self.client.post(
            "/api/inventory/write-offs/",
            {"title": title, "reason": reason, "items": lines},
            format="json",
        )

    def test_write_off_reduces_stock_and_logs_movement(self):
        res = self._write_off([
            {"item": self.cream.id, "quantity": "200"},
            {"item": self.sugar.id, "quantity": "30"},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        self.cream.refresh_from_db()
        self.sugar.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("800"))
        self.assertEqual(self.sugar.quantity, Decimal("470"))
        mv = self.cream.movements.get(kind=StockMovement.Kind.WRITE_OFF)
        self.assertEqual(mv.delta, Decimal("-200"))
        self.assertIn("Сливочная шапка", mv.comment)
        self.assertIn("не продали", mv.comment)

    def test_cost_is_counted_by_last_price(self):
        res = self._write_off([
            {"item": self.cream.id, "quantity": "200"},
            {"item": self.sugar.id, "quantity": "30"},
        ])
        # 200 мл × 0,5 ₽; у пудры цены нет — в сумму не входит
        self.assertEqual(Decimal(res.data["total_cost"]), Decimal("100.00"))

    def test_half_kopeck_rounds_up(self):
        # 15,5 мл × 0,75 ₽ = 11,625 ₽ → 11,63, как в форме, а не 11,62
        self.client.post(
            "/api/inventory/receipts/",
            {"items": [{"item": self.cream.id, "quantity": "100", "unit_cost": "0.75"}]},
            format="json",
        )
        res = self._write_off([{"item": self.cream.id, "quantity": "15.5"}])
        self.assertEqual(res.data["total_cost"], "11.63")
        self.assertEqual(res.data["items"][0]["subtotal"], "11.63")

    def test_price_is_frozen_at_write_off(self):
        self._write_off([{"item": self.cream.id, "quantity": "100"}])
        self.client.post(
            "/api/inventory/receipts/",
            {"items": [{"item": self.cream.id, "quantity": "1000", "unit_cost": "2"}]},
            format="json",
        )
        wo = WriteOff.objects.get()
        self.assertEqual(wo.total_cost, Decimal("50"))  # прежняя цена

    def test_reason_and_lines_are_required(self):
        self.assertEqual(
            self._write_off([{"item": self.cream.id, "quantity": "1"}], reason="").status_code,
            400,
        )
        self.assertEqual(self._write_off([]).status_code, 400)

    def test_same_item_twice_is_summed(self):
        self._write_off([
            {"item": self.cream.id, "quantity": "100"},
            {"item": self.cream.id, "quantity": "50"},
        ])
        wo = WriteOff.objects.get()
        self.assertEqual(wo.items.count(), 1)
        self.assertEqual(wo.items.get().quantity, Decimal("150"))

    def test_stock_may_go_negative(self):
        res = self._write_off([{"item": self.cream.id, "quantity": "1500"}])
        self.assertEqual(res.status_code, 201)
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("-500"))

    def test_delete_returns_goods(self):
        self._write_off([{"item": self.cream.id, "quantity": "200"}])
        wo = WriteOff.objects.get()
        self.assertEqual(
            self.client.delete(f"/api/inventory/write-offs/{wo.id}/").status_code, 204
        )
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("1000"))
        self.assertFalse(
            self.cream.movements.filter(kind=StockMovement.Kind.WRITE_OFF).exists()
        )

    def test_delete_keeps_stock_set_outside_the_journal(self):
        # остаток правили в админке мимо движений: 1000 → 1980
        StockItem.objects.filter(pk=self.cream.pk).update(quantity=Decimal("1980"))
        self._write_off([{"item": self.cream.id, "quantity": "15.5"}])
        wo = WriteOff.objects.get()
        self.client.delete(f"/api/inventory/write-offs/{wo.id}/")
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("1980"))

    def test_list_is_by_month(self):
        self._write_off([{"item": self.cream.id, "quantity": "10"}])
        old = WriteOff.objects.get()
        WriteOff.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=62)
        )
        self._write_off([{"item": self.cream.id, "quantity": "10"}], title="Капучино")
        res = self.client.get("/api/inventory/write-offs/")
        self.assertEqual([w["title"] for w in res.data], ["Капучино"])
        month = timezone.localtime(
            WriteOff.objects.get(pk=old.pk).created_at
        ).strftime("%Y-%m")
        res = self.client.get(f"/api/inventory/write-offs/?month={month}")
        self.assertEqual([w["title"] for w in res.data], ["Сливочная шапка"])

    # ——— несколько позиций за раз ———

    def _batch(self, positions, reason="Не продали"):
        return self.client.post(
            "/api/inventory/write-offs/batch/",
            {"reason": reason, "positions": positions},
            format="json",
        )

    def test_batch_makes_one_write_off_per_position(self):
        res = self._batch([
            {"title": "Сливочная шапка", "items": [
                {"item": self.cream.id, "quantity": "100"},
                {"item": self.sugar.id, "quantity": "20"},
            ]},
            {"title": "Капучино × 2", "items": [
                {"item": self.cream.id, "quantity": "50"},
            ]},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual([w["title"] for w in res.data], ["Сливочная шапка", "Капучино × 2"])
        self.assertEqual({w.reason for w in WriteOff.objects.all()}, {"Не продали"})
        self.cream.refresh_from_db()
        self.sugar.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("850"))
        self.assertEqual(self.sugar.quantity, Decimal("480"))
        # у каждой позиции своя сумма: 100 мл × 0,5 и 50 мл × 0,5
        self.assertEqual([w["total_cost"] for w in res.data], ["50.00", "25.00"])

    def test_batch_is_all_or_nothing(self):
        """Ошибка в одной позиции — не списывается ни одна."""
        res = self._batch([
            {"title": "Сливочная шапка", "items": [{"item": self.cream.id, "quantity": "100"}]},
            {"title": "Капучино", "items": []},
        ])
        self.assertEqual(res.status_code, 400)
        self.assertFalse(WriteOff.objects.exists())
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("1000"))

    def test_batch_fails_whole_if_db_breaks_midway(self):
        """Даже если сломалось уже при записи второй позиции — первая откатывается."""
        from . import serializers as sers

        real = sers._write_off
        calls = []

        def flaky(**kw):
            calls.append(kw["title"])
            if len(calls) == 2:
                raise RuntimeError("база упала")
            return real(**kw)

        with mock.patch.object(sers, "_write_off", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self._batch([
                    {"title": "Первая", "items": [{"item": self.cream.id, "quantity": "100"}]},
                    {"title": "Вторая", "items": [{"item": self.cream.id, "quantity": "100"}]},
                ])
        self.assertFalse(WriteOff.objects.exists())
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("1000"))

    def test_batch_needs_reason_and_positions(self):
        self.assertEqual(self._batch([]).status_code, 400)
        res = self._batch(
            [{"title": "Шапка", "items": [{"item": self.cream.id, "quantity": "1"}]}],
            reason="",
        )
        self.assertEqual(res.status_code, 400)
        res = self._batch([{"title": "", "items": [{"item": self.cream.id, "quantity": "1"}]}])
        self.assertEqual(res.status_code, 400)
        self.assertFalse(WriteOff.objects.exists())

    def test_batch_positions_delete_separately(self):
        res = self._batch([
            {"title": "Шапка", "items": [{"item": self.cream.id, "quantity": "100"}]},
            {"title": "Латте", "items": [{"item": self.cream.id, "quantity": "30"}]},
        ])
        self.client.delete(f"/api/inventory/write-offs/{res.data[0]['id']}/")
        self.cream.refresh_from_db()
        self.assertEqual(self.cream.quantity, Decimal("970"))  # осталось только латте
        self.assertEqual(list(WriteOff.objects.values_list("title", flat=True)), ["Латте"])

    def test_waiter_cannot_batch(self):
        User.objects.create_user(username="w2", password="pw", role=User.Role.WAITER)
        self._login("w2")
        res = self._batch([{"title": "Шапка", "items": [{"item": self.cream.id, "quantity": "1"}]}])
        self.assertEqual(res.status_code, 403)

    def test_waiter_cannot_write_off(self):
        User.objects.create_user(username="w", password="pw", role=User.Role.WAITER)
        self._login("w")
        res = self._write_off([{"item": self.cream.id, "quantity": "10"}])
        self.assertEqual(res.status_code, 403)


class WriteOffWarehouseEffectsTests(APITestCase):
    """Как списания отражаются на остальном складе: остатки, закуп, история,
    удаление товара, инвентаризация, приходы, продажи и тех карты."""

    def setUp(self):
        site = SiteSettings.load()
        site.plan = SiteSettings.Plan.HALL
        site.save()
        User.objects.create_user(username="sklad", password="pw", role=User.Role.WAREHOUSE)
        res = self.client.post(
            "/api/auth/token/", {"username": "sklad", "password": "pw"}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        cat = StockCategory.objects.create(name="Молочка")
        # порог 300 мл, держим 1000
        self.milk = StockItem.objects.create(
            category=cat, name="Молоко", unit=StockItem.Unit.MILLILITER,
            min_quantity=Decimal("300"), target_quantity=Decimal("1000"),
        )
        self._receipt("1000", "0.1")

    def _receipt(self, qty, cost):
        return self.client.post(
            "/api/inventory/receipts/",
            {"items": [{"item": self.milk.id, "quantity": qty, "unit_cost": cost}]},
            format="json",
        )

    def _write_off(self, qty, title="Шапка"):
        res = self.client.post(
            "/api/inventory/write-offs/",
            {"title": title, "reason": "Не продали",
             "items": [{"item": self.milk.id, "quantity": qty}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

    def _item(self):
        rows = self.client.get("/api/inventory/items/").data
        return next(r for r in rows if r["id"] == self.milk.id)

    def test_stock_list_shows_low_and_shortage_after_write_off(self):
        self.assertFalse(self._item()["is_low"])
        self._write_off("750")  # 1000 → 250, ниже порога 300
        row = self._item()
        self.assertEqual(Decimal(row["quantity"]), Decimal("250"))
        self.assertTrue(row["is_low"])
        self.assertEqual(Decimal(row["shortage"]), Decimal("750"))

    def test_purchase_list_picks_up_item_written_off_below_threshold(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        before = self.client.get(f"/api/inventory/purchases/day/?date={tomorrow}").data
        self.assertEqual(before["lines"], [])
        self._write_off("750")
        after = self.client.get(f"/api/inventory/purchases/day/?date={tomorrow}").data
        self.assertEqual([l["item"] for l in after["lines"]], [self.milk.id])
        self.assertEqual(Decimal(after["lines"][0]["quantity"]), Decimal("750"))

    def test_deleting_write_off_restores_not_low(self):
        wo = self._write_off("750")
        self.client.delete(f"/api/inventory/write-offs/{wo['id']}/")
        row = self._item()
        self.assertEqual(Decimal(row["quantity"]), Decimal("1000"))
        self.assertFalse(row["is_low"])

    def test_movement_history_shows_write_off(self):
        self._write_off("100", title="Сливочная шапка")
        moves = self.client.get(f"/api/inventory/items/{self.milk.id}/movements/").data
        last = moves[0]
        self.assertEqual(last["kind"], "writeoff")
        self.assertEqual(last["kind_display"], "Списание")
        self.assertEqual(Decimal(last["delta"]), Decimal("-100"))
        self.assertEqual(last["comment"], "Сливочная шапка — Не продали")
        self.assertEqual(last["created_by_name"], "sklad")

    def test_item_with_write_off_is_hidden_not_deleted(self):
        """Товар из списания удалить насовсем нельзя (на него ссылается
        журнал) — он прячется, а журнал списаний не ломается."""
        self._write_off("100")
        res = self.client.delete(f"/api/inventory/items/{self.milk.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["deactivated"])
        journal = self.client.get("/api/inventory/write-offs/").data
        self.assertEqual(journal[0]["items"][0]["item_name"], "Молоко")

    def test_hidden_item_cannot_be_written_off(self):
        StockItem.objects.filter(pk=self.milk.pk).update(is_active=False)
        res = self.client.post(
            "/api/inventory/write-offs/",
            {"title": "Шапка", "reason": "Не продали",
             "items": [{"item": self.milk.id, "quantity": "10"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("1000"))

    def test_inventory_count_after_write_off(self):
        """Инвентаризация ставит остаток как насчитали, списание — не мешает."""
        self._write_off("100")  # 900
        self.client.post(
            f"/api/inventory/items/{self.milk.id}/adjust/",
            {"quantity": "850", "comment": "пересчёт"}, format="json",
        )
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("850"))
        kinds = list(self.milk.movements.order_by("id").values_list("kind", "delta"))
        self.assertEqual(
            kinds,
            [("receipt", Decimal("1000")), ("writeoff", Decimal("-100")), ("adjust", Decimal("-50"))],
        )

    def test_receipt_deleted_after_write_off(self):
        """Удалили ошибочный приход после списания — снимается только приход."""
        self._receipt("500", "0.1")  # 1500
        self._write_off("200")       # 1300
        second = Receipt.objects.order_by("-id").first()
        self.client.delete(f"/api/inventory/receipts/{second.id}/")
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("800"))
        self.assertTrue(self.milk.movements.filter(kind="writeoff").exists())

    def test_write_off_does_not_change_recipe_cost(self):
        """Себестоимость в тех карте — по цене закупки, списания на неё не влияют."""
        menu_cat = Category.objects.create(name="Кофе")
        latte = ProductVariant.objects.create(
            product=Product.objects.create(category=menu_cat, name="Латте"),
            price=Decimal("300"),
        )
        RecipeItem.objects.create(variant=latte, item=self.milk, quantity=Decimal("200"))
        before = self.client.get(f"/api/inventory/recipes/{latte.id}/").data["cost"]
        self._write_off("500")
        after = self.client.get(f"/api/inventory/recipes/{latte.id}/").data["cost"]
        self.assertEqual(before, after)
        self.assertEqual(Decimal(after), Decimal("20.00"))

    def test_kitchen_sale_after_write_off_into_minus(self):
        """Списали всё, кухня продаёт дальше — продажа не блокируется,
        остаток уходит в минус и честно показывает недостачу."""
        menu_cat = Category.objects.create(name="Кофе")
        latte = ProductVariant.objects.create(
            product=Product.objects.create(category=menu_cat, name="Латте"),
            price=Decimal("300"),
        )
        RecipeItem.objects.create(variant=latte, item=self.milk, quantity=Decimal("200"))
        self._write_off("1000")  # 0
        order = Order.objects.create(table="1", total=Decimal("300"))
        oi = OrderItem.objects.create(
            order=order, variant=latte, quantity=1, unit_price=Decimal("300")
        )
        short = write_off_order_item(oi)
        self.assertEqual([i.id for i in short], [self.milk.id])
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("-200"))
        # отмена позиции возвращает только проданное, списание остаётся
        return_order_item(oi)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("0"))

    def test_quantity_equals_journal_after_mixed_operations(self):
        """После прихода, продажи, списаний, удаления и пачки остаток сходится
        с суммой журнала движений — ничего не потерялось и не задвоилось."""
        self._write_off("100")
        wo = self._write_off("50")
        self.client.post(
            "/api/inventory/write-offs/batch/",
            {"reason": "Персоналу", "positions": [
                {"title": "Раф", "items": [{"item": self.milk.id, "quantity": "120"}]},
                {"title": "Латте", "items": [{"item": self.milk.id, "quantity": "80.5"}]},
            ]},
            format="json",
        )
        self.client.delete(f"/api/inventory/write-offs/{wo['id']}/")
        self._receipt("300", "0.12")
        self.milk.refresh_from_db()
        journal = sum(self.milk.movements.values_list("delta", flat=True), Decimal("0"))
        self.assertEqual(self.milk.quantity, journal)
        self.assertEqual(self.milk.quantity, Decimal("999.5"))  # 1000−100−120−80,5+300

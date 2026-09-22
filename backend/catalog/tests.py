import os
import tempfile
from decimal import Decimal
from io import BytesIO
from unittest import mock

from PIL import Image
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.images import GeneratedImage
from core.llm import LLMError
from orders.models import Order, OrderItem
from users.models import User

from .image_ai import build_prompt
from .models import (
    Category,
    DishwareSample,
    ImageGeneration,
    ImageQuota,
    Modifier,
    ModifierGroup,
    Product,
    ProductVariant,
)
from .services import image_quota


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

class ModifierGroupApiTests(CatalogAdminBase):
    """Редактор наборов опций: опции и их действия правятся одной формой."""

    def _create(self, **over):
        body = {
            "name": "Молоко", "min_choices": 1, "max_choices": 1,
            "products": [self.latte.id],
            "modifiers": [
                {"name": "Коровье", "price_delta": "0"},
                {"name": "Овсяное", "price_delta": "60"},
            ],
            **over,
        }
        return self.client.post("/api/modifier-groups/", body, format="json")

    def setUp(self):
        super().setUp()
        from inventory.models import StockCategory, StockItem

        cat = StockCategory.objects.create(name="Бар")
        self.milk = StockItem.objects.create(category=cat, name="Молоко", unit="ml")
        self.oat = StockItem.objects.create(category=cat, name="Овсяное", unit="ml")

    def test_admin_creates_group_with_options(self):
        self.auth(self.admin)
        res = self._create()
        self.assertEqual(res.status_code, 201)
        self.assertEqual([m["name"] for m in res.data["modifiers"]], ["Коровье", "Овсяное"])
        self.assertTrue(res.data["is_required"])
        self.assertEqual(res.data["products"], [self.latte.id])

    def test_effects_are_saved_with_the_option(self):
        self.auth(self.admin)
        res = self.client.post(
            "/api/modifier-groups/",
            {"name": "Молоко", "max_choices": 1, "products": [self.latte.id],
             "modifiers": [{"name": "Овсяное", "price_delta": "60", "effects": [
                 {"kind": "swap", "item": self.milk.id, "replacement": self.oat.id}
             ]}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        effect = res.data["modifiers"][0]["effects"][0]
        self.assertEqual(effect["kind"], "swap")
        self.assertEqual(effect["replacement"], self.oat.id)

    def test_add_without_quantity_is_refused(self):
        """Иначе опция «+ шот» молча не списала бы ничего."""
        self.auth(self.admin)
        res = self.client.post(
            "/api/modifier-groups/",
            {"name": "Добавки", "products": [self.latte.id],
             "modifiers": [{"name": "+ шот", "effects": [
                 {"kind": "add", "item": self.milk.id}
             ]}]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_swap_without_replacement_is_refused(self):
        self.auth(self.admin)
        res = self.client.post(
            "/api/modifier-groups/",
            {"name": "Молоко", "products": [self.latte.id],
             "modifiers": [{"name": "Овсяное", "effects": [
                 {"kind": "swap", "item": self.milk.id}
             ]}]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_sold_option_is_hidden_not_deleted(self):
        """Опция из чека не удаляется: по ней считается списание."""
        from orders.models import Order, OrderItem, OrderItemModifier

        self.auth(self.admin)
        group_id = self._create().data["id"]
        modifier_id = ModifierGroup.objects.get(id=group_id).modifiers.first().id
        order = Order.objects.create(status=Order.Status.OPEN, table="5")
        item = OrderItem.objects.create(
            order=order, variant=self.latte_v, quantity=1, unit_price=Decimal("240")
        )
        OrderItemModifier.objects.create(
            order_item=item, modifier_id=modifier_id, name="Коровье",
            price_delta=Decimal("0"),
        )
        res = self.client.patch(
            f"/api/modifier-groups/{group_id}/",
            {"modifiers": [{"name": "Овсяное", "price_delta": "60"}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        kept = Modifier.objects.get(id=modifier_id)
        self.assertTrue(kept.is_stopped)  # спрятана, чек цел

    def test_waiter_cannot_edit_groups(self):
        self.auth(self.waiter)
        self.assertEqual(self._create().status_code, 403)

    def test_guest_sees_groups_in_the_menu_only(self):
        self.auth(self.admin)
        self._create()
        self.client.credentials()
        card = [p for p in self.client.get("/api/products/").data
                if p["id"] == self.latte.id][0]
        self.assertEqual(card["modifier_groups"][0]["name"], "Молоко")



def _png_bytes(color="blue"):
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IMAGE_GEN_ASYNC=False)
class MenuImagePromptTests(CatalogAdminBase):
    """Запрос к модели: в нём должна быть наша посуда и запрет на надписи."""

    def test_prompt_mentions_dishware_and_forbids_text(self):
        sample = DishwareSample.objects.create(
            name="Стакан 0,4", note="прозрачный, с крышкой", image=image_file("cup.png")
        )
        self.latte.description = "Эспрессо и молоко"
        prompt = build_prompt(
            self.latte, [sample], style="wood", extra="наши зелёные салфетки"
        )
        self.assertIn("Латте", prompt)
        self.assertIn("Эспрессо и молоко", prompt)
        self.assertIn("Стакан 0,4", prompt)
        self.assertIn("прозрачный, с крышкой", prompt)
        self.assertIn("деревянном столе", prompt)
        self.assertIn("наши зелёные салфетки", prompt)
        self.assertIn("посторонних надписей", prompt)
        # то, что напечатано на нашем стакане, стирать не просим
        self.assertIn("оставь как есть", prompt)
        # но и дорисовывать печать, которой нет, — тоже
        self.assertIn("Не придумывай", prompt)
        self.assertIn("остаётся чистой", prompt)

    def test_prompt_says_nothing_about_material_itself(self):
        """Про материал промпт молчит — это дело подписи к образцу.

        «Стакан непрозрачный» — правда для бумажного и враньё для
        стеклянного. Общий промпт этого знать не может, поэтому в нём
        только правило, верное для любой посуды.
        """
        prompt = build_prompt(self.latte, [])
        self.assertIn("сквозь непрозрачные стенки", prompt)
        self.assertIn("сквозь прозрачные", prompt)
        self.assertNotIn("стакан непрозрачный", prompt.lower())

    def test_sample_note_travels_with_its_dishware(self):
        """Материал сказан один раз у образца и попадает в каждый запрос."""
        glass = DishwareSample.objects.create(
            name="Айриш 0,4", note="толстое прозрачное стекло",
            image=image_file("glass.png"),
        )
        prompt = build_prompt(self.latte, [glass])
        self.assertIn("толстое прозрачное стекло", prompt)

    def test_prompt_without_dishware_stays_neutral(self):
        prompt = build_prompt(self.latte, [])
        self.assertIn("нейтральная", prompt)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IMAGE_GEN_ASYNC=False)
class MenuImageBatchTests(CatalogAdminBase):
    """Пачка генераций: кто может её запустить, сколько стоит и что рисует."""

    def setUp(self):
        super().setUp()
        self.raf = Product.objects.create(category=self.cat, name="Раф")
        ProductVariant.objects.create(product=self.raf, price=Decimal("320"))
        self.cup = DishwareSample.objects.create(
            name="Стакан 0,4", image=image_file("cup.png"), category=self.cat
        )

    def _fake(self, color="blue", cost=0.019):
        """Подмена модели: рисовать по-настоящему в тестах нечем и не за что."""
        return mock.patch(
            "catalog.image_ai.generate_image",
            return_value=(GeneratedImage(data=_png_bytes(color), mime="image/png"), cost),
        )

    def test_waiter_cannot_generate(self):
        self.auth(self.waiter)
        res = self.client.post(
            "/api/image-batches/", {"products": [self.latte.id]}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_category_batch_draws_dishes_without_photo(self):
        self.latte.image.save("own.png", ContentFile(_png_bytes("red")), save=True)
        self.auth(self.admin)
        with self._fake(), self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/image-batches/",
                {"category": self.cat.id, "only_without_photo": True},
                format="json",
            )
        self.assertEqual(res.status_code, 201)
        # у латте фото уже есть — рисуем только раф. В ответе картинка ещё
        # рисуется: фронт поллит пачку, пока pending не станет нулём.
        self.assertEqual(
            res.data["counts"],
            {"total": 1, "pending": 1, "ready": 0, "failed": 0},
        )
        generation = ImageGeneration.objects.get()
        self.assertEqual(generation.status, ImageGeneration.Status.READY)
        self.assertEqual(generation.product_id, self.raf.id)
        self.assertTrue(generation.image)
        # посуда категории подставилась сама, отдельно выбирать не пришлось
        self.assertEqual(list(generation.dishware.all()), [self.cup])
        # счётчик и деньги
        self.assertEqual(image_quota()[0], 1)
        self.assertEqual(generation.cost_usd, Decimal("0.0190"))

    def test_variants_multiply_the_bill(self):
        self.auth(self.admin)
        with self._fake(), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                "/api/image-batches/",
                {"products": [self.latte.id, self.raf.id], "variants": 2},
                format="json",
            )
        self.assertEqual(ImageGeneration.objects.count(), 4)
        self.assertEqual(image_quota()[0], 4)

    def test_batch_over_the_monthly_limit_is_refused(self):
        ImageQuota.objects.create(
            month=timezone.localdate().replace(day=1),
            used=ImageQuota.MONTHLY_LIMIT - 1,
        )
        self.auth(self.admin)
        with self._fake():
            res = self.client.post(
                "/api/image-batches/",
                {"products": [self.latte.id, self.raf.id]},
                format="json",
            )
        self.assertEqual(res.status_code, 402)
        # ни одной задачи не поставлено: отказ до обращения к модели
        self.assertEqual(ImageGeneration.objects.count(), 0)

    def test_failed_generation_keeps_the_reason(self):
        self.auth(self.admin)
        broken = mock.patch(
            "catalog.image_ai.generate_image", side_effect=LLMError("туннель лёг")
        )
        with broken, self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                "/api/image-batches/", {"products": [self.latte.id]}, format="json"
            )
        generation = ImageGeneration.objects.get()
        self.assertEqual(generation.status, ImageGeneration.Status.FAILED)
        self.assertIn("туннель лёг", generation.error)
        # неудача всё равно списана: обращение к модели оплачено нами
        self.assertEqual(image_quota()[0], 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IMAGE_GEN_ASYNC=False)
class MenuImageApplyTests(CatalogAdminBase):
    """Сгенерированное попадает в меню только руками владельца."""

    def setUp(self):
        super().setUp()
        self.generation = ImageGeneration.objects.create(
            product=self.latte, prompt="фото латте", status=ImageGeneration.Status.READY
        )
        self.generation.image.save(
            "gen.png", ContentFile(_png_bytes("green")), save=True
        )

    def test_apply_copies_photo_into_the_menu(self):
        self.auth(self.admin)
        res = self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.assertEqual(res.status_code, 200)
        self.latte.refresh_from_db()
        self.assertTrue(self.latte.image)
        self.assertTrue(self.latte.thumbnail)  # превью собралось само
        self.assertTrue(self.latte.image_is_generated)

    def test_menu_photo_survives_deleting_the_draft(self):
        """Файл копируется, а не переиспользуется: уборка в галерее не должна
        оставлять меню без картинки."""
        self.auth(self.admin)
        self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.latte.refresh_from_db()
        path = self.latte.image.path
        self.client.delete(f"/api/image-generations/{self.generation.id}/")
        self.assertTrue(os.path.exists(path))

    def test_own_photo_removes_the_illustration_mark(self):
        self.auth(self.admin)
        self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.client.patch(
            f"/api/products/{self.latte.id}/", {"image": image_file()}, format="multipart"
        )
        self.latte.refresh_from_db()
        self.assertFalse(self.latte.image_is_generated)

    def test_pending_generation_cannot_be_applied(self):
        self.generation.status = ImageGeneration.Status.PENDING
        self.generation.save(update_fields=["status"])
        self.auth(self.admin)
        res = self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.assertEqual(res.status_code, 400)

    def test_only_one_photo_is_marked_as_current(self):
        second = ImageGeneration.objects.create(
            product=self.latte, prompt="ещё раз", status=ImageGeneration.Status.READY
        )
        second.image.save("gen2.png", ContentFile(_png_bytes("yellow")), save=True)
        self.auth(self.admin)
        self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.client.post(f"/api/image-generations/{second.id}/apply/")
        self.generation.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.generation.applied_at)
        self.assertIsNotNone(second.applied_at)

    def test_guest_sees_that_the_photo_is_drawn(self):
        self.auth(self.admin)
        self.client.post(f"/api/image-generations/{self.generation.id}/apply/")
        self.client.credentials()
        res = self.client.get("/api/products/")
        card = next(p for p in res.data if p["id"] == self.latte.id)
        self.assertTrue(card["image_is_generated"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IMAGE_GEN_ASYNC=False)
class DishwareUploadTests(CatalogAdminBase):
    """Загрузка образца посуды. Форма едет multipart-ом — с файлом иначе никак."""

    def test_uploaded_sample_is_in_use(self):
        """Образец без галочки «используется» всё равно используется.

        DRF считает multipart html-формой, а в html-форме отсутствующая
        галочка значит «снята». Из-за этого загруженный стакан ложился в
        базу неактивным: в студии не показывался, к генерациям не цеплялся,
        и вся затея — «нарисуй в НАШЕЙ посуде» — тихо превращалась в
        стоковую картинку.
        """
        self.auth(self.admin)
        res = self.client.post(
            "/api/dishware/",
            {"name": "Стакан 0,4", "image": image_file("cup.png")},
            format="multipart",
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(DishwareSample.objects.get().is_active)

    def test_sample_can_still_be_retired(self):
        """Снять с работы по-прежнему можно — явным False."""
        self.auth(self.admin)
        sample = DishwareSample.objects.create(
            name="Старая кружка", image=image_file("mug.png")
        )
        res = self.client.patch(
            f"/api/dishware/{sample.id}/", {"is_active": False}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        sample.refresh_from_db()
        self.assertFalse(sample.is_active)

    def test_batch_uses_the_uploaded_sample_by_default(self):
        """Владелец загрузил стакан и ничего не выбирал — стакан всё равно в деле."""
        self.auth(self.admin)
        self.client.post(
            "/api/dishware/",
            {"name": "Стакан 0,4", "image": image_file("cup.png")},
            format="multipart",
        )
        with mock.patch(
            "catalog.image_ai.generate_image",
            return_value=(GeneratedImage(data=_png_bytes(), mime="image/png"), 0.019),
        ), self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/image-batches/", {"products": [self.latte.id]}, format="json"
            )
        self.assertEqual(res.status_code, 201)
        generation = ImageGeneration.objects.get()
        self.assertEqual(
            list(generation.dishware.values_list("name", flat=True)), ["Стакан 0,4"]
        )
        self.assertIn("Стакан 0,4", generation.prompt)

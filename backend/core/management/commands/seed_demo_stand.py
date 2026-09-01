"""Наполнить демо-стенд данными выдуманного кафе.

Стенд — витрина продукта: по нему ходят потенциальные клиенты, поэтому
он должен выглядеть работающим заведением, а не пустой базой. Особенно
важен склад с тех. картами и ценами закупа: без них «Финансы» покажут
прибыль с пометкой «оценка сверху», а это самый сильный экран продукта.

Команда СТИРАЕТ доменные данные, поэтому работает только там, где явно
включён DEMO_STAND=1 — на боевой установке она откажется запускаться.

    python manage.py seed_demo_stand
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog.models import Category, Product
from core.models import SiteSettings
from finance.models import Expense, ExpenseCategory, PayrollPayout
from inventory.models import (
    Receipt,
    ReceiptItem,
    RecipeItem,
    StockCategory,
    StockItem,
    StockMovement,
)
from orders.models import Order, OrderItem, Table
from payments.models import Payment
from wallet.models import TokenPackage, TokenTransaction, Wallet
from shifts.models import Shift, ShiftMember, ShiftSettings
from users.models import User

PASSWORD = "demo12345"
DAYS = 21  # столько дней истории показываем в отчётах

# Доступы показываем прямо на стенде — человек должен зайти каждой ролью
# и увидеть, что экраны разные. Это главный аргумент продукта.
STAFF = [
    ("demo_admin", User.Role.ADMIN, "Сергей", "владелец"),
    ("demo_manager", User.Role.WAREHOUSE, "Ирина", "менеджер"),
    ("demo_waiter", User.Role.WAITER, "Аня", "официант"),
    ("demo_waiter2", User.Role.WAITER, "Максим", "официант"),
    ("demo_cook", User.Role.COOK, "Пётр", "повар"),
    ("demo_bar", User.Role.BAR, "Лена", "бармен"),
]

# (название, единица, цена закупа за базовую единицу, остаток)
STOCK = [
    ("Кофе в зёрнах", "g", "1.90", 8000),
    ("Молоко", "ml", "0.09", 24000),
    ("Сироп карамель", "ml", "0.75", 2000),
    ("Апельсин", "g", "0.14", 9000),
    ("Авокадо", "pcs", "95.00", 40),
    ("Яйцо", "pcs", "12.00", 200),
    ("Хлеб тостовый", "g", "0.22", 4000),
    ("Краб", "g", "1.85", 2200),
    ("Креветки", "g", "1.35", 2000),
    ("Куриное филе", "g", "0.42", 6000),
    ("Рис", "g", "0.11", 10000),
    ("Манго", "g", "0.38", 3000),
    ("Клубника", "g", "0.55", 2000),
    ("Кокосовое молоко", "ml", "0.28", 4000),
    ("Семена чиа", "g", "1.10", 800),
    ("Бейгл", "pcs", "38.00", 60),
    ("Паста карри", "g", "0.90", 700),
    ("Мука", "g", "0.06", 12000),
]

# (категория, станция, название, цена, тех. карта, файл фото)
# Фото блюд — снимки заведения, лежат в media/demo_src на стенде.
MENU = [
    ("Кофе", "bar", "Капучино", "220", [("Кофе в зёрнах", 18), ("Молоко", 150)], "cappuccino.jpg"),
    ("Кофе", "bar", "Латте", "240", [("Кофе в зёрнах", 18), ("Молоко", 220)], "latte.jpg"),
    ("Кофе", "bar", "Эспрессо", "150", [("Кофе в зёрнах", 18)], "espresso.jpg"),
    ("Кофе", "bar", "Карамельный раф", "290",
     [("Кофе в зёрнах", 18), ("Молоко", 180), ("Сироп карамель", 20)], "raf.jpg"),
    ("Напитки", "bar", "Апельсиновый фреш", "320", [("Апельсин", 400)], "juice.jpg"),

    ("Завтраки", "kitchen", "Авокадо-тост с яйцом пашот", "420",
     [("Авокадо", 1), ("Яйцо", 1), ("Хлеб тостовый", 80)], "avocado_toast.png"),
    ("Завтраки", "kitchen", "Гавайский завтрак", "480",
     [("Яйцо", 2), ("Хлеб тостовый", 60), ("Авокадо", 1)], "hawaii_breakfast.png"),
    ("Завтраки", "kitchen", "Рисовая каша с манго", "380",
     [("Рис", 90), ("Кокосовое молоко", 150), ("Манго", 70)], "rice_mango.png"),
    ("Завтраки", "kitchen", "Тропические вафли", "390",
     [("Мука", 90), ("Яйцо", 1), ("Манго", 50)], "waffles.png"),
    ("Завтраки", "kitchen", "Чиа-пудинг с манго", "350",
     [("Семена чиа", 40), ("Кокосовое молоко", 160), ("Манго", 60)], "chia.png"),

    ("Боулы", "kitchen", "Боул с крабом", "640",
     [("Краб", 90), ("Рис", 150), ("Авокадо", 1)], "bowl_crab.png"),
    ("Боулы", "kitchen", "Боул с курицей", "540",
     [("Куриное филе", 120), ("Рис", 150), ("Авокадо", 1)], "bowl_chicken.jpg"),
    ("Боулы", "kitchen", "Боул с креветкой", "590",
     [("Креветки", 90), ("Рис", 150), ("Авокадо", 1)], "bowl_shrimp.jpg"),
    ("Боулы", "kitchen", "Смузи-боул клубника", "420",
     [("Клубника", 120), ("Кокосовое молоко", 100), ("Семена чиа", 15)], "smoothie_bowl.png"),

    ("Кухня", "kitchen", "Бейгл с курицей карри", "430",
     [("Бейгл", 1), ("Куриное филе", 90), ("Паста карри", 15)], "bagel_curry.png"),
    ("Кухня", "kitchen", "Курица карри", "520",
     [("Куриное филе", 160), ("Рис", 120), ("Паста карри", 25)], "chicken_curry.png"),
]

EXPENSES = [("Аренда", "95000"), ("Коммунальные платежи", "11400"), ("Реклама и продвижение", "8000")]


class Command(BaseCommand):
    help = "Наполнить демо-стенд данными выдуманного кафе (только при DEMO_STAND=1)"

    def handle(self, *args, **options):
        if not settings.DEMO_STAND:
            raise CommandError(
                "Команда стирает данные и работает только на демо-стенде. "
                "Установка без DEMO_STAND=1 — выходим."
            )
        random.seed(20260901)  # одинаковый стенд после каждого сброса
        with transaction.atomic():
            self._wipe()
            self._settings()
            staff = self._staff()
            items = self._stock()
            products = self._menu(items)
            self._history(products, staff)
            self._expenses()
        with_photo = Product.objects.exclude(image="").exclude(image__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"Демо-стенд готов: {Product.objects.count()} блюд ({with_photo} с фото), "
            f"{Order.objects.count()} заказов, {Shift.objects.count()} смен. "
            f"Вход: demo_manager / {PASSWORD}"
        ))

    # ——— шаги ———

    def _wipe(self):
        """Полная зачистка: стенд должен выглядеть одинаково после сброса."""
        # Порядок важен: кошельки и платежи держат пользователей и заказы
        # защищёнными ключами, поэтому их сносим первыми.
        Payment.objects.all().delete()
        TokenTransaction.objects.all().delete()
        Wallet.objects.all().delete()
        TokenPackage.objects.all().delete()
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        ShiftMember.objects.all().delete()
        Shift.objects.all().delete()
        PayrollPayout.objects.all().delete()
        Expense.objects.all().delete()
        StockMovement.objects.all().delete()
        ReceiptItem.objects.all().delete()
        Receipt.objects.all().delete()
        RecipeItem.objects.all().delete()
        StockItem.objects.all().delete()
        StockCategory.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()
        Table.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

    def _settings(self):
        site = SiteSettings.load()
        site.name = "Кофейня «Компас»"
        site.app_short_name = "Компас"
        site.tagline = "завтраки, кофе и обеды"
        site.address = "Демо-режим · данные вымышленные"
        site.working_hours = "Пн–Вс · 8:00–22:00"
        site.theme = SiteSettings.Theme.NEUTRAL
        site.accent_color = "#1f58a6"
        logins = "\n".join(
            f"{u} / {PASSWORD} — {title}" for u, _, _, title in STAFF if u != "demo_waiter2"
        )
        site.about = (
            "Демонстрационный стенд «Падачи» — вымышленное кафе с настоящими "
            "данными за три недели. Заходите любой ролью, экраны у всех разные:\n\n"
            f"{logins}\n\n"
            "Меняйте что угодно: это витрина, боевых данных здесь нет."
        )
        site.save()

        for i in range(1, 9):
            Table.objects.create(name=str(i), sort_order=i)
        penalty = Table.objects.create(name="Штраф", sort_order=99)

        cfg = ShiftSettings.load()
        cfg.daily_rate = Decimal("2500")
        cfg.bonus_percent = Decimal("8")
        cfg.penalty_table = penalty
        cfg.save()

    def _staff(self):
        out = {}
        for username, role, first, _ in STAFF:
            # Роль «админ» даёт панель продукта, но НЕ доступ в Django-админку:
            # стенд публичный, суперпользователей на нём быть не должно.
            u = User.objects.create_user(
                username=username, password=PASSWORD, role=role, first_name=first
            )
            out[role] = out.get(role, []) + [u]
        return out

    def _stock(self):
        cat = StockCategory.objects.create(name="Продукты", sort_order=10)
        receipt = Receipt.objects.create(supplier="МЕТРО", comment="Стартовый закуп")
        items = {}
        for name, unit, cost, qty in STOCK:
            it = StockItem.objects.create(
                category=cat, name=name, unit=unit,
                quantity=Decimal(qty), min_quantity=Decimal(qty) / 4,
            )
            # Цена закупа обязательна: без неё не считается себестоимость,
            # и отчёт о прибыли пометит цифру как оценку.
            ReceiptItem.objects.create(
                receipt=receipt, item=it,
                quantity=Decimal(qty), unit_cost=Decimal(cost),
            )
            items[name] = it
        return items

    def _menu(self, items):
        cats, products = {}, []
        for cat_name, station, name, price, recipe, photo in MENU:
            if cat_name not in cats:
                cats[cat_name] = Category.objects.create(
                    name=cat_name, station=station, sort_order=len(cats) + 1
                )
            p = Product.objects.create(
                category=cats[cat_name], name=name, price=Decimal(price),
                prep_minutes=random.choice([5, 8, 10, 12]),
                sort_order=len(products) + 1,
            )
            for item_name, qty in recipe:
                RecipeItem.objects.create(
                    product=p, item=items[item_name], quantity=Decimal(str(qty))
                )
            self._attach_photo(p, photo)
            products.append(p)
        return products

    def _attach_photo(self, product, filename):
        """Прикрепить фото блюда из media/demo_src, если оно там лежит.

        Файлы кладутся на стенд отдельно и в репозиторий не попадают:
        снимки заведения весят десятки мегабайт.
        """
        import os

        from django.core.files import File

        path = os.path.join(settings.MEDIA_ROOT, "demo_src", filename)
        if not os.path.exists(path):
            self.stdout.write(self.style.WARNING(f"нет фото: {filename}"))
            return
        with open(path, "rb") as f:
            # save=True запускает и генерацию превью (Product.make_thumbnail)
            product.image.save(filename, File(f), save=True)

    def _history(self, products, staff):
        """Три недели заказов и смен — чтобы отчёты были не пустыми."""
        today = timezone.localdate()
        cooks = staff[User.Role.COOK] + staff[User.Role.BAR]
        waiters = staff[User.Role.WAITER]
        cfg = ShiftSettings.load()

        for back in range(DAYS, -1, -1):
            day = today - timedelta(days=back)
            shift = Shift.objects.create(
                date=day, daily_rate=cfg.daily_rate,
                bonus_percent=cfg.bonus_percent, penalty_table="Штраф",
            )
            crew = [random.choice(waiters), random.choice(cooks)]
            for u in crew:
                ShiftMember.objects.create(shift=shift, user=u, role=u.role)

            # выходные оживлённее буднего дня
            base = 34 if day.weekday() >= 5 else 22
            for _ in range(base + random.randint(-5, 6)):
                hour = random.choices(
                    [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                    weights=[3, 5, 6, 9, 10, 8, 5, 4, 5, 7, 6, 3],
                )[0]
                closed = timezone.make_aware(
                    timezone.datetime(day.year, day.month, day.day, hour, random.randint(0, 59))
                )
                order = Order.objects.create(
                    table=str(random.randint(1, 8)),
                    status=Order.Status.PAID,
                    pay_method=random.choices(["cash", "card"], weights=[3, 7])[0],
                    closed_at=closed,
                )
                Order.objects.filter(pk=order.pk).update(created_at=closed - timedelta(minutes=35))
                total = Decimal("0")
                for p in random.sample(products, random.randint(1, 3)):
                    qty = random.choices([1, 2], weights=[8, 2])[0]
                    OrderItem.objects.create(
                        order=order, product=p, quantity=qty,
                        unit_price=p.price, status="ready",
                    )
                    total += p.price * qty
                Order.objects.filter(pk=order.pk).update(total=total)

    def _expenses(self):
        """Постоянные расходы за прошлый месяц и за текущий.

        В текущем месяце берём долю по прошедшим дням: иначе первого числа
        отчёт покажет один день выручки против аренды за весь месяц, и
        витрина встретит гостя убытком в сотню тысяч.
        """
        from calendar import monthrange

        today = timezone.localdate()
        this_first = today.replace(day=1)
        prev_last = this_first - timedelta(days=1)
        days_in_month = monthrange(today.year, today.month)[1]
        share = Decimal(today.day) / Decimal(days_in_month)

        for name, amount in EXPENSES:
            cat, _ = ExpenseCategory.objects.get_or_create(name=name)
            full = Decimal(amount)
            # прошлый месяц — полностью
            Expense.objects.create(
                date=prev_last.replace(day=1), category=cat, amount=full
            )
            # текущий — по прошедшим дням, округляя до сотни рублей
            part = (full * share / 100).quantize(Decimal("1")) * 100
            if part > 0:
                Expense.objects.create(date=this_first, category=cat, amount=part)

"""Товары и варианты: плоская номенклатура превращается в «товар → варианты».

Каждая существующая позиция становится товаром с одним безымянным вариантом —
остатки, движения и приходы остаются привязаны к тому же StockItem.

Откат данных описан, но на непустой базе не пройдёт: возврат StockItem.category
(FK NOT NULL без значения по умолчанию) упрётся в Postgres раньше RunPython.
"""
import django.db.models.deletion
from django.db import migrations, models


def items_to_products(apps, schema_editor):
    StockProduct = apps.get_model("inventory", "StockProduct")
    StockItem = apps.get_model("inventory", "StockItem")

    for item in StockItem.objects.all().iterator():
        product = StockProduct.objects.create(
            category_id=item.category_id,
            name=item.name,
            unit=item.unit,
            min_quantity=item.min_quantity,
            is_active=item.is_active,
        )
        # Имя переезжает на товар: вариант остаётся единственным и безымянным.
        StockItem.objects.filter(pk=item.pk).update(product=product, name="")


def products_to_items(apps, schema_editor):
    StockItem = apps.get_model("inventory", "StockItem")

    for item in StockItem.objects.select_related("product").iterator():
        product = item.product
        StockItem.objects.filter(pk=item.pk).update(
            category_id=product.category_id,
            name=f"{product.name} {item.name}".strip(),
            unit=product.unit,
            min_quantity=product.min_quantity,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_receiptscan"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="Название")),
                (
                    "unit",
                    models.CharField(
                        choices=[("g", "г"), ("ml", "мл"), ("pcs", "шт")],
                        default="pcs",
                        max_length=4,
                        verbose_name="Единица",
                    ),
                ),
                (
                    "min_quantity",
                    models.DecimalField(
                        blank=True,
                        decimal_places=3,
                        help_text="Если общий остаток опустится до этого значения — товар подсветится",
                        max_digits=12,
                        null=True,
                        verbose_name="Порог «заканчивается»",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="products",
                        to="inventory.stockcategory",
                        verbose_name="Категория",
                    ),
                ),
            ],
            options={
                "verbose_name": "Товар склада",
                "verbose_name_plural": "Товары склада",
                "ordering": ["category__sort_order", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="stockproduct",
            constraint=models.UniqueConstraint(
                fields=("category", "name"), name="uniq_stockproduct_per_category"
            ),
        ),
        migrations.AddField(
            model_name="stockitem",
            name="product",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="variants",
                to="inventory.stockproduct",
                verbose_name="Товар",
            ),
        ),
        migrations.AlterField(
            model_name="stockitem",
            name="name",
            field=models.CharField(
                blank=True,
                help_text="Чем отличается от других: «замороженные 16/20», «Pepsi 1 л». "
                "Можно оставить пустым, если вариант один",
                max_length=200,
                verbose_name="Вариант",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="stockitem",
            name="uniq_stockitem_per_category",
        ),
        migrations.RunPython(items_to_products, products_to_items),
        migrations.AlterField(
            model_name="stockitem",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="variants",
                to="inventory.stockproduct",
                verbose_name="Товар",
            ),
        ),
        migrations.RemoveField(model_name="stockitem", name="category"),
        migrations.RemoveField(model_name="stockitem", name="unit"),
        migrations.RemoveField(model_name="stockitem", name="min_quantity"),
        migrations.AlterField(
            model_name="stockitem",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="Активен"),
        ),
        migrations.AlterModelOptions(
            name="stockitem",
            options={
                "ordering": ["product__category__sort_order", "product__name", "name"],
                "verbose_name": "Вариант товара",
                "verbose_name_plural": "Варианты товара",
            },
        ),
        migrations.AddConstraint(
            model_name="stockitem",
            constraint=models.UniqueConstraint(
                fields=("product", "name"), name="uniq_stockitem_per_product"
            ),
        ),
        migrations.CreateModel(
            name="StockItemAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, verbose_name="Название в чеке")),
                ("norm", models.CharField(max_length=200, unique=True, verbose_name="Нормализованное")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Когда")),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aliases",
                        to="inventory.stockitem",
                        verbose_name="Вариант",
                    ),
                ),
            ],
            options={
                "verbose_name": "Название в чеке",
                "verbose_name_plural": "Названия в чеках",
                "ordering": ["name"],
            },
        ),
    ]

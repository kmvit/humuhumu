"""Остаток снова один на товар: варианты сливаются в товар, их имена — в алиасы.

Учёт по вариантам оказался лишним: 500 г мелких креветок и 500 г крупных — это
1000 г креветок. Остатки, движения и позиции приходов всех вариантов переезжают
на одну позицию (сам товар), а названия вариантов остаются как названия закупки
(StockItemAlias) — они нужны только для сопоставления чеков.
"""
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def merge_variants(apps, schema_editor):
    StockProduct = apps.get_model("inventory", "StockProduct")
    StockItem = apps.get_model("inventory", "StockItem")
    StockItemAlias = apps.get_model("inventory", "StockItemAlias")
    StockMovement = apps.get_model("inventory", "StockMovement")
    ReceiptItem = apps.get_model("inventory", "ReceiptItem")

    for product in StockProduct.objects.prefetch_related("variants"):
        variants = list(product.variants.order_by("pk"))
        if not variants:
            continue
        main, rest = variants[0], variants[1:]

        total = sum((v.quantity or Decimal("0") for v in variants), Decimal("0"))
        StockItem.objects.filter(pk=main.pk).update(
            category_id=product.category_id,
            name=product.name,
            unit=product.unit,
            min_quantity=product.min_quantity,
            quantity=total,
            is_active=product.is_active,
        )

        taken = set(StockItemAlias.objects.values_list("norm", flat=True))
        for v in [main, *rest]:
            # Имя варианта («мелкие 70/90») теперь просто название закупки.
            if v.name and normalize(v.name) not in taken:
                StockItemAlias.objects.create(
                    item_id=main.pk, name=v.name, norm=normalize(v.name)
                )
                taken.add(normalize(v.name))

        if rest:
            ids = [v.pk for v in rest]
            StockMovement.objects.filter(item_id__in=ids).update(item_id=main.pk)
            ReceiptItem.objects.filter(item_id__in=ids).update(item_id=main.pk)
            StockItemAlias.objects.filter(item_id__in=ids).update(item_id=main.pk)
            StockItem.objects.filter(pk__in=ids).delete()


def split_back(apps, schema_editor):
    """Обратно: товар = один вариант. Разделить слитые остатки уже нельзя."""
    StockProduct = apps.get_model("inventory", "StockProduct")
    StockItem = apps.get_model("inventory", "StockItem")

    for item in StockItem.objects.all():
        product = StockProduct.objects.create(
            category_id=item.category_id,
            name=item.name,
            unit=item.unit,
            min_quantity=item.min_quantity,
            is_active=item.is_active,
        )
        StockItem.objects.filter(pk=item.pk).update(product=product, name="")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_stockproduct"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockitem",
            name="category",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="inventory.stockcategory",
                verbose_name="Категория",
            ),
        ),
        migrations.AddField(
            model_name="stockitem",
            name="unit",
            field=models.CharField(
                choices=[("g", "г"), ("ml", "мл"), ("pcs", "шт")],
                default="pcs",
                max_length=4,
                verbose_name="Единица",
            ),
        ),
        migrations.AddField(
            model_name="stockitem",
            name="min_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Если остаток опустится до этого значения — товар попадёт в закуп",
                max_digits=12,
                null=True,
                verbose_name="Порог «заканчивается»",
            ),
        ),
        migrations.AddField(
            model_name="stockitem",
            name="target_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="До какого остатка закупаем. Пусто — берём два порога",
                max_digits=12,
                null=True,
                verbose_name="Сколько держать",
            ),
        ),
        migrations.RunPython(merge_variants, split_back),
        # Категория заполнена данными выше — можно требовать её обязательной.
        migrations.AlterField(
            model_name="stockitem",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="inventory.stockcategory",
                verbose_name="Категория",
            ),
        ),
    ]

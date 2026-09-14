"""Позиция заказа продаёт вариант, а не товар.

После catalog.0012 у каждого товара ровно один вариант, поэтому перенос
однозначный: variant = единственный вариант товара позиции. Поле product
снимается — товар достаётся через variant.product, и история заказов
переживает любые будущие перестановки вариантов между товарами.
"""
from django.db import migrations, models
import django.db.models.deletion


def fill_variant(apps, schema_editor):
    OrderItem = apps.get_model("orders", "OrderItem")
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    by_product = dict(ProductVariant.objects.values_list("product_id", "id"))
    items = []
    for item in OrderItem.objects.all().iterator():
        item.variant_id = by_product[item.product_id]
        items.append(item)
        if len(items) >= 500:
            OrderItem.objects.bulk_update(items, ["variant"])
            items = []
    if items:
        OrderItem.objects.bulk_update(items, ["variant"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_product_variants"),
        ("orders", "0025_alter_table_name_table_uniq_table_per_org"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="variant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="catalog.productvariant",
                verbose_name="Вариант",
            ),
        ),
        migrations.RunPython(fill_variant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="orderitem",
            name="variant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="catalog.productvariant",
                verbose_name="Вариант",
            ),
        ),
        migrations.RemoveField(model_name="orderitem", name="product"),
    ]

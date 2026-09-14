"""Тех карта принадлежит варианту блюда: у «0,33» и «0,7» состав свой."""
from django.db import migrations, models
import django.db.models.deletion


def fill_variant(apps, schema_editor):
    RecipeItem = apps.get_model("inventory", "RecipeItem")
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    by_product = dict(ProductVariant.objects.values_list("product_id", "id"))
    rows = list(RecipeItem.objects.all())
    for row in rows:
        row.variant_id = by_product[row.product_id]
    RecipeItem.objects.bulk_update(rows, ["variant"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_product_variants"),
        ("inventory", "0010_alter_receiptscan_image"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="recipeitem", name="uniq_recipeitem_per_product"
        ),
        migrations.AddField(
            model_name="recipeitem",
            name="variant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recipe",
                to="catalog.productvariant",
                verbose_name="Вариант блюда",
            ),
        ),
        migrations.RunPython(fill_variant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="recipeitem",
            name="variant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recipe",
                to="catalog.productvariant",
                verbose_name="Вариант блюда",
            ),
        ),
        migrations.RemoveField(model_name="recipeitem", name="product"),
        migrations.AddConstraint(
            model_name="recipeitem",
            constraint=models.UniqueConstraint(
                fields=("variant", "item"), name="uniq_recipeitem_per_variant"
            ),
        ),
        migrations.AlterModelOptions(
            name="recipeitem",
            options={
                "ordering": ["variant__product__name", "item__name"],
                "verbose_name": "Строка тех карты",
                "verbose_name_plural": "Тех карты блюд",
            },
        ),
    ]

"""Вариант товара: цена, вес и стоп переезжают с Product на ProductVariant.

Каждому существующему товару создаётся ровно один безымянный вариант с его
же ценой — витрина после миграции выглядит как раньше. Поля с товара
снимаются в этой же миграции, после переноса значений.
"""
from django.db import migrations, models
import django.db.models.deletion
import django.db.models.manager


def fill_variants(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    ProductVariant.objects.bulk_create(
        ProductVariant(
            product_id=p.id,
            organization_id=p.organization_id,
            label="",
            price=p.price,
            weight_grams=p.weight_grams,
            prep_minutes=p.prep_minutes,
            is_stopped=p.is_stopped,
            sort_order=0,
        )
        # исторические модели без тенант-менеджера — берём всех
        for p in Product.objects.all().iterator()
    )


def unfill_variants(apps, schema_editor):
    """Откат: вернуть цену и стоп на товар из его первого варианта."""
    Product = apps.get_model("catalog", "Product")
    ProductVariant = apps.get_model("catalog", "ProductVariant")
    for p in Product.objects.all().iterator():
        v = (
            ProductVariant.objects.filter(product_id=p.id)
            .order_by("sort_order", "id")
            .first()
        )
        if v is None:
            continue
        p.price = v.price
        p.weight_grams = v.weight_grams
        p.prep_minutes = v.prep_minutes
        p.is_stopped = v.is_stopped
        p.save(
            update_fields=["price", "weight_grams", "prep_minutes", "is_stopped"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_tenancy_fill"),
        ("catalog", "0011_alter_category_icon_alter_product_image_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductVariant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(blank=True, max_length=40, verbose_name="Вариант")),
                ("price", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="Цена, ₽")),
                ("weight_grams", models.PositiveIntegerField(blank=True, null=True, verbose_name="Вес, г")),
                ("prep_minutes", models.PositiveIntegerField(blank=True, null=True, verbose_name="Время приготовления, мин")),
                ("is_stopped", models.BooleanField(default=False, verbose_name="На стопе (временно)")),
                ("is_active", models.BooleanField(default=True, verbose_name="Продаётся")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="Порядок сортировки")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.organization", verbose_name="Заведение")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="variants", to="catalog.product", verbose_name="Товар")),
            ],
            options={
                "verbose_name": "Вариант товара",
                "verbose_name_plural": "Варианты товаров",
                "ordering": ["sort_order", "id"],
            },
            managers=[
                ("objects", django.db.models.manager.Manager()),
                ("all_objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.UniqueConstraint(
                fields=("product", "label"), name="uniq_variant_label_per_product"
            ),
        ),
        migrations.RunPython(fill_variants, unfill_variants),
        migrations.RemoveField(model_name="product", name="price"),
        migrations.RemoveField(model_name="product", name="weight_grams"),
        migrations.RemoveField(model_name="product", name="prep_minutes"),
        migrations.RemoveField(model_name="product", name="is_stopped"),
    ]

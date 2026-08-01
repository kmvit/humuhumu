"""Проставить станцию категориям: «Еда» → кухня, остальное остаётся баром."""
from django.db import migrations


def forward(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    for cat in Category.objects.all():
        name = (cat.name or "").lower()
        if "еда" in name or "food" in name or "кухн" in name:
            cat.station = "kitchen"
        else:
            cat.station = "bar"
        cat.save(update_fields=["station"])


def backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_category_station"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

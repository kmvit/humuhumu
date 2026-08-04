# Добавляем столы 11..15 в реестр (в кафе стало 15 столов).
from django.db import migrations


def seed(apps, schema_editor):
    Table = apps.get_model("orders", "Table")
    for i in range(11, 16):
        Table.objects.get_or_create(name=str(i), defaults={"sort_order": i})


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0015_order_comment"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

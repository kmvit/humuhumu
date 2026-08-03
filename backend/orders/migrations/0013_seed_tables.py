# Засеваем текущие столы 1..10, чтобы они не пропали при переходе на реестр.
from django.db import migrations


def seed(apps, schema_editor):
    Table = apps.get_model("orders", "Table")
    for i in range(1, 11):
        Table.objects.get_or_create(name=str(i), defaults={"sort_order": i})


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0012_table_model"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

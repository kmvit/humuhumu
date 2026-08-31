# Историческая миграция: добавляла столы 11..15 первому кафе.
# Отключена по той же причине, что и 0013 — столы заводит заведение.
from django.db import migrations


def seed(apps, schema_editor):
    pass


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0015_order_comment"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

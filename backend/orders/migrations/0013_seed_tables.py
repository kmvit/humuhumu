# Историческая миграция: когда-то засевала столы 1..10 первому кафе.
# Продукт ставится разным заведениям, поэтому сид отключён — столы заводит
# само заведение. Файл оставлен, чтобы не рвать историю миграций.
from django.db import migrations


def seed(apps, schema_editor):
    pass


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0012_table_model"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

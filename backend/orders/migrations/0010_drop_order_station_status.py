# Убираем order.food_status/drinks_status — теперь это агрегат из статусов позиций.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0009_item_status_and_times"),
    ]

    operations = [
        migrations.RemoveField(model_name="order", name="drinks_status"),
        migrations.RemoveField(model_name="order", name="food_status"),
    ]

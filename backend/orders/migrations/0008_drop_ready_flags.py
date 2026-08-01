# Удаляем старые булевы *_ready (данные уже перенесены в *_status в 0007).
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0007_station_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='order',
            name='drinks_ready',
        ),
        migrations.RemoveField(
            model_name='order',
            name='food_ready',
        ),
    ]

# Канбан-статус станций вместо булевых *_ready.
from django.db import migrations, models


def copy_ready(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    # что было готово — переносим в статус ready, остальное остаётся new
    Order.objects.filter(food_ready=True).update(food_status="ready")
    Order.objects.filter(drinks_ready=True).update(drinks_status="ready")


def backward(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(food_status="ready").update(food_ready=True)
    Order.objects.filter(drinks_status="ready").update(drinks_ready=True)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_remap_open_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='drinks_status',
            field=models.CharField(choices=[('new', 'Новый'), ('in_progress', 'В процессе'), ('ready', 'Готов')], default='new', max_length=12, verbose_name='Напитки'),
        ),
        migrations.AddField(
            model_name='order',
            name='food_status',
            field=models.CharField(choices=[('new', 'Новый'), ('in_progress', 'В процессе'), ('ready', 'Готов')], default='new', max_length=12, verbose_name='Еда'),
        ),
        migrations.RunPython(copy_ready, backward),
    ]

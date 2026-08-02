# Статус на уровне позиции + таймстемпы этапов. Старые order.*_status копируем в позиции.
from django.db import migrations, models

STATION_CHOICES = [("new", "Новый"), ("in_progress", "В процессе"), ("ready", "Готов")]


def copy_status_to_items(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.all():
        for item in order.items.select_related("product__category").all():
            station = item.product.category.station
            item.status = order.food_status if station == "kitchen" else order.drinks_status
            item.save(update_fields=["status"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0008_drop_ready_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="status",
            field=models.CharField(choices=STATION_CHOICES, default="new", max_length=12, verbose_name="Статус"),
        ),
        migrations.AddField(
            model_name="order",
            name="food_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Кухня взяла"),
        ),
        migrations.AddField(
            model_name="order",
            name="food_ready_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Кухня готова"),
        ),
        migrations.AddField(
            model_name="order",
            name="drinks_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Бар взял"),
        ),
        migrations.AddField(
            model_name="order",
            name="drinks_ready_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Бар готов"),
        ),
        migrations.AddField(
            model_name="order",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Закрыт"),
        ),
        migrations.RunPython(copy_status_to_items, noop),
    ]

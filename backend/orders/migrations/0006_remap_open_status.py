"""Старые статусы preparing/ready → open (в новой схеме заказ «Открыт», пока не закрыт)."""
from django.db import migrations


def forward(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(status__in=["preparing", "ready"]).update(status="open")


def backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_remove_order_cashier_order_closed_by_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

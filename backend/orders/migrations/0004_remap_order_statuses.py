"""Ремап старых статусов на новую схему официант→кухня→касса.

pending (ожидал оплаты) и preparing → «На кухне» (preparing)
done (выдан) → «Оплачен» (paid)
Старый способ оплаты tokens → cash.
"""
from django.db import migrations


def forward(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(status="pending").update(status="preparing")
    Order.objects.filter(status="done").update(status="paid")
    Order.objects.filter(pay_method="tokens").update(pay_method="cash")


def backward(apps, schema_editor):
    # необратимо: старые статусы pending/done восстановить нельзя
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_order_table_order_waiter_alter_order_cashier_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

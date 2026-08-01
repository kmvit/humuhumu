"""Роль cashier упразднена — переносим такие аккаунты в роль bar."""
from django.db import migrations


def forward(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(role="cashier").update(role="bar")


def backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

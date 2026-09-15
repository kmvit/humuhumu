"""Подписки переезжают на два тарифа-формата — см. core/0027.

Тариф подписки и формат заведения теперь одно и то же, поэтому берём
формат точки: он записан в её настройках и отражает, как она работает.
"""
from django.db import migrations, models

BACK = {"counter": "start", "hall": "max"}


def plan_by_mode(apps, schema_editor):
    Subscription = apps.get_model("billing", "Subscription")
    SiteSettings = apps.get_model("core", "SiteSettings")

    modes = dict(SiteSettings.objects.values_list("organization_id", "service_mode"))
    for sub in Subscription.objects.all():
        plan = "counter" if modes.get(sub.organization_id) == "counter" else "hall"
        if sub.plan != plan:
            sub.plan = plan
            sub.save(update_fields=["plan"])


def plan_back(apps, schema_editor):
    Subscription = apps.get_model("billing", "Subscription")
    for sub in Subscription.objects.all():
        old = BACK.get(sub.plan)
        if old:
            sub.plan = old
            sub.save(update_fields=["plan"])


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        # после переноса настроек: оттуда и берём формат
        ('core', '0027_alter_sitesettings_plan_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscription',
            name='plan',
            field=models.CharField(choices=[('counter', 'Стойка'), ('hall', 'Зал')], default='counter', max_length=8, verbose_name='Тариф'),
        ),
        migrations.RunPython(plan_by_mode, plan_back),
    ]

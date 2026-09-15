"""Тарифная сетка из трёх функциональных ступеней стала двумя форматными.

Тариф теперь означает формат заведения, а не набор функций, поэтому и
переносим по формату: как точка на самом деле работает, так и называется
её тариф. Переводить по старому названию было бы неверно — «Максимум» брали
и кофейни без зала, и кафе с залом.
"""
from django.db import migrations, models

#: Формат обслуживания → новый тариф.
BY_MODE = {"counter": "counter", "hall": "hall"}

#: Обратно: точного соответствия нет, восстанавливаем ближайшее по смыслу —
#: «Стойка» это бывший «Старт», «Зал» — бывший «Максимум» (его брали все).
BACK = {"counter": "start", "hall": "max"}


def plan_by_mode(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    LicenseState = apps.get_model("core", "LicenseState")

    modes = {}
    for site in SiteSettings.objects.all():
        plan = BY_MODE.get(site.service_mode, "hall")
        modes[site.organization_id] = plan
        if site.plan != plan:
            site.plan = plan
            site.save(update_fields=["plan"])

    # Кэш лицензии: следующая сверка перезапишет его сама, но до неё
    # владелец видел бы в панели тариф, которого больше не существует.
    for state in LicenseState.objects.exclude(plan=""):
        plan = modes.get(state.organization_id, "hall")
        if state.plan != plan:
            state.plan = plan
            state.save(update_fields=["plan"])


def plan_back(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    LicenseState = apps.get_model("core", "LicenseState")
    for model in (SiteSettings, LicenseState):
        for row in model.objects.all():
            old = BACK.get(row.plan)
            if old:
                row.plan = old
                row.save(update_fields=["plan"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_alter_sitesettings_logo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='plan',
            field=models.CharField(choices=[('counter', 'Стойка'), ('hall', 'Зал')], default='counter', help_text='«Стойка» — точка без зала; «Зал» — с посадкой и официантами. Функционал полный на обоих, кроме экранов кухни и бара.', max_length=8, verbose_name='Тариф'),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='service_mode',
            field=models.CharField(choices=[('hall', 'Зал с официантами'), ('counter', 'Стойка / окно выдачи')], default='hall', help_text='«Стойка» — для точек без зала: гость заказывает по QR, забирает по номеру. Столов и официанта нет, заказ сразу уходит в работу. Меняется вместе с тарифом.', max_length=16, verbose_name='Формат обслуживания'),
        ),
        migrations.RunPython(plan_by_mode, plan_back),
    ]

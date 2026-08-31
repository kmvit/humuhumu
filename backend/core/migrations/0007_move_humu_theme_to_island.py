# Тема «хуму» называлась по имени первого кафе — в продукте она стала
# «Островной». Переводим существующую установку на новый ключ, чтобы
# оформление не сбросилось на нейтральное.
from django.db import migrations


def rename(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(theme="humu").update(theme="island")


def back(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(theme="island").update(theme="humu")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_rename_humu_theme"),
    ]

    operations = [
        migrations.RunPython(rename, back),
    ]

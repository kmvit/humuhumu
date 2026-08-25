# Существующая установка уже живёт с авторским оформлением «хуму» —
# фиксируем его явно, чтобы деплой не сменил вид кафе без спроса.
# Новые установки получают дефолт «neutral» из поля.
from django.db import migrations


def keep_humu(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(pk=1).update(theme="humu")


def revert(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(pk=1).update(theme="neutral")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_sitesettings_accent_color_sitesettings_theme"),
    ]

    operations = [migrations.RunPython(keep_humu, revert)]

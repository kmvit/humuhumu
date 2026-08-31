# У первого заведения длинное название («хумухумунукунукуапуа») — под иконкой
# на телефоне оно обрезалось бы в бессмыслицу. Ставим то короткое имя,
# под которым приложение уже установлено у гостей и персонала.
from django.db import migrations


def seed(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(pk=1, app_short_name="").update(app_short_name="ХУМУ")


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_app_short_name"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

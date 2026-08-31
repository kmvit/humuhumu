# Реквизиты первого заведения переезжают из кода фронта (legal.ts) в БД.
# Заполняем только существующую установку — у неё эти данные уже
# показывались в оферте. Новое заведение получает пустые поля и заполняет
# их в админке под себя.
from django.db import migrations

EXISTING = {
    "merchant_type": "Индивидуальный предприниматель",
    "merchant_name": "Индивидуальный предприниматель Говоров Вячеслав Юрьевич",
    "merchant_short": "ИП Говоров В. Ю.",
    "merchant_address": (
        "357538, Россия, Ставропольский край, г. Пятигорск, "
        "тер. СНТ Ивушка (массив 5), д. 65"
    ),
    "merchant_inn": "263209482531",
    "merchant_ogrn": "322265100058396",
    "merchant_account": "40802810100003342448",
    "merchant_bank": "АО «ТБанк»",
    "merchant_bank_inn": "7710140679",
    "merchant_bik": "044525974",
    "merchant_corr_account": "30101810145250000974",
    "merchant_bank_address": "127287, г. Москва, ул. Хуторская 2-я, д. 38А, стр. 26",
    "acquirer": "АО «ТБанк» (Т-Касса)",
    "legal_updated": "3 августа 2026 г.",
}


def seed(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    # Только для уже живущей установки: если записи нет, это новое
    # заведение — ему чужие реквизиты не нужны.
    SiteSettings.objects.filter(pk=1, merchant_inn="").update(**EXISTING)


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_merchant_details"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

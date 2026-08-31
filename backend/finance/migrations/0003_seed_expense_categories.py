# Стартовый набор статей расходов — универсальный, не привязан к заведению.
# Это шаблон: кафе переименовывает, удаляет и добавляет свои в админке.
from django.db import migrations

STARTER = [
    "Аренда",
    "Коммунальные платежи",
    "Налоги и взносы",
    "Реклама и продвижение",
    "Хозтовары и расходники",
    "Ремонт и обслуживание",
    "Прочее",
]


def seed(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    for i, name in enumerate(STARTER, start=1):
        ExpenseCategory.objects.get_or_create(
            name=name, defaults={"sort_order": i * 10}
        )


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_expenses"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

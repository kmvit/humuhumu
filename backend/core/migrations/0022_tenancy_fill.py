"""Привязать существующие данные к заведению.

Ставится ПОСЛЕ добавления поля organization во всех приложениях (отсюда
длинный список зависимостей) и ДО того, как поле станет обязательным.

На работающей установке заведение уже есть — просто оно нигде не
записано: берём название из настроек сайта. На чистой базе создаём
запись-заготовку, её переименует владелец при настройке.
"""
from django.db import migrations
from django.utils.text import slugify

#: Все модели, у которых появилось поле organization.
TENANT_MODELS = [
    ("core", "SiteSettings"),
    ("core", "LicenseState"),
    ("users", "User"),
    ("catalog", "Category"),
    ("catalog", "Product"),
    ("catalog", "ProductLike"),
    ("orders", "Table"),
    ("orders", "Order"),
    ("orders", "OrderItem"),
    ("payments", "Payment"),
    ("inventory", "StockCategory"),
    ("inventory", "StockItem"),
    ("inventory", "StockItemAlias"),
    ("inventory", "Receipt"),
    ("inventory", "ReceiptItem"),
    ("inventory", "ReceiptScan"),
    ("inventory", "StockMovement"),
    ("inventory", "RecipeItem"),
    ("inventory", "PurchaseList"),
    ("inventory", "PurchaseLine"),
    ("shifts", "ShiftSettings"),
    ("shifts", "Shift"),
    ("shifts", "ShiftMember"),
    ("finance", "PayrollPayout"),
    ("finance", "ExpenseCategory"),
    ("finance", "Expense"),
    ("wallet", "Wallet"),
    ("wallet", "TokenTransaction"),
    ("wallet", "TokenPackage"),
    ("loyalty", "LoyaltyMember"),
    ("loyalty", "BonusTransaction"),
]


def create_and_fill(apps, schema_editor):
    Organization = apps.get_model("core", "Organization")
    org = Organization.objects.order_by("pk").first()
    if org is None:
        SiteSettings = apps.get_model("core", "SiteSettings")
        site = SiteSettings.objects.first()
        name = (site.name if site and site.name else "Заведение").strip()
        # slugify не берёт кириллицу — тогда просто «cafe».
        slug = slugify(name, allow_unicode=False) or "cafe"
        org = Organization.objects.create(name=name, slug=slug[:60])

    for app_label, model_name in TENANT_MODELS:
        model = apps.get_model(app_label, model_name)
        model.objects.filter(organization__isnull=True).update(organization=org)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_organization_licensestate_organization_and_more"),
        ("users", "0006_user_organization"),
        ("catalog", "0008_alter_category_managers_alter_product_managers_and_more"),
        ("orders", "0023_alter_order_managers_alter_orderitem_managers_and_more"),
        ("payments", "0005_alter_payment_managers_payment_organization"),
        ("inventory", "0006_alter_purchaseline_managers_and_more"),
        ("shifts", "0003_alter_shift_managers_alter_shiftmember_managers_and_more"),
        ("finance", "0004_alter_expense_managers_and_more"),
        ("wallet", "0002_alter_tokenpackage_managers_and_more"),
        ("loyalty", "0002_alter_bonustransaction_managers_and_more"),
    ]

    operations = [
        # Обратной операции нет намеренно: откат оставит поле как есть,
        # а следующий прогон просто заполнит пустые снова.
        migrations.RunPython(create_and_fill, migrations.RunPython.noop),
    ]

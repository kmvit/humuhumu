from django.db import migrations


def turn_samples_back_on(apps, schema_editor):
    """Вернуть образцам посуды «используется».

    Все существующие образцы были заведены через студию — multipart-ом, а
    DRF считает multipart html-формой и отсутствующую галочку трактует как
    снятую (см. комментарий в DishwareSampleSerializer). Поэтому каждый
    загруженный стакан лёг в базу неактивным: в студии он не показывался,
    к генерациям не цеплялся — и нейросеть придумывала посуду сама.

    Чинить руками нечего: фича прожила один день, и других причин для
    выключенного образца просто не было.
    """
    apps.get_model("catalog", "DishwareSample").objects.update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0014_product_image_is_generated_dishwaresample_imagebatch_and_more"),
    ]

    operations = [
        migrations.RunPython(turn_samples_back_on, migrations.RunPython.noop),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0031_bonus_redeem_staff")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="counter_stations_enabled",
            field=models.BooleanField(
                "Экраны кухни и бара на стойке",
                default=False,
                help_text="Разделяет приготовление по станциям при выдаче заказа по номеру.",
            ),
        ),
    ]

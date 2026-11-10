from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0008_alter_user_phone_alter_user_username_and_more")]
    operations = [migrations.AddField(model_name="user", name="is_platform_admin", field=models.BooleanField(default=False, verbose_name="Оператор хаба"))]

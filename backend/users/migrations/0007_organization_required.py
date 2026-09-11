"""Заведение у записи обязательно.

Ставится после core.0022_tenancy_fill — он проставил связь существующим
строкам. Обязательность и есть смысл этапа: строки без заведения больше
не появятся, поэтому будущее слияние баз не встретит сирот.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_user_organization'),
        ('core', '0022_tenancy_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
    ]

"""Заведение у записи обязательно.

Ставится после core.0022_tenancy_fill — он проставил связь существующим
строкам. Обязательность и есть смысл этапа: строки без заведения больше
не появятся, поэтому будущее слияние баз не встретит сирот.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shifts', '0003_alter_shift_managers_alter_shiftmember_managers_and_more'),
        ('core', '0022_tenancy_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shiftsettings',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='shift',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='shiftmember',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
    ]

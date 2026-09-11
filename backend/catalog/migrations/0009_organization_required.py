"""Заведение у записи обязательно.

Ставится после core.0022_tenancy_fill — он проставил связь существующим
строкам. Обязательность и есть смысл этапа: строки без заведения больше
не появятся, поэтому будущее слияние баз не встретит сирот.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_alter_category_managers_alter_product_managers_and_more'),
        ('core', '0022_tenancy_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='product',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='productlike',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
    ]

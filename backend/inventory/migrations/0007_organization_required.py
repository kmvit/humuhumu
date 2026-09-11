"""Заведение у записи обязательно.

Ставится после core.0022_tenancy_fill — он проставил связь существующим
строкам. Обязательность и есть смысл этапа: строки без заведения больше
не появятся, поэтому будущее слияние баз не встретит сирот.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_alter_purchaseline_managers_and_more'),
        ('core', '0022_tenancy_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockcategory',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='stockitem',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='stockitemalias',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='receipt',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='receiptitem',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='receiptscan',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='recipeitem',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='purchaselist',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
        migrations.AlterField(
            model_name='purchaseline',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='core.organization', verbose_name='Заведение'),
        ),
    ]

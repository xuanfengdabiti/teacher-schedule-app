from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0005_sessionchange_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='schedulerule',
            name='is_active',
            field=models.BooleanField(default=True, help_text='snapshot 导入缺失的规则会停用；重新显式导入同一规则时恢复启用，历史课次保留。', verbose_name='启用'),
        ),
    ]

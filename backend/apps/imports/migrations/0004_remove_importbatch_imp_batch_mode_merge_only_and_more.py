import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0001_initial'),
        ('imports', '0003_importdecision'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='importbatch',
            name='imp_batch_mode_merge_only',
        ),
        migrations.AddField(
            model_name='importbatch',
            name='enrollment_end_date',
            field=models.DateField(blank=True, help_text='snapshot 模式下，缺失选课的生效结束日期（不含当天）。不能按上传日猜测。', null=True, verbose_name='快照退课生效日期'),
        ),
        migrations.AddField(
            model_name='importbatch',
            name='missing_confirmed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='缺失清单确认时间'),
        ),
        migrations.AddField(
            model_name='importbatch',
            name='missing_confirmed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='confirmed_import_missing', to=settings.AUTH_USER_MODEL, verbose_name='缺失清单确认人'),
        ),
        migrations.AddField(
            model_name='importbatch',
            name='missing_signature',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='缺失清单指纹'),
        ),
        migrations.AddField(
            model_name='importbatch',
            name='scope_json',
            field=models.JSONField(blank=True, default=dict, verbose_name='快照范围'),
        ),
        migrations.AlterField(
            model_name='importbatch',
            name='mode',
            field=models.CharField(choices=[('merge', '增量合并'), ('snapshot', '限定范围快照（未实现）')], default='merge', max_length=16, verbose_name='模式'),
        ),
        migrations.AddConstraint(
            model_name='importbatch',
            constraint=models.CheckConstraint(condition=models.Q(('mode__in', ['merge', 'snapshot'])), name='imp_batch_mode_valid'),
        ),
    ]

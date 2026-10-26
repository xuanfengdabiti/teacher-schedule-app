# Generated for the B12a import slice.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academics", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("source_system", models.CharField(max_length=64, verbose_name="来源系统")),
                (
                    "mode",
                    models.CharField(
                        choices=[("merge", "增量合并")],
                        default="merge",
                        max_length=16,
                        verbose_name="模式",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("uploaded", "已上传，待预览"),
                            ("previewed", "已预览"),
                            ("committed", "已提交"),
                            ("failed", "失败"),
                        ],
                        default="uploaded",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("file_names", models.JSONField(blank=True, default=list, verbose_name="原始文件名")),
                ("file_hash", models.CharField(blank=True, default="", max_length=64, verbose_name="文件 SHA-256")),
                ("source_updated_at", models.DateTimeField(verbose_name="来源更新时间")),
                (
                    "preview_base_schedule_version",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="预览时学期课表版本"),
                ),
                ("row_counts", models.JSONField(blank=True, default=dict, verbose_name="逐文件行统计")),
                ("errors_json", models.JSONField(blank=True, default=list, verbose_name="预览错误")),
                ("committed_at", models.DateTimeField(blank=True, null=True, verbose_name="提交时间")),
                (
                    "committed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="committed_import_batches",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="提交人",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="import_batches",
                        to="academics.school",
                        verbose_name="学校",
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="import_batches",
                        to="academics.term",
                        verbose_name="学期",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="uploaded_import_batches",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="上传人",
                    ),
                ),
            ],
            options={
                "verbose_name": "导入批次",
                "verbose_name_plural": "导入批次",
                "ordering": ("-created_at",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("mode", "merge")),
                        name="imp_batch_mode_merge_only",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["term", "created_at"], name="imp_batch_term_created_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ImportIdentity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("source_system", models.CharField(max_length=64, verbose_name="来源系统")),
                ("entity_type", models.CharField(max_length=32, verbose_name="对象类型")),
                ("external_id", models.CharField(max_length=200, verbose_name="外部编号")),
                ("object_id", models.UUIDField(verbose_name="本地对象 ID")),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="identities",
                        to="imports.importbatch",
                        verbose_name="首次建立的批次",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="import_identities",
                        to="academics.school",
                        verbose_name="学校",
                    ),
                ),
            ],
            options={
                "verbose_name": "导入身份映射",
                "verbose_name_plural": "导入身份映射",
                "ordering": ("school", "source_system", "entity_type", "external_id"),
                "indexes": [
                    models.Index(
                        fields=["school", "source_system", "entity_type", "object_id"],
                        name="imp_identity_object_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("school", "source_system", "entity_type", "external_id"),
                        name="imp_identity_source_key_uniq",
                    ),
                ],
            },
        ),
    ]

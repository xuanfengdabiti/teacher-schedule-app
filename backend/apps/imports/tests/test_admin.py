"""Admin upload -> preview -> commit and error-table rendering."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.imports.models import ImportBatch, ImportStatus
from apps.imports.services import preview_batch
from apps.imports.tests.conftest import (
    IMPORT_FILES,
    PREVIEW_NOW,
    TEMPLATES_DIR,
    copy_templates,
    make_batch,
    read_csv,
    write_csv,
)
from apps.scheduling.models import ClassSession

pytestmark = pytest.mark.django_db

ADD_URL = reverse("admin:imports_importbatch_add")


def _uploaded_files() -> list[SimpleUploadedFile]:
    return [
        SimpleUploadedFile(
            name,
            (TEMPLATES_DIR / name).read_bytes(),
            content_type="text/csv",
        )
        for name in IMPORT_FILES
    ]


def test_upload_auto_previews_then_commits(client, school, academic_staff) -> None:
    client.force_login(academic_staff)

    response = client.get(ADD_URL)
    assert response.status_code == 200
    assert "上传导入批次" in response.content.decode()

    response = client.post(
        ADD_URL,
        {
            "school": str(school.pk),
            "source_system": "demo_csv_v1",
            "source_updated_at": "2026-10-26 08:00:00",
            "files": _uploaded_files(),
        },
    )

    assert response.status_code == 302
    batch = ImportBatch.objects.get()
    assert batch.status == ImportStatus.PREVIEWED
    assert batch.errors_json == []
    assert batch.row_counts["sessions.csv"]["new"] == 3

    detail = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))
    assert detail.status_code == 200
    rendered = detail.content.decode()
    assert "预览统计" in rendered
    assert "提交导入" in rendered

    commit = client.post(reverse("admin:imports_importbatch_commit", args=[batch.pk]))
    assert commit.status_code == 302
    batch.refresh_from_db()
    assert batch.status == ImportStatus.COMMITTED
    assert ClassSession.objects.count() == 40


def test_detail_renders_errors(client, school, academic_staff, tmp_path) -> None:
    client.force_login(academic_staff)
    paths = copy_templates(tmp_path)
    files = {path.name: path for path in paths}
    rows = read_csv(files["coverage.csv"])
    rows[1][8] = ""
    write_csv(files["coverage.csv"], rows)
    batch = make_batch(school, files=paths)
    preview_batch(batch, now=PREVIEW_NOW)

    response = client.get(reverse("admin:imports_importbatch_change", args=[batch.pk]))

    assert response.status_code == 200
    rendered = response.content.decode()
    assert "REQUIRED" in rendered
    assert "confirmation_ref" in rendered
    assert "提交导入" not in rendered

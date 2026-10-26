"""Shared fixtures for first-import and repeated-import tests."""

from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from apps.academics.models import School
from apps.imports.models import ImportBatch
from apps.imports.storage import copy_batch_files

TEMPLATES_DIR = Path(settings.BASE_DIR).parent / "templates" / "import"
IMPORT_FILES = [
    "terms.csv",
    "periods.csv",
    "teachers.csv",
    "classrooms.csv",
    "offerings.csv",
    "enrollments.csv",
    "calendar_exceptions.csv",
    "rules.csv",
    "sessions.csv",
    "room_blocks.csv",
    "resource_unavailability.csv",
    "coverage.csv",
]
PREVIEW_NOW = datetime(2026, 10, 27, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture(autouse=True)
def private_media(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return settings.MEDIA_ROOT


@pytest.fixture
def school(db) -> School:
    return School.objects.create(code="DEMO-SCHOOL", name="虚构演示学校")


@pytest.fixture
def academic_staff(db):
    return get_user_model().objects.create_superuser(
        email="academic-staff@example.invalid",
        password="test-pass-9x",
    )


def make_batch(
    school: School,
    *,
    files: list[Path] | None = None,
    source_system: str = "demo_csv_v1",
    source_updated_at: datetime = datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
) -> ImportBatch:
    selected = files or [TEMPLATES_DIR / name for name in IMPORT_FILES]
    batch = ImportBatch.objects.create(
        school=school,
        source_system=source_system,
        source_updated_at=source_updated_at,
        mode="merge",
    )
    names, file_hash = copy_batch_files(batch, selected)
    batch.file_names = names
    batch.file_hash = file_hash
    batch.save(update_fields=["file_names", "file_hash", "updated_at"])
    return batch


def copy_templates(tmp_path: Path) -> list[Path]:
    target = tmp_path / "templates"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in IMPORT_FILES:
        destination = target / name
        shutil.copyfile(TEMPLATES_DIR / name, destination)
        copied.append(destination)
    return copied


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

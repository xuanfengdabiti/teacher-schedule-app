"""Private storage helpers for imported source files."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterable
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import UploadedFile

from apps.imports.models import ImportBatch

ALLOWED_SUFFIXES = {".csv", ".xlsx"}


def _storage() -> FileSystemStorage:
    return FileSystemStorage(location=settings.MEDIA_ROOT)


def relative_batch_dir(batch: ImportBatch) -> str:
    return f"imports/{batch.pk}"


def batch_directory(batch: ImportBatch) -> Path:
    return Path(settings.MEDIA_ROOT) / relative_batch_dir(batch)


def batch_file_entries(batch: ImportBatch) -> list[tuple[str, Path]]:
    directory = batch_directory(batch)
    entries: list[tuple[str, Path]] = []
    for name in batch.file_names or []:
        safe_name = Path(name).name
        entries.append((safe_name, directory / safe_name))
    return entries


def hash_named_contents(entries: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(entries, key=lambda item: item[0]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def save_uploaded_files(batch: ImportBatch, files: list[UploadedFile]) -> tuple[list[str], str]:
    """Persist uploads under ``MEDIA_ROOT/imports/<batch_id>/``.

    Returns the stored base names and the SHA-256 of all named contents.  The
    caller must have validated that names are unique and suffixes supported.
    """
    storage = _storage()
    directory = relative_batch_dir(batch)
    names: list[str] = []
    content_entries: list[tuple[str, bytes]] = []
    for uploaded in files:
        safe_name = Path(uploaded.name or "").name
        if not safe_name:
            raise ValueError("上传文件缺少文件名。")
        if Path(safe_name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的上传文件类型：{safe_name}。")
        content = uploaded.read()
        uploaded.seek(0)
        content_entries.append((safe_name, content))
        saved_name = storage.save(f"{directory}/{safe_name}", uploaded)
        names.append(Path(saved_name).name)
    return names, hash_named_contents(content_entries)


def copy_batch_files(batch: ImportBatch, source_paths: Iterable[Path]) -> tuple[list[str], str]:
    """Test/development helper that copies trusted template files into storage."""
    directory = batch_directory(batch)
    directory.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    content_entries: list[tuple[str, bytes]] = []
    for source in source_paths:
        name = Path(source).name
        target = directory / name
        shutil.copyfile(source, target)
        names.append(name)
        content_entries.append((name, target.read_bytes()))
    return names, hash_named_contents(content_entries)


def delete_batch_files(batch: ImportBatch) -> None:
    directory = batch_directory(batch)
    if directory.exists():
        shutil.rmtree(directory)

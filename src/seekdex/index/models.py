"""Records passed between the indexer, database and UI worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileRecord:
    path: Path
    name: str
    extension: str
    size: int
    mtime: float
    mtime_ns: int
    is_image: bool
    width: int | None
    height: int | None
    root_path: Path
    indexed_at: float
    id: int | None = None
    capture_time: float | None = None
    capture_time_source: str | None = None
    capture_time_text: str | None = None
    file_uid: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    metadata_version: int = 0
    metadata_error: str | None = None


@dataclass
class ScanStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    cancelled: bool = False
    completed: bool = False

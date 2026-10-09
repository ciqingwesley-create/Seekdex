"""Resumable, scoped metadata completion and index-only device suggestions."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic
from PySide6.QtCore import QThread, Signal

from .image_metadata import METADATA_VERSION, ensure_image_metadata
from .index.database import FileIndex
from .index.models import FileRecord
from .paths import get_database_path
from .search import SearchOptions


@dataclass
class MetadataProgress:
    total: int = 0
    processed: int = 0
    failed: int = 0
    cancelled: bool = False


def complete_metadata(database: FileIndex, options: SearchOptions,
                      cancelled: Callable[[], bool] = lambda: False,
                      progress: Callable[[MetadataProgress], None] = lambda _: None,
                      on_record: Callable[[FileRecord], None] = lambda _: None) -> MetadataProgress:
    _, pending, _ = database.metadata_status(options, METADATA_VERSION)
    stats = MetadataProgress(total=pending)
    progress(stats)
    last_emit = monotonic()
    for record in database.metadata_candidates(options, METADATA_VERSION):
        if cancelled():
            break
        try:
            updated = ensure_image_metadata(record, database)
            stats.failed += int(updated.metadata_error is not None)
            on_record(updated)
        except Exception:
            stats.failed += 1
        stats.processed += 1
        if stats.processed % 100 == 0:
            database.commit()
        if stats.processed % 20 == 0 or monotonic()-last_emit >= .15:
            progress(stats)
            last_emit = monotonic()
    stats.cancelled = cancelled()
    database.commit()
    progress(stats)
    return stats


class MetadataThread(QThread):
    progress = Signal(object)
    batch_ready = Signal(object)
    completed = Signal(object, str)

    def __init__(self, options: SearchOptions, database_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.options = options.folder_scope()
        self.database_path = database_path or get_database_path()
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        stats = MetadataProgress()
        error = ""
        try:
            with FileIndex(self.database_path) as database:
                batch = []
                def collect(record: FileRecord) -> None:
                    batch.append(record)
                    if len(batch)>=20:
                        database.commit()
                        self.batch_ready.emit(batch[:])
                        batch.clear()
                stats = complete_metadata(database, self.options, self._cancelled.is_set,
                                          self.progress.emit, collect)
                if batch:
                    self.batch_ready.emit(batch)
        except Exception as exc:
            error = str(exc)
        self.completed.emit(stats, error)


class MetadataStatusThread(QThread):
    ready = Signal(object, int, int)
    def __init__(self, options: SearchOptions, database_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.options = options.folder_scope()
        self.database_path = database_path or get_database_path()

    def run(self) -> None:
        try:
            with FileIndex(self.database_path) as database:
                self.ready.emit(*database.metadata_status(self.options, METADATA_VERSION))
        except Exception:
            self.ready.emit([], 0, 0)

"""Load metadata and disk-cached previews only for requested visible results."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Condition
from time import monotonic
import logging

from PySide6.QtCore import QThread, Signal

from .index.database import FileIndex
from .image_metadata import ensure_image_metadata
from .paths import get_database_path, get_thumbnail_cache_dir, path_key
from .search import SearchResult, inspect_image
from .thumbnail_cache import ThumbnailCache


def load_visible_image(
    result: SearchResult, database: FileIndex, cache: ThumbnailCache
) -> SearchResult:
    """Run in a worker thread; source images are opened only on cache misses."""
    if not result.is_image:
        return result
    try:
        stat = result.path.stat()
    except OSError:
        return result
    key = path_key(result.path)
    record = database.get_by_key(key)
    if record is not None and (record.mtime_ns, record.size) != (stat.st_mtime_ns, stat.st_size):
        # A changed source invalidates both stored dimensions and thumbnail key.
        database.upsert(replace(
            record, size=stat.st_size, mtime=stat.st_mtime,
            mtime_ns=stat.st_mtime_ns, width=None, height=None,
            capture_time=None, capture_time_source=None, capture_time_text=None,
            camera_make=None, camera_model=None, metadata_version=0, metadata_error=None,
        ))
        database.commit()
        record = database.get_by_key(key)
    if record is not None:
        try:
            record = ensure_image_metadata(record, database)
            database.commit()
        except OSError:
            pass
    width = record.width if record is not None else None
    height = record.height if record is not None else None
    decoded: tuple[int, int] | None = None

    def render() -> bytes | None:
        nonlocal decoded
        try:
            image_width, image_height, thumbnail = inspect_image(result.path, with_thumbnail=True)
            decoded = (image_width, image_height)
            return thumbnail
        except Exception:
            return None

    thumbnail = cache.get_or_create(result.path, stat.st_mtime_ns, stat.st_size, render)
    if decoded is not None and (width is None or height is None):
        width, height = decoded
    elif thumbnail is not None and (width is None or height is None):
        # Legacy cache without stored dimensions: read just the dimensions once.
        try:
            width, height, _ = inspect_image(result.path, with_thumbnail=False)
        except Exception:
            pass
    if width is not None and height is not None:
        database.update_image_details(key, stat.st_mtime_ns, stat.st_size, width, height)
        database.commit()
    return SearchResult(
        result.path, stat.st_size, stat.st_mtime, width, height, thumbnail,
        stat.st_mtime_ns, True,
        record.capture_time if record else None,
        record.capture_time_source if record else None,
        record.capture_time_text if record else None,
        record.id if record else None,
        record.file_uid if record else result.file_uid,
        result.similarity if (result.mtime_ns, result.size) == (stat.st_mtime_ns, stat.st_size) else None,
        record.camera_make if record else None, record.camera_model if record else None,
        record.metadata_version if record else 0,
    )


class ImageLoader(QThread):
    image_ready = Signal(int, object)

    def __init__(
        self, database_path: Path | None = None, cache_dir: Path | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path or get_database_path()
        self.cache_dir = cache_dir or get_thumbnail_cache_dir()
        self._condition = Condition()
        self._pending: list[SearchResult] = []
        self._done: set[tuple[int, str, int, int]] = set()
        self._generation = 0
        self._stopping = False

    def set_visible(self, results: list[SearchResult], generation: int) -> None:
        # Replace pending work when the viewport moves. In-flight file work may finish.
        with self._condition:
            if generation != self._generation:
                self._done.clear()
            self._generation = generation
            self._pending = [
                result for result in results if result.is_image and
                (generation, path_key(result.path), result.mtime_ns, result.size) not in self._done
            ]
            self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._pending.clear()
            self._condition.notify()

    def run(self) -> None:
        from .settings import AppSettings
        from .cache_management import prune_thumbnails
        limit = AppSettings().get("thumbnail/limit_mb")*1024*1024
        next_prune = 0.0
        with FileIndex(self.database_path) as database:
            cache = ThumbnailCache(self.cache_dir)
            while True:
                with self._condition:
                    while not self._pending and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return
                    result = self._pending.pop(0)
                    generation = self._generation
                    token = (generation, path_key(result.path), result.mtime_ns, result.size)
                    if token in self._done:
                        continue
                    self._done.add(token)
                try:
                    loaded = load_visible_image(result, database, cache)
                except Exception as exc:
                    # One unreadable file or cache/SQLite error must not kill the loader.
                    logging.getLogger(__name__).warning("Image read failed: %s",type(exc).__name__)
                    loaded = result
                if limit and monotonic()>=next_prune:
                    try:prune_thumbnails(self.cache_dir,limit)
                    except OSError as exc:
                        logging.getLogger(__name__).warning("Cache maintenance failed: %s",type(exc).__name__)
                    next_prune = monotonic()+60
                with self._condition:
                    if generation == self._generation and not self._stopping:
                        self.image_ready.emit(generation, loaded)

"""Progressive search; image pixel decoding belongs to the visible-result loader."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from datetime import datetime
from threading import Event
from time import monotonic

from PySide6.QtCore import QThread, Signal

from .index.database import FileIndex
from .capture_time import ensure_capture_time
from .index.indexer import Indexer
from .index.models import FileRecord, ScanStats
from .paths import get_database_path, path_key, unique_roots
from .search import SearchOptions, SearchResult
from .image_filters import matches_image_filters


def matches(record: FileRecord, options: SearchOptions) -> bool:
    if not matches_image_filters(record, options):
        return False
    if options.filename.casefold().strip() not in record.name.casefold():
        return False
    if options.extensions and record.extension not in options.extensions:
        return False
    selected_time = record.capture_time if options.time_type == "capture" and record.capture_time is not None else record.mtime
    if options.time_type == "capture" and record.capture_time_text:
        # Compare camera calendar dates directly, including old dates unsupported
        # by Windows mktime. This also agrees with the SQLite capture query.
        calendar = datetime.fromisoformat(record.capture_time_text).replace(tzinfo=None)
        return ((options.modified_from is None or calendar >= datetime.fromtimestamp(options.modified_from))
                and (options.modified_before is None or calendar < datetime.fromtimestamp(options.modified_before)))
    if options.modified_from is not None and selected_time < options.modified_from:
        return False
    if options.modified_before is not None and selected_time >= options.modified_before:
        return False
    return True


def result_from_record(record: FileRecord) -> SearchResult:
    return SearchResult(
        record.path, record.size, record.mtime, record.width, record.height,
        None, record.mtime_ns, record.is_image,
        record.capture_time, record.capture_time_source, record.capture_time_text, record.id, record.file_uid,
        camera_make=record.camera_make, camera_model=record.camera_model, metadata_version=record.metadata_version,
    )


class SearchThread(QThread):
    progress = Signal(str)
    batch_ready = Signal(object)
    paths_removed = Signal(object)
    search_done = Signal(int, bool, str)

    def __init__(
        self,
        options: SearchOptions,
        refresh: bool = False,
        database_path: Path | None = None,
        cache_dir: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.options = options
        self.refresh = refresh
        self.database_path = database_path
        self.cache_dir = cache_dir  # Kept for existing callers; thumbnails use ImageLoader.
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        count = 0
        error = ""
        try:
            root = self.options.folder.expanduser().resolve()
            if not self.options.folders and not root.is_dir():
                raise NotADirectoryError(f"文件夹不存在或无法访问：{root}")
            options = replace(self.options, folder=root)
            roots = unique_roots(options.folders or (root,), options.recursive)
            with FileIndex(self.database_path or get_database_path()) as database:
                states = {r: database.coverage_state(r, options.recursive) for r in roots}
                state = ("complete" if all(s == "complete" for s in states.values()) else
                         "not_indexed" if all(s == "not_indexed" for s in states.values()) else "partial")
                seen: dict[str, tuple[int, int]] = {}
                batch: list[SearchResult] = []
                removed: list[str] = []
                last_emit = monotonic()
                limit_reached = False

                def flush() -> None:
                    nonlocal batch, removed, last_emit
                    if batch or removed:
                        database.commit()
                        if batch:
                            self.batch_ready.emit(batch)
                            batch = []
                        if removed:
                            self.paths_removed.emit(removed)
                            removed = []
                        last_emit = monotonic()

                def remove_key(key: str) -> None:
                    nonlocal count
                    if key in seen:
                        seen.pop(key)
                        count -= 1
                        removed.append(key)
                        if len(removed) >= 32:
                            flush()

                def offer(record: FileRecord) -> None:
                    nonlocal count, limit_reached
                    if options.max_results and count >= options.max_results:
                        limit_reached = True
                        return
                    key = path_key(record.path)
                    if options.ocr_text.strip():
                        from .ocr.store import OCRStore
                        if not OCRStore(database).matches(record, options.ocr_text):
                            remove_key(key)
                            return
                    if options.time_type == "capture" and (options.modified_from is not None or options.modified_before is not None):
                        if not matches(record, replace(options, modified_from=None, modified_before=None)):
                            remove_key(key)
                            return
                        record = ensure_capture_time(record, database)
                    if not matches(record, options):
                        remove_key(key)
                        return
                    signature = (record.mtime_ns, record.size)
                    if seen.get(key) == signature:
                        return
                    if key not in seen:
                        count += 1
                    seen[key] = signature
                    batch.append(result_from_record(record))
                    # The first hit is visible immediately. Later hits are batched.
                    if count == 1:
                        flush()
                    elif len(batch) >= 32 or monotonic() - last_emit >= 0.15:
                        flush()

                # A partial index gives immediate results while the scan resumes.
                if state != "not_indexed" or options.ocr_text.strip():
                    self.progress.emit("正在读取已有索引…")
                    candidates = replace(options, modified_from=None, modified_before=None) if options.time_type == "capture" else options
                    capture_filter = options.time_type == "capture" and (
                        options.modified_from is not None or options.modified_before is not None)
                    for record in database.query(candidates, snapshot=capture_filter):
                        if self._cancelled.is_set() or limit_reached:
                            break
                        offer(record)
                    flush()

                for scan_root in roots:
                    if self._cancelled.is_set() or limit_reached:
                        break
                    if not self.refresh and states[scan_root] == "complete":
                        continue
                    if not scan_root.is_dir():
                        self.progress.emit("一个索引目录不可访问，继续搜索其他目录。")
                        import logging
                        logging.getLogger(__name__).warning("Search scope unavailable")
                        continue
                    self.progress.emit("正在渐进扫描…")

                    def progress(stats: ScanStats) -> None:
                        database.commit()
                        flush()
                        self.progress.emit(
                            f"已扫描 {stats.scanned:,} 个文件，找到 {count:,} 个结果；"
                            f"新增 {stats.added:,}，更新 {stats.updated:,}"
                        )

                    stats = Indexer(database).refresh(
                        scan_root, lambda: self._cancelled.is_set() or limit_reached, progress, offer,
                        recursive=options.recursive, on_deleted=remove_key,
                    )
                    flush()
                    if not stats.completed and not stats.cancelled:
                        self.progress.emit("部分目录无法访问；已保留可用索引。")
                flush()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Search / index failed: %s",type(exc).__name__)
            error = str(exc) or type(exc).__name__
        self.search_done.emit(count, self._cancelled.is_set(), error)

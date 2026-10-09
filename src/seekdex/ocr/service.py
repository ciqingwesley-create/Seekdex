"""Cancelable, resumable OCR indexing; no UI or concrete engine dependency."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from ..index.database import FileIndex, _record
from ..index.indexer import Indexer
from ..index.models import FileRecord
from ..organize.planner import current_record
from ..search import SearchOptions
from ..paths import unique_roots
from .images import read_ocr_image
from .interfaces import OCRBackend, OCRResult
from .store import OCRStore


@dataclass
class OCRProgress:
    total: int = 0
    existing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: bool = False
    pictures_s: float = 0.0
    detection_s: float = 0.0
    recognition_s: float = 0.0
    database_s: float = 0.0
    backend: str = ""


class OCRService:
    def __init__(self, database: FileIndex, backend: OCRBackend, image_reader=read_ocr_image,
                 backend_setup=None) -> None:
        self.database, self.backend = database, backend
        self.store = OCRStore(database, backend.model_id)
        self.image_reader, self.backend_setup = image_reader, backend_setup

    def build(self, options: SearchOptions, paths: list[Path] | None = None, *, all_indexed: bool = False,
              cancelled: Callable[[], bool] = lambda: False,
              progress: Callable[[OCRProgress], None] = lambda _: None,
              message: Callable[[str], None] = lambda _: None) -> OCRProgress:
        stats = OCRProgress()
        if all_indexed:
            # Existing files only. Never visit unknown drives or folders.
            originals = [_record(row) for row in self.database.connection.execute("SELECT * FROM files WHERE is_image=1 ORDER BY id")]
        elif paths is None:
            message("正在轻量刷新当前目录，不读取图片像素…")
            for root in unique_roots(options.folders or (options.folder,), options.recursive):
                if cancelled():
                    break
                Indexer(self.database).refresh(root, cancelled, recursive=options.recursive,
                    progress=lambda s: message(f"OCR 范围：已检查 {s.scanned:,} 个文件"))
            originals = [record for record in self.database.query(options.folder_scope()) if record.is_image]
        else:
            originals = list(dict.fromkeys(paths))
        self.database.commit()
        stats.total = len(originals)
        pending = []
        for source in originals:
            if cancelled():
                stats.cancelled = True
                break
            try:
                path = source.path if isinstance(source, FileRecord) else source
                record = current_record(path.expanduser().resolve(), self.database)
                if not record.is_image:
                    continue
                if self.store.get(record) is not None:
                    stats.existing += 1
                else:
                    pending.append(record)
            except (OSError, ValueError):
                stats.failed += 1
        self.database.commit()
        progress(stats)
        if cancelled() or not pending:
            stats.cancelled = cancelled()
            return stats
        if self.backend_setup is not None:
            try:
                configured = self.backend_setup([record.path for record in pending], cancelled, message)
            except InterruptedError:
                stats.cancelled = True
                return stats
            if configured.model_id != self.backend.model_id:
                raise ValueError("后端调优不能改变 OCR 模型或预处理版本")
            self.backend = configured
        if cancelled():
            stats.cancelled = True
            return stats
        message("正在加载本地 OCR 模型…")
        self.backend.load_model()
        buffer: list[tuple[FileRecord, OCRResult]] = []
        history = deque(maxlen=20)
        started = perf_counter()

        def flush() -> None:
            if not buffer:
                return
            begin = perf_counter()
            with self.database.transaction():
                for record, result in buffer:
                    if self.store.save(record, result):
                        stats.completed += 1
                    else:
                        stats.failed += 1
            buffer.clear()
            stats.database_s += perf_counter()-begin

        for record in pending:
            if cancelled():
                break
            try:
                with self.image_reader(record.path) as image:
                    result = self.backend.recognize_image(image)
                stat = record.path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (record.size, record.mtime_ns):
                    raise ValueError("识别期间图片发生变化")
                stats.detection_s += result.detection_s
                stats.recognition_s += result.recognition_s
                buffer.append((record, result))
                history.append(perf_counter())
                if len(buffer) >= 8:
                    flush()
            except Exception as exc:
                stats.failed += 1
                message(f"OCR 跳过 {record.path.name}：{exc}")
            now = perf_counter()
            count = len(history)-1 if len(history)==20 else len(history)
            stats.pictures_s = count/max(0.001, now-(history[0] if len(history)==20 else started))
            stats.backend = self.backend.device
            # Include buffered completed work in UI progress, without per-file commits.
            progress(OCRProgress(**dict(vars(stats), completed=stats.completed+len(buffer))))
        flush()
        stats.cancelled = cancelled()
        stats.backend = self.backend.device
        progress(stats)
        return stats

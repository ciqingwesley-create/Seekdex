"""AI indexing and filtered search, independent of widgets and concrete models."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
import numpy as np
from time import perf_counter
from ..capture_time import ensure_capture_time
from ..index.database import FileIndex
from ..index.indexer import Indexer
from ..index.models import FileRecord
from ..organize.planner import current_record
from ..search import SearchOptions, SearchResult
from ..worker import matches, result_from_record
from .images import read_ai_image
from .interfaces import EmbeddingBackend, VectorSearchBackend
from .store import EmbeddingStore
from .vectors import NumpyVectorSearch
from .pipeline import run_pipeline
from .profiling import IndexProfiler
from ..paths import unique_roots


@dataclass
class IndexProgress:
    total: int = 0
    completed: int = 0
    failed: int = 0
    existing: int = 0
    cancelled: bool = False
    pictures_s: float = 0.0
    batch_size: int = 0
    backend: str = ""


def candidate_records(database: FileIndex, options: SearchOptions,
                      cancelled: Callable[[], bool] = lambda: False) -> Iterable[FileRecord]:
    capture_filter = options.time_type == "capture" and (
        options.modified_from is not None or options.modified_before is not None)
    query = replace(options, modified_from=None, modified_before=None) if capture_filter else options
    for record in database.query(query, snapshot=capture_filter):
        if cancelled():
            return
        if record.is_image:
            if capture_filter:
                record = ensure_capture_time(record, database)
            if matches(record, options):
                yield record


class AIService:
    def __init__(self, database: FileIndex, backend: EmbeddingBackend,
                 vectors: VectorSearchBackend | None = None, image_reader=read_ai_image,
                 profiler: IndexProfiler | None = None, backend_setup=None) -> None:
        self.database, self.backend = database, backend
        self.vectors = vectors or NumpyVectorSearch()
        self.store = EmbeddingStore(database, backend.model_id, backend.embedding_dimension)
        self.image_reader = image_reader
        self.profiler = profiler
        self.backend_setup = backend_setup
        self.search_counts = (0, 0)  # total and indexed for this query, before Top-K

    def build(self, options: SearchOptions, paths: list[Path] | None = None,
              cancelled: Callable[[], bool] = lambda: False,
              progress: Callable[[IndexProgress], None] = lambda _: None,
              message: Callable[[str], None] = lambda _: None) -> IndexProgress:
        stats = IndexProgress()
        if paths is None:
            message("正在轻量刷新所选目录（不解码图片）…")
            for root in unique_roots(options.folders or (options.folder,), options.recursive):
                if cancelled():
                    break
                Indexer(self.database).refresh(root, cancelled,
                    progress=lambda s: message(f"已检查 {s.scanned:,} 个文件…"), recursive=options.recursive)
            records = list(candidate_records(self.database, options, cancelled))
        else:
            records = []
            for path in dict.fromkeys(paths):
                if cancelled():
                    break
                try:
                    record = current_record(path.resolve(), self.database)
                    if record.is_image:
                        records.append(record)
                except OSError:
                    stats.failed += 1
        self.database.commit()
        stats.total = len(records) + stats.failed
        progress(stats)
        if cancelled():
            stats.cancelled = True
            return stats
        valid = self.store.valid_uids()
        pending = []
        for record in records:
            if record.file_uid in valid:
                stats.existing += 1
            else:
                pending.append(record)
        progress(stats)
        if not pending:
            return stats
        message("正在加载本地模型…")
        self.backend.load_model()
        if pending and self.backend_setup is not None and not cancelled():
            configured = self.backend_setup(self.backend, pending, cancelled, message)
            if (configured.model_id, configured.embedding_dimension) != (self.backend.model_id, self.backend.embedding_dimension):
                raise ValueError("性能后端改变了向量空间")
            self.backend = configured
        if cancelled():
            stats.cancelled = True
            return stats
        previous_profiler = getattr(self.backend, "profiler", None)
        self.backend.profiler = self.profiler
        reader = self.image_reader
        if self.profiler is not None and reader is read_ai_image:
            reader = lambda path: read_ai_image(path, self.profiler)
        if self.profiler is not None:
            self.profiler.started = perf_counter()
        try:
            run_pipeline(self.database, self.backend, pending, reader, stats, cancelled, progress, self.profiler)
        finally:
            self.backend.profiler = previous_profiler
            if self.profiler is not None:
                self.profiler.finish(stats.completed)
        return stats

    def search(self, options: SearchOptions, *, text: str = "", image_path: Path | None = None,
               top_k: int = 100, cancelled: Callable[[], bool] = lambda: False) -> list[SearchResult]:
        self.backend.load_model()
        records = {record.file_uid: record for record in candidate_records(self.database, options, cancelled)}
        if cancelled():
            return []
        exclude = None
        if image_path is not None:
            record = current_record(image_path.expanduser().resolve(), self.database)
            exclude = record.file_uid
            query = self.store.get(record)
            if query is None:
                with self.image_reader(record.path) as image:
                    query = self.backend.encode_image(image)
                stat = record.path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (record.size, record.mtime_ns):
                    raise ValueError("查询图片发生变化，请重试")
                if not self.store.save(record, query):
                    raise ValueError("查询图片索引已变化")
        else:
            query = self.backend.encode_text(text)
        self.database.commit()
        self.vectors.refresh(self.store)
        valid = self.store.valid_uids()
        self.search_counts = (len(records), sum(uid in valid for uid in records))
        if cancelled():
            return []
        ranked = self.vectors.search(query, set(records), top_k, exclude)
        return [replace(result_from_record(records[uid]), similarity=score) for uid, score in ranked]

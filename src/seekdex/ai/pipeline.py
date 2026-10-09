"""Bounded read/preprocess -> batch infer -> transactional writer pipeline."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from queue import Queue, Empty, Full
from threading import Thread, Event, Lock
from time import perf_counter
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from PIL import Image
from ..index.database import FileIndex
from ..index.models import FileRecord
from .interfaces import EmbeddingBackend
from .profiling import IndexProfiler
from .store import EmbeddingStore

if TYPE_CHECKING:
    from .service import IndexProgress


def _close(item) -> None:
    if hasattr(item, "close"):
        item.close()


def run_pipeline(database: FileIndex, backend: EmbeddingBackend, records: list[FileRecord],
                 reader: Callable[[Path], Image.Image], stats: IndexProgress,
                 cancelled: Callable[[], bool], progress: Callable[[IndexProgress], None],
                 profiler: IndexProfiler | None = None) -> None:
    batch_size = max(1, min(64, backend.batch_size))
    prepared: Queue = Queue(maxsize=batch_size*2)
    writes: Queue = Queue(maxsize=2)
    stop = Event()
    writer_failed = Event()
    producer_done = Event()
    stats_lock = Lock()
    errors = []
    started = perf_counter()
    history = deque([(started, stats.completed)])

    def notify() -> None:
        with stats_lock:
            now = perf_counter()
            history.append((now, stats.completed))
            while len(history) > 2 and now-history[0][0] > 8:
                history.popleft()
            stats.pictures_s = (stats.completed-history[0][1]) / max(0.001, now-history[0][0])
            stats.batch_size = backend.batch_size
            stats.backend = backend.device
            snapshot = replace(stats)
        progress(snapshot)

    def produce() -> None:
        try:
            for record in records:
                if stop.is_set() or cancelled():
                    break
                image = item = None
                try:
                    stat = record.path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (record.size, record.mtime_ns):
                        raise ValueError("读取前文件发生变化")
                    image = reader(record.path)
                    item = backend.prepare_image(image)
                    entry = (record, item, None)
                except Exception as exc:
                    entry = (record, None, exc)
                finally:
                    if image is not None and item is not image:
                        image.close()
                queued = False
                while not stop.is_set() and not cancelled():
                    try:
                        prepared.put(entry, timeout=0.05)
                        queued = True
                        break
                    except Full:
                        pass
                if not queued:
                    _close(item)
                    break
        except BaseException as exc:
            errors.append(exc)
            stop.set()
        finally:
            producer_done.set()

    def write() -> None:
        try:
            with FileIndex(database.database_path) as connection:
                store = EmbeddingStore(connection, backend.model_id, backend.embedding_dimension)
                while True:
                    payload = writes.get()
                    if payload is None:
                        break
                    ready, vectors = payload
                    valid_records, valid_vectors = [], []
                    for record, vector in zip(ready, vectors):
                        try:
                            current = record.path.stat()
                            if (current.st_size, current.st_mtime_ns) != (record.size, record.mtime_ns):
                                raise ValueError("推理期间文件发生变化")
                            valid_records.append(record)
                            valid_vectors.append(vector)
                        except OSError:
                            with stats_lock:
                                stats.failed += 1
                        except ValueError:
                            with stats_lock:
                                stats.failed += 1
                    if valid_records:
                        try:
                            saved = store.save_batch(valid_records, np.asarray(valid_vectors), profiler)
                            with stats_lock:
                                stats.completed += sum(saved)
                                stats.failed += len(saved)-sum(saved)
                        except Exception:
                            # The transaction rolled back this batch; older commits remain usable.
                            with stats_lock:
                                stats.failed += len(valid_records)
                    notify()
        except BaseException as exc:
            errors.append(exc)
            writer_failed.set()
            stop.set()

    def submit(records, values) -> None:
        while not writer_failed.is_set():
            try:
                writes.put((records, values), timeout=0.05)
                return
            except Full:
                pass
        raise RuntimeError("向量写入线程失败")

    producer = Thread(target=produce, name="clip-image-reader")
    writer = Thread(target=write, name="clip-embedding-writer")
    producer.start()
    writer.start()
    try:
        while not stop.is_set() and not cancelled():
            ready, items = [], []
            while len(items) < max(1, backend.batch_size) and not cancelled():
                try:
                    record, item, error = prepared.get(timeout=0.05)
                except Empty:
                    if producer_done.is_set():
                        break
                    continue
                if error is not None:
                    with stats_lock:
                        stats.failed += 1
                    notify()
                else:
                    ready.append(record)
                    items.append(item)
            if not items:
                if producer_done.is_set() or cancelled():
                    break
                continue
            if cancelled():
                for item in items:
                    _close(item)
                break
            try:
                try:
                    values = backend.encode_prepared(items)
                    if np.shape(values) != (len(ready), backend.embedding_dimension):
                        raise ValueError("模型返回数量或维度不一致")
                    submit(ready, values)
                except Exception:
                    if writer_failed.is_set():
                        raise
                    for record, item in zip(ready, items):
                        if cancelled():
                            break
                        try:
                            submit([record], backend.encode_prepared([item]))
                        except Exception:
                            with stats_lock:
                                stats.failed += 1
            finally:
                for item in items:
                    _close(item)
            if producer_done.is_set() and prepared.empty():
                break
    finally:
        stop.set()
        producer.join()
        while not prepared.empty():
            _, item, _ = prepared.get_nowait()
            _close(item)
        while writer.is_alive():
            try:
                writes.put(None, timeout=0.05)
                break
            except Full:
                pass
        writer.join()
    stats.cancelled = cancelled()
    notify()
    if errors:
        raise RuntimeError(str(errors[0])) from errors[0]

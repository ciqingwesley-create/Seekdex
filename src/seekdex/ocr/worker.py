"""Qt job boundary: file / model / database operations stay off the UI thread."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from ..index.database import FileIndex
from ..paths import get_database_path, get_ocr_model_root
from ..search import SearchOptions
from .backend import RapidOCRBackend, available_backends
from .model_cache import dependencies_available, installed, install_models, model_dir, MODEL_ID
from .service import OCRService


class OCRRuntime:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_ocr_model_root()
        self.backend = None
        self.requested = ""
        from ..settings import AppSettings
        self.threads = AppSettings().get("ocr/threads")

    def get_backend(self, requested: str):
        if self.backend is None or self.requested != requested:
            from .tuning import load_profile
            choice = load_profile(self.root).get("preferred", "ort-cpu") if requested == "auto" else requested
            self.backend = RapidOCRBackend(choice, self.root)
            self.backend.cpu_threads = self.threads
            self.requested = requested
        return self.backend

    def tune(self, paths, cancelled, message):
        from .tuning import select_backend
        self.backend = select_backend(paths, self.root, cancelled, message)
        return self.backend


class OCRTask(QThread):
    progress = Signal(str)
    completed = Signal(object, str)

    def __init__(self, runtime: OCRRuntime, operation: str, options: SearchOptions,
                 database_path: Path | None = None, backend: str = "auto", *,
                 paths: list[Path] | None = None, all_indexed: bool = False,
                 file_uid: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.runtime, self.operation, self.options = runtime, operation, options
        self.database_path = database_path or get_database_path()
        self.requested, self.paths, self.all_indexed = backend, paths, all_indexed
        self.file_uid = file_uid
        self.cancelled = Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        value, error = None, ""
        try:
            if self.operation == "install":
                value = install_models(self.runtime.root, cancelled=self.cancelled.is_set,
                                       progress=self.progress.emit)
            elif self.operation in {"status", "check"}:
                available, devices = available_backends()
                with FileIndex(self.database_path) as database:
                    records = list(database.query(self.options.folder_scope()))
                    total = sum(record.is_image for record in records)
                    uids = {row[0] for row in database.connection.execute(
                        "SELECT o.file_uid FROM ocr_results o JOIN files f ON f.file_uid=o.file_uid "
                        "WHERE o.ocr_model_id=? AND o.size=f.size AND o.mtime_ns=f.mtime_ns", (MODEL_ID,))}
                    indexed = sum(record.is_image and record.file_uid in uids for record in records)
                value = {"dependencies": dependencies_available(),
                         "installed": installed(self.runtime.root, verify=self.operation=="check"),
                         "total": total, "indexed": indexed, "backends": available, "devices": devices,
                         "cache": str(model_dir(self.runtime.root))}
            elif self.operation == "detail":
                with FileIndex(self.database_path) as database:
                    row = database.connection.execute(
                        "SELECT o.* FROM ocr_results o JOIN files f ON f.file_uid=o.file_uid "
                        "WHERE o.file_uid=? AND o.ocr_model_id=? AND f.size=o.size AND f.mtime_ns=o.mtime_ns",
                        (self.file_uid, MODEL_ID)).fetchone()
                    value = dict(row) if row else None
            elif self.operation == "index":
                options = replace(self.options, folder=self.options.folder.expanduser().resolve())
                if self.paths is None and not self.all_indexed and not options.folder.is_dir():
                    raise NotADirectoryError("所选 OCR 目录不存在或无法访问")
                backend = self.runtime.get_backend(self.requested)
                with FileIndex(self.database_path) as database:
                    service = OCRService(database, backend,
                        backend_setup=self.runtime.tune if self.requested=="auto" else None)
                    def report(stats) -> None:
                        self.progress.emit(f"OCR：{stats.existing+stats.completed+stats.failed:,} / {stats.total:,}；"
                            f"复用 {stats.existing:,}，完成 {stats.completed:,}，失败 {stats.failed:,}；"
                            f"{stats.backend or '准备中'}；{stats.pictures_s:.2f} pictures/s")
                    value = service.build(options, self.paths, all_indexed=self.all_indexed,
                        cancelled=self.cancelled.is_set, progress=report, message=self.progress.emit)
                    self.runtime.backend = service.backend
                    import logging
                    logging.getLogger(__name__).info("OCR model ready: %s, backend=%s",MODEL_ID,service.backend.device)
            else:
                raise ValueError("未知 OCR 操作")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("OCR task %s failed", self.operation)
            error = f"{type(exc).__name__}: {exc}"
        self.completed.emit(value, error)

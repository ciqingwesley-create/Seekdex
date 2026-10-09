"""Settings maintenance and managed-directory jobs; never touch widgets."""
from pathlib import Path
from threading import Event
from time import monotonic
import csv
import logging
import shutil
from PySide6.QtCore import QThread, Signal
from .paths import get_database_path, get_thumbnail_cache_dir, get_model_cache_dir, get_ocr_model_root
from .index.database import FileIndex
from .index.catalog import IndexCatalog
from .index.indexer import Indexer
from .search import SearchOptions
from .cache_management import cache_entries, prune_thumbnails

log = logging.getLogger(__name__)


class ProductTask(QThread):
    progress = Signal(str)
    completed = Signal(object, str)

    def __init__(self, operation: str, database_path: Path | None = None, *,
                 roots: list[Path] | None = None, parameters: dict | None = None, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.database_path = database_path or get_database_path()
        self.roots = roots or []
        self.parameters = parameters or {}
        self.cancelled = Event()
        self.child = None

    def cancel(self):
        self.cancelled.set()
        if self.child is not None:
            self.child.cancel()

    def run(self):
        value, error = None, ""
        try:
            if self.operation in {"scan", "ai", "ocr"}:
                value = self.index_directories()
            else:
                with FileIndex(self.database_path) as database:
                    catalog = IndexCatalog(database)
                    if self.operation == "catalog":
                        value = catalog.statistics()
                    elif self.operation == "add":
                        for root in self.roots:
                            catalog.add(root, self.parameters.get("recursive", True))
                        value = catalog.statistics()
                    elif self.operation == "remove":
                        value = [catalog.remove(root) for root in self.roots]
                    elif self.operation == "integrity":
                        value = [r[0] for r in database.connection.execute("PRAGMA integrity_check")]
                    elif self.operation == "export":
                        destination = Path(self.parameters["destination"])
                        cursor = database.connection.execute("SELECT * FROM file_operations ORDER BY id")
                        with destination.open("w", encoding="utf-8-sig", newline="") as stream:
                            writer = csv.writer(stream)
                            writer.writerow([column[0] for column in cursor.description])
                            for row in cursor:
                                writer.writerow([("'"+v if v.startswith(("=","+","-","@")) else v)
                                                 if isinstance(v,str) else v for v in row])
                        value = str(destination)
                    elif self.operation == "status":
                        value = self.settings_status(database)
                    elif self.operation == "tune":
                        value = self.tune(database)
                    elif self.operation == "clear_thumbnails":
                        value = prune_thumbnails(Path(self.parameters["directory"]), 0, clear=True)
                    elif self.operation == "prune":
                        value = prune_thumbnails(Path(self.parameters["directory"]), self.parameters["max_bytes"])
                    elif self.operation == "relocate_cache":
                        from .storage import copy_missing_tree
                        copy_missing_tree(Path(self.parameters["source"]), Path(self.parameters["target"]), self.progress.emit)
                        value = True
                    elif self.operation == "clear_models":
                        from .ai.model_cache import snapshot_dir
                        from .ocr.model_cache import model_dir
                        if self.parameters["kind"] == "ai":
                            paths = [snapshot_dir(Path(self.parameters["directory"]))]
                        else:
                            paths = [model_dir(Path(self.parameters["directory"]), variant) for variant in ("small", "medium")]
                        for path in paths:
                            # Never recurse into a configured root or a symlink.
                            if path.is_symlink() or not path.resolve().is_relative_to(Path(self.parameters["directory"]).resolve()):
                                raise ValueError("模型目录不安全，未清理")
                            if path.is_dir():
                                def retry_readonly(function,filename,exc_info):
                                    candidate = Path(filename)
                                    if candidate.is_symlink():raise exc_info[1]
                                    candidate.chmod(candidate.stat().st_mode | 0o200)
                                    function(filename)
                                shutil.rmtree(path,onerror=retry_readonly)
                        value = True
                    else:
                        raise ValueError("未知维护任务")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.error("Product task %s failed: %s", self.operation, type(exc).__name__)
        self.completed.emit(value, error)

    def index_directories(self):
        results = []
        for root in self.roots:
            if self.cancelled.is_set():
                break
            options = SearchOptions(root, recursive=self.parameters.get("recursive", True))
            result, error = None, ""
            try:
                with FileIndex(self.database_path) as database:
                    catalog = IndexCatalog(database)
                    catalog.add(root, self.parameters.get("recursive", True))
                    catalog.set_status(root, self.operation, "running")
                if self.operation == "scan":
                    started = monotonic()
                    with FileIndex(self.database_path) as database:
                        result = Indexer(database).refresh(root, self.cancelled.is_set,
                            progress=lambda stats: self.progress.emit(f"{root.name}：已扫描 {stats.scanned:,} 个文件，"
                                f"{stats.scanned/max(.001,monotonic()-started):.0f} 文件/s"), recursive=options.recursive)
                else:
                    if self.operation == "ai":
                        from .ai.worker import AITask, AIRuntime
                        self.child = AITask(self.parameters.get("ai_runtime") or AIRuntime(), "index", options,
                            self.database_path, self.parameters.get("ai_backend", "auto"),
                            batch_size=self.parameters.get("ai_batch", 0))
                    else:
                        from .ocr.worker import OCRTask, OCRRuntime
                        self.child = OCRTask(self.parameters.get("ocr_runtime") or OCRRuntime(), "index", options,
                            self.database_path, self.parameters.get("ocr_backend", "auto"))
                    outcome = []
                    self.child.progress.connect(self.progress.emit)
                    # Called synchronously in this worker; no nested GUI thread.
                    self.child.completed.connect(lambda value, error: outcome.append((value,error)))
                    if self.cancelled.is_set(): self.child.cancel()
                    self.child.run()
                    result, error = outcome[0]
                    self.child = None
                if result is not None and getattr(result, "failed", 0):
                    error = f"有 {result.failed} 个文件读取 / 推理失败；可再次补全。"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            status = "error" if error else "partial" if self.cancelled.is_set() else "complete"
            if self.operation == "scan" and result is not None and not result.completed:
                status = "partial"
            with FileIndex(self.database_path) as database:
                IndexCatalog(database).set_status(root, self.operation, status, error)
            log.info("Index job %s finished: %s", self.operation, status)
            results.append(dict(root=str(root), result=result, error=error, status=status))
        return results

    def tune(self, database):
        from .ai.worker import AIRuntime
        from .index.database import _record
        from .settings import AppSettings
        runtime = self.parameters.get("ai_runtime") or AIRuntime()
        backend = runtime.get_backend(self.parameters.get("ai_backend", "auto"))
        backend.load_model()
        records = []
        for root in self.roots:
            # Only previously indexed samples are read, never unknown directories.
            where, params = database._where(SearchOptions(root))
            records.extend(_record(row) for row in database.connection.execute(
                "SELECT * FROM files WHERE "+" AND ".join(where)+" AND is_image=1 LIMIT 8",params).fetchall())
            if len(records)>=8:break
        if not records:
            self.progress.emit("模型可加载；没有已索引图片样本，实际性能调优将在首次 AI 索引时执行。")
            return "没有图片样本，首次建立 AI 索引时调优"
        runtime.batch_override = self.parameters.get("ai_batch",0)
        backend = runtime.configure_for_index(backend,records[:8],self.cancelled.is_set,self.progress.emit)
        runtime.backend = backend
        self.progress.emit(f"检测 / 调优结束：{backend.device}，batch {backend.batch_size}")
        return dict(device=backend.device,batch=backend.batch_size)

    def settings_status(self, database):
        from .ai.model_cache import installed as ai_installed, snapshot_dir
        from .ocr.model_cache import installed as ocr_installed, model_dir
        from .ai.openvino_backend import detect_openvino
        status = dict(database=str(self.database_path), database_bytes=sum(p.stat().st_size for p in
            (self.database_path, self.database_path.with_name(self.database_path.name+"-wal")) if p.exists()),
            files=database.connection.execute("SELECT count(*) FROM files").fetchone()[0],
            embeddings=database.connection.execute("SELECT count(*) FROM image_embeddings").fetchone()[0],
            ocr=database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0],
            thumbnail_bytes=sum(size for _,size,_ in cache_entries(get_thumbnail_cache_dir())),
            ai_installed=ai_installed(), ai_path=str(snapshot_dir()),
            ocr_installed=ocr_installed(get_ocr_model_root(), verify=False), ocr_path=str(model_dir(get_ocr_model_root())),
            openvino=detect_openvino())
        try:
            import torch
            status["cuda"] = torch.cuda.is_available()
        except Exception:
            status["cuda"] = False
        return status

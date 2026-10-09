"""One AI job at a time; all model, disk and SQLite work runs off the UI thread."""
from __future__ import annotations

from pathlib import Path
from threading import Event
from PySide6.QtCore import QThread, Signal
from ..index.database import FileIndex
from ..paths import get_database_path
from ..search import SearchOptions
from .model_cache import MODEL_ID, dependencies_available, download_model, installed


class AIRuntime:
    def __init__(self, model_cache_dir: Path | None = None) -> None:
        self.model_cache_dir = model_cache_dir
        self.backend = None
        self.requested_device = "auto"
        self.vectors = None
        self.batch_override = 0
        self.unavailable: dict[str, str] = {}

    def get_backend(self, requested_device: str):
        from .backend import ChineseClipBackend
        if self.backend is None or self.requested_device != requested_device:
            from .tuning import load_tuning
            from .openvino_backend import detect_openvino, OpenVINOEmbeddingBackend
            profile = load_tuning(self.model_cache_dir)
            selected = requested_device
            if selected == "auto":
                selected = profile.get("preferred") or ("ov-cpu" if detect_openvino()["available"] else "auto")
            recommendation = profile.get("backends", {}).get(selected, {})
            if selected.startswith("ov-"):
                self.backend = OpenVINOEmbeddingBackend(selected[3:].upper(), self.model_cache_dir,
                                                        recommendation.get("threads", 4))
            else:
                self.backend = ChineseClipBackend(selected, self.model_cache_dir)
                self.backend.cpu_threads = recommendation.get("threads", 6)
            self.requested_device = requested_device
        return self.backend

    def configure_for_index(self, backend, records, cancelled, message):
        from .tuning import load_tuning, save_tuning, tune_backend
        from .backend import ChineseClipBackend
        from .openvino_backend import OpenVINOEmbeddingBackend, detect_openvino
        from .images import read_ai_image
        profile = load_tuning(self.model_cache_dir)
        key = self.requested_device
        if key == "auto":
            key = profile.get("preferred", "")
        expected = {"cpu": "cpu", "cuda": "cuda", "ov-cpu": "OpenVINO CPU",
                    "ov-gpu": "OpenVINO GPU", "ov-auto": "OpenVINO AUTO"}
        if key in expected and backend.device != expected[key]:
            self.unavailable[key] = backend.device_message
            key = {"cpu": "cpu", "PyTorch CPU": "cpu", "OpenVINO CPU": "ov-cpu"}.get(backend.device, key)
        cached = profile.get("backends", {}).get(key)
        if cached or self.batch_override:
            backend.batch_size = self.batch_override or cached["batch_size"]
            if cached:
                threads = cached.get("threads", 6)
                if hasattr(backend, 'cpu_threads'):
                    backend.cpu_threads = threads
                elif getattr(backend, '_fallback', False):
                    backend.reference.cpu_threads = threads
                elif backend.device == 'OpenVINO CPU' and backend.threads != threads:
                    backend.threads = threads
                    backend._compile('CPU')
            return backend
        images = []
        try:
            for record in records[:8]:
                if cancelled():
                    return backend
                try:
                    images.append(read_ai_image(record.path))
                except Exception:
                    pass
            if not images:
                return backend
            candidates = {self.requested_device: backend}
            if self.requested_device == "auto":
                candidates = {"cpu": ChineseClipBackend("cpu", self.model_cache_dir)}
                if backend.device == "cuda":
                    candidates["cuda"] = backend
                else:
                    import torch
                    if torch.cuda.is_available():
                        candidates["cuda"] = ChineseClipBackend("cuda", self.model_cache_dir)
                detected = detect_openvino()
                if detected["available"]:
                    candidates["ov-cpu"] = backend if isinstance(backend, OpenVINOEmbeddingBackend) and backend.device == "OpenVINO CPU" else OpenVINOEmbeddingBackend("CPU", self.model_cache_dir)
                    if any(d.startswith("GPU") for d in detected['devices']):
                        candidates["ov-gpu"] = OpenVINOEmbeddingBackend("GPU", self.model_cache_dir)
            measurements = profile.setdefault("backends", {})
            for candidate_key, candidate in candidates.items():
                if cancelled():
                    return backend
                candidate.load_model()
                if candidate.device != expected.get(candidate_key, candidate.device):
                    self.unavailable[candidate_key] = candidate.device_message
                    continue
                try:
                    measured = tune_backend(candidate, images, cancelled, message)
                except InterruptedError:
                    return backend
                measurements[candidate_key] = measured
            usable = [choice for choice in candidates if choice in measurements and choice not in self.unavailable]
            if not usable:
                return backend
            winner = max(usable, key=lambda choice: measurements[choice]['pictures_s'])
            self.backend = candidates[winner]
            if self.requested_device == "auto":
                profile["preferred"] = winner
            save_tuning(profile, self.model_cache_dir)
            message(f"采用 {self.backend.device}，batch {self.backend.batch_size}（性能设置已缓存）")
            return self.backend
        finally:
            for image in images:
                image.close()


class AITask(QThread):
    progress = Signal(str)
    completed = Signal(object, str)
    candidate_counts = Signal(int, int)

    def __init__(self, runtime: AIRuntime, operation: str, options: SearchOptions,
                 database_path: Path | None = None, device: str = "auto",
                 paths: list[Path] | None = None, text: str = "",
                 image_path: Path | None = None, top_k: int = 100, batch_size: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.runtime, self.operation, self.options = runtime, operation, options
        self.database_path = database_path or get_database_path()
        self.device, self.paths, self.text = device, paths, text
        self.image_path, self.top_k = image_path, top_k
        self.batch_size = batch_size
        self.cancelled = Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        result, error = None, ""
        try:
            if self.operation == "download":
                result = download_model(self.runtime.model_cache_dir, self.cancelled.is_set, self.progress.emit)
            elif self.operation == "status":
                ready = dependencies_available()
                available = installed(self.runtime.model_cache_dir)
                with FileIndex(self.database_path) as database:
                    from .store import EmbeddingStore
                    valid = EmbeddingStore(database, MODEL_ID, 512).valid_uids()
                    total = indexed = 0
                    for record in database.query(self.options):
                        if self.cancelled.is_set():
                            break
                        if record.is_image:
                            total += 1
                            indexed += record.file_uid in valid
                backend = self.runtime.backend
                result = {"dependencies": ready, "installed": available,
                          "total": total, "indexed": indexed,
                          "device": backend.device if backend and backend.model is not None else "尚未加载"}
                from .openvino_backend import detect_openvino
                result['openvino'] = detect_openvino()
                result['unavailable_backends'] = dict(self.runtime.unavailable)
                import ctypes
                try:
                    driver = ctypes.WinDLL('nvcuda.dll')
                    count = ctypes.c_int()
                    result['cuda_available'] = driver.cuInit(0) == 0 and driver.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0
                except (AttributeError, OSError):
                    result['cuda_available'] = False
            else:
                from .service import AIService
                from .vectors import NumpyVectorSearch
                backend = self.runtime.get_backend(self.device)
                self.runtime.batch_override = self.batch_size
                self.progress.emit("正在加载本地模型…")
                backend.load_model()
                import logging
                logging.getLogger(__name__).info("AI model ready: %s, backend=%s",backend.model_id,backend.device)
                self.progress.emit(f"推理设备：{backend.device} {backend.device_message}")
                if self.runtime.vectors is None:
                    self.runtime.vectors = NumpyVectorSearch()
                with FileIndex(self.database_path) as database:
                    service = AIService(database, backend, self.runtime.vectors,
                                        backend_setup=self.runtime.configure_for_index)
                    if self.operation == "index":
                        def report(s):
                            self.progress.emit(f"AI 索引：总数 {s.total:,}，已有 {s.existing:,}，"
                                               f"本次完成 {s.completed:,}，失败 {s.failed:,}；{s.backend or backend.device}；"
                                               f"batch {s.batch_size}，{s.pictures_s:.1f} pictures/s")
                        result = service.build(self.options, self.paths, self.cancelled.is_set,
                                               report, self.progress.emit)
                    elif self.operation == "search":
                        result = service.search(self.options, text=self.text, image_path=self.image_path,
                                                top_k=self.top_k, cancelled=self.cancelled.is_set)
                        total, indexed = service.search_counts
                        self.candidate_counts.emit(indexed, total)
                    else:
                        raise ValueError("未知 AI 任务")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("AI task %s failed", self.operation)
            error = f"{type(exc).__name__}：{exc}"
            if isinstance(exc, ImportError) and (exc.name or "").startswith("seekdex."):
                error = ("程序模块版本不一致或安装不完整，请关闭全部程序窗口后重新启动。"
                         "无需重建 AI 索引。详情：" + error)
        self.completed.emit(result, error)

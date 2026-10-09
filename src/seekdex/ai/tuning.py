"""Measured batch/thread recommendations cached per model, machine and runtimes."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
from time import perf_counter
from .model_cache import MODEL_ID, snapshot_dir


def tuning_identity() -> str:
    versions = []
    for package in ("torch", "openvino", "transformers"):
        try:
            versions.append(importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            versions.append("unavailable")
    value = (MODEL_ID, platform.node(), platform.processor(), os.cpu_count(), versions, "pipeline-v1")
    return hashlib.sha256(repr(value).encode()).hexdigest()[:20]


def tuning_path(cache_dir: Path | None = None) -> Path:
    return snapshot_dir(cache_dir) / f"tuning-{tuning_identity()}.json"


def load_tuning(cache_dir: Path | None = None) -> dict:
    try:
        data = json.loads(tuning_path(cache_dir).read_text(encoding="utf8"))
        return data if data.get("identity") == tuning_identity() else {}
    except (OSError, ValueError):
        return {}


def save_tuning(data: dict, cache_dir: Path | None = None) -> None:
    path = tuning_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data, identity=tuning_identity(), model_id=MODEL_ID)
    temporary = path.with_suffix('.pending.json')
    temporary.write_text(json.dumps(data, indent=2), encoding="utf8")
    temporary.replace(path)


def tune_backend(backend, images, cancelled=lambda: False, message=lambda _: None) -> dict:
    backend.load_model()
    prepared = [backend.prepare_image(image) for image in images[:8]]
    if not prepared:
        return {"batch_size": 4, "threads": 4, "pictures_s": 0}
    entries = []
    def trial(batch, threads):
        if cancelled():
            raise InterruptedError("已取消性能调优")
        message(f"正在测量 {backend.device}：batch {batch}，线程 {threads}…（仅首次）")
        if hasattr(backend, "cpu_threads"):
            backend.cpu_threads = threads
        if hasattr(backend, "_compile") and not backend._fallback and backend.device == "OpenVINO CPU":
            backend.threads = threads
            backend._compile("CPU")
        items = [prepared[i % len(prepared)] for i in range(batch)]
        backend.encode_prepared(items)
        started = perf_counter()
        backend.encode_prepared(items)
        elapsed = perf_counter()-started
        result = {"batch_size": batch, "threads": threads, "pictures_s": batch/elapsed}
        entries.append(result)
        return result
    threads = [1, 2, 4, 6] if backend.device in {"cpu", "PyTorch CPU", "OpenVINO CPU"} else [4]
    best_thread = max((trial(4, min(n, os.cpu_count() or 2)) for n in threads), key=lambda r: r['pictures_s'])["threads"]
    measurements = [trial(batch, best_thread) for batch in (1, 4, 8, 16, 32)]
    fastest = max(r['pictures_s'] for r in measurements)
    # Prefer a smaller, responsive batch when the difference is within measurement noise.
    best = min((r for r in measurements if r['pictures_s'] >= fastest*0.95), key=lambda r: r['batch_size'])
    backend.batch_size = best['batch_size']
    if hasattr(backend, "cpu_threads"):
        backend.cpu_threads = best_thread
    if hasattr(backend, "_compile") and not backend._fallback and backend.device == "OpenVINO CPU":
        backend.threads = best_thread
        backend._compile("CPU")
    return dict(best, measurements=entries, device=backend.device)

"""Cache measured backend choices by model, hardware and runtime versions."""
from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import platform
from time import perf_counter

from .backend import RapidOCRBackend, available_backends
from .images import read_ocr_image
from .model_cache import model_dir, MODEL_ID
from .text import normalize_text


def signature() -> str:
    versions = []
    for name in ("rapidocr", "onnxruntime", "openvino"):
        try:
            versions.append(version(name))
        except PackageNotFoundError:
            versions.append("")
    _, devices = available_backends()
    value = [platform.processor(), platform.machine(), devices, versions, MODEL_ID]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def load_profile(root: Path | None = None) -> dict:
    try:
        profile = json.loads((model_dir(root) / "tuning.json").read_text(encoding="utf-8"))
        return profile if profile.get("signature") == signature() else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_profile(profile: dict, root: Path | None = None) -> None:
    folder = model_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "tuning.tmp"
    temporary.write_text(json.dumps(dict(profile, signature=signature()), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "tuning.json")


def select_backend(paths: list[Path], root: Path | None = None,
                   cancelled: Callable[[], bool] = lambda: False,
                   message: Callable[[str], None] = lambda _: None) -> RapidOCRBackend:
    cached = load_profile(root)
    if cached:
        return RapidOCRBackend(cached["preferred"], root)
    images = []
    try:
        for path in paths[:3]:
            if cancelled():
                raise InterruptedError("OCR 调优已取消")
            try:
                images.append(read_ocr_image(path))
            except Exception:
                pass
        if not images:
            return RapidOCRBackend("ort-cpu", root)
        measurements = {}
        reference = None
        winner = None
        for choice in available_backends()[0]:
            if cancelled():
                raise InterruptedError("OCR 调优已取消")
            message(f"正在实测 OCR {choice}，首次编译可能需要时间…")
            backend = RapidOCRBackend(choice, root)
            try:
                backend.load_model()
                if backend.backend_id != choice:
                    measurements[choice] = {"error": backend.device_message}
                    continue
                backend.recognize_image(images[0])
                start = perf_counter()
                results = []
                for image in images:
                    if cancelled():
                        raise InterruptedError("OCR 调优已取消")
                    results.append(backend.recognize_image(image))
                elapsed = perf_counter()-start
                if backend.backend_id != choice:
                    measurements[choice] = {"error": backend.device_message}
                    continue
                texts = [normalize_text(result.text) for result in results]
                if reference is None:
                    reference = texts
                agreement = min(SequenceMatcher(None, a, b).ratio() for a, b in zip(reference, texts))
                measurements[choice] = {"pictures_s": len(images)/elapsed,
                    "text_agreement": agreement, "device": backend.device}
                if agreement >= 0.97 and (winner is None or len(images)/elapsed > measurements[winner.backend_id]["pictures_s"]):
                    winner = backend
            except InterruptedError:
                raise
            except Exception as exc:
                measurements[choice] = {"error": str(exc)}
        if winner is None:
            raise RuntimeError("OCR 各后端均不可用")
        save_profile({"preferred": winner.backend_id, "measurements": measurements}, root)
        message(f"OCR 自动采用 {winner.device}，实测 {measurements[winner.backend_id]['pictures_s']:.2f} pictures/s")
        return winner
    finally:
        for image in images:
            image.close()

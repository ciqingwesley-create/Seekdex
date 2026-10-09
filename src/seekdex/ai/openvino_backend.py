"""Image-only OpenVINO acceleration in the original 512-dimensional CLIP space."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from collections.abc import Sequence
import numpy as np
from PIL import Image
from unittest.mock import patch
from .backend import ChineseClipBackend
from .interfaces import EmbeddingBackend
from .model_cache import MODEL_ID, snapshot_dir, installed
from .profiling import timed
from .store import normalize

FORMAT_VERSION = "image-fp32-ir-v1"


def detect_openvino() -> dict:
    try:
        import openvino as ov
        core = ov.Core()
        devices = {}
        for device in core.available_devices:
            details = {}
            for key in ("FULL_DEVICE_NAME", "OPTIMIZATION_CAPABILITIES", "DRIVER_VERSION"):
                try:
                    details[key] = str(core.get_property(device, key))
                except Exception:
                    pass
            devices[device] = details
        return {"available": True, "version": ov.__version__, "devices": devices, "reason": ""}
    except Exception as exc:
        return {"available": False, "devices": {}, "reason": str(exc)}


class OpenVINOEmbeddingBackend(EmbeddingBackend):
    model_id = MODEL_ID  # Runtime changes must not invalidate compatible existing vectors.
    embedding_dimension = 512

    def __init__(self, requested_device: str = "CPU", cache_dir: Path | None = None,
                 threads: int = 4) -> None:
        self.requested_device, self.cache_dir, self.threads = requested_device, cache_dir, threads
        self.reference = ChineseClipBackend("cpu", cache_dir)
        self.device = "OpenVINO " + requested_device
        self.device_message = ""
        self.batch_size = 4
        self.profiler = None
        self.model = None
        self._compiled = None
        self._fallback = False
        self.compatibility_cosine = None

    @property
    def conversion_dir(self) -> Path:
        return snapshot_dir(self.cache_dir) / FORMAT_VERSION

    def load_model(self) -> None:
        if self.model is not None or self._fallback:
            return
        try:
            import openvino as ov
            self.ov, self.core = ov, ov.Core()
            if not installed(self.cache_dir):
                raise RuntimeError("本地模型未安装")
            folder = self.conversion_dir
            metadata = {"model_id": MODEL_ID, "format": FORMAT_VERSION,
                        "preprocessing": "rgb224-exif-v1", "dimension": 512,
                        "openvino_version": ov.__version__}
            xml = folder / "image.xml"
            manifest = folder / "metadata.json"
            folder.mkdir(parents=True, exist_ok=True)
            from PySide6.QtCore import QLockFile
            lock = QLockFile(str(folder / "conversion.lock"))
            if not lock.tryLock(0):
                raise RuntimeError("其他进程正在转换模型，本次使用 PyTorch")
            try:
                self._ensure_ir(xml, manifest, metadata)
            finally:
                lock.unlock()
            self.model = self.core.read_model(xml)
            from transformers import ChineseCLIPProcessor
            self.processor = ChineseCLIPProcessor.from_pretrained(snapshot_dir(self.cache_dir),
                                                      local_files_only=True, use_fast=False)
            devices = self.core.available_devices
            requested = self.requested_device
            if requested == "GPU" and not any(device.startswith("GPU") for device in devices):
                self.device_message = "OpenVINO 未识别 Intel GPU，退回 CPU"
                requested = "CPU"
            self._compile(requested)
            try:
                self._validate_compatibility()
            except Exception as exc:
                if self.device == "OpenVINO CPU":
                    raise
                self.device_message = f"GPU 向量兼容性检查失败，退回 CPU：{exc}"
                self._compile("CPU")
                self._validate_compatibility()
        except Exception as exc:
            self._torch_fallback(str(exc))

    def _ensure_ir(self, xml: Path, manifest: Path, metadata: dict) -> None:
        ov, folder = self.ov, xml.parent
        valid = xml.is_file() and xml.with_suffix('.bin').is_file() and manifest.is_file()
        if valid:
            try:
                valid = json.loads(manifest.read_text(encoding="utf8")) == metadata
            except (ValueError, OSError):
                valid = False
        if not valid:
            self.reference.load_model()
            torch = self.reference.torch
            class Vision(torch.nn.Module):
                def __init__(self, source):
                    super().__init__()
                    self.vision = source.vision_model
                    self.projection = source.visual_projection
                def forward(self, pixels):
                    return self.projection(self.vision(pixel_values=pixels, return_dict=False)[1])
            # Suppress converter telemetry locally; never change global user consent.
            from openvino.tools.ovc.telemetry_stub import Telemetry
            with torch.inference_mode(), patch("openvino_telemetry.Telemetry", Telemetry):
                graph = ov.convert_model(Vision(self.reference.model).eval(),
                    example_input=torch.zeros(1, 3, 224, 224), input=[([-1, 3, 224, 224], ov.Type.f32)])
            folder.mkdir(parents=True, exist_ok=True)
            ov.save_model(graph, folder / "image.pending.xml", compress_to_fp16=False)
            (folder / "image.pending.bin").replace(xml.with_suffix('.bin'))
            (folder / "image.pending.xml").replace(xml)
            pending = manifest.with_suffix('.pending.json')
            pending.write_text(json.dumps(metadata, indent=2), encoding="utf8")
            pending.replace(manifest)

    def _compile(self, device: str) -> None:
        config = {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": "f32",
                  "CACHE_DIR": str(self.conversion_dir / "compiled")}
        if device == "CPU":
            config.update(INFERENCE_NUM_THREADS=self.threads, NUM_STREAMS=1)
        if device == "AUTO":
            config["DEVICE_PRIORITIES"] = "GPU,CPU" if any(d.startswith("GPU") for d in self.core.available_devices) else "CPU"
        try:
            self._compiled = self.core.compile_model(self.model, device, config)
            self.device = "OpenVINO " + device
        except Exception as exc:
            if device == "CPU":
                raise
            self.device_message = f"OpenVINO {device} 失败，退回 CPU：{exc}"
            self._compile("CPU")

    def _validate_compatibility(self) -> None:
        actual = str(self._compiled.get_property("EXECUTION_DEVICES"))
        hardware = []
        for device in self.core.available_devices:
            for key in ("FULL_DEVICE_NAME", "DRIVER_VERSION"):
                try:
                    hardware.append(str(self.core.get_property(device, key)))
                except Exception:
                    pass
        identity = hashlib.sha256(repr((MODEL_ID, FORMAT_VERSION, self.ov.__version__, actual, hardware, "f32")).encode()).hexdigest()[:16]
        marker = self.conversion_dir / f"compatible-{identity}.json"
        try:
            saved = float(json.loads(marker.read_text())["minimum_cosine"])
            if saved >= 0.9999:
                self.compatibility_cosine = saved
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.reference.load_model()
        pixels = np.random.default_rng(2026).normal(size=(3, 3, 224, 224)).astype(np.float32)
        with self.reference.torch.inference_mode():
            expected = self.reference.model.get_image_features(pixel_values=self.reference.torch.from_numpy(pixels)).numpy()
        received = self._compiled([pixels])[0]
        if received.shape != (3, 512):
            raise ValueError("OpenVINO 输出维度不兼容")
        cosine = np.sum(normalize(expected)*normalize(received), axis=1)
        self.compatibility_cosine = float(cosine.min())
        if not np.isfinite(self.compatibility_cosine) or self.compatibility_cosine < 0.9999:
            raise ValueError(f"OpenVINO 向量兼容性未通过：{self.compatibility_cosine}")
        pending = marker.with_suffix('.pending.json')
        pending.write_text(json.dumps({"minimum_cosine": self.compatibility_cosine,
                                      "execution_devices": actual}), encoding="utf8")
        pending.replace(marker)

    def _torch_fallback(self, reason: str) -> None:
        self._fallback = True
        self.device_message = "OpenVINO 不可用，退回 PyTorch CPU：" + reason
        self.reference.load_model()
        self.device = "PyTorch CPU"
        self.model = self.reference.model
        self.batch_size = self.reference.batch_size

    def prepare_image(self, image: Image.Image):
        if self._fallback:
            return self.reference.prepare_image(image)
        with timed(self.profiler, "preprocess"):
            return self.processor(images=[image], return_tensors="np").pixel_values[0]

    def encode_prepared(self, items: Sequence[np.ndarray]) -> np.ndarray:
        self.load_model()
        if self._fallback:
            self.reference.profiler = self.profiler
            return self.reference.encode_prepared(items)
        with timed(self.profiler, "tensor_batch"):
            pixels = np.stack(items)
        try:
            with timed(self.profiler, "inference"):
                return self._compiled([pixels])[0].copy()
        except Exception as exc:
            if self.device != "OpenVINO CPU":
                try:
                    self._compile("CPU")
                    self._validate_compatibility()
                    with timed(self.profiler, "inference"):
                        return self._compiled([pixels])[0].copy()
                except Exception:
                    pass
            self._torch_fallback(str(exc))
            return self.reference.encode_prepared(items)

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.load_model()
        return self.encode_prepared([self.prepare_image(image) for image in images])

    def encode_text(self, text: str) -> np.ndarray:
        return self.reference.encode_text(text)

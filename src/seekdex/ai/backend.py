"""Chinese CLIP implementation; heavy libraries loaded only inside AI tasks."""
from __future__ import annotations

import os
from threading import local
from pathlib import Path
from collections.abc import Sequence
import numpy as np
from PIL import Image
from .interfaces import EmbeddingBackend
from .model_cache import MODEL_ID, installed, snapshot_dir
from .profiling import timed


def select_device(torch, requested: str) -> tuple[str, str]:
    if requested == "cpu":
        return "cpu", ""
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("没有可用的 CUDA")
        # A real kernel test catches unsupported Pascal wheels and driver issues.
        sample = torch.ones((32, 32), device="cuda")
        (sample @ sample).sum().item()
        torch.cuda.synchronize()
        del sample
        return "cuda", ""
    except Exception as exc:
        return "cpu", f"CUDA 不可用，已使用 CPU：{exc}"


class ChineseClipBackend(EmbeddingBackend):
    model_id = MODEL_ID
    embedding_dimension = 512

    def __init__(self, requested_device: str = "auto", cache_dir: Path | None = None) -> None:
        self.requested_device = requested_device
        self.cache_dir = cache_dir
        self.model = None
        self.device = "cpu"
        self.device_message = ""
        self.profiler = None
        self.cpu_threads = max(1, min(6, (os.cpu_count() or 2) // 2))
        self._thread_state = local()

    def configure_execution(self) -> None:
        if getattr(self._thread_state, "threads", None) != self.cpu_threads:
            self.torch.set_num_threads(self.cpu_threads)
            try:
                if hasattr(self.torch, "set_num_interop_threads"):
                    self.torch.set_num_interop_threads(1)
            except RuntimeError:
                pass  # Inter-op can only be set once before parallel execution.
            self._thread_state.threads = self.cpu_threads

    def load_model(self) -> None:
        if self.model is not None:
            self.configure_execution()
            return
        if not installed(self.cache_dir):
            raise RuntimeError("本地模型未安装，请先点击“安装 / 检查模型”")
        try:
            import torch
            from transformers import ChineseCLIPModel, ChineseCLIPProcessor
        except Exception as exc:
            raise RuntimeError("AI 依赖不可用，请安装项目的 [ai] 可选依赖") from exc
        self.torch = torch
        self.configure_execution()
        self.device, self.device_message = select_device(torch, self.requested_device)
        self.batch_size = 4 if self.device == "cuda" else 2
        folder = snapshot_dir(self.cache_dir)
        self.processor = ChineseCLIPProcessor.from_pretrained(folder, local_files_only=True, use_fast=False)
        self.model = ChineseCLIPModel.from_pretrained(
            folder, local_files_only=True, use_safetensors=True, attn_implementation="eager",
        ).eval()
        try:
            self.model.to(self.device)
        except Exception as exc:
            self._cpu_fallback(str(exc))

    def _cpu_fallback(self, reason: str) -> None:
        self.device = "cpu"
        self.device_message = f"CUDA 推理失败，已退回 CPU：{reason}"
        self.batch_size = 2
        self.model.to("cpu")
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()

    def _images_once(self, images: Sequence[Image.Image]) -> np.ndarray:
        with timed(self.profiler, "preprocess"):
            values = self.processor(images=list(images), return_tensors="np")
        with timed(self.profiler, "tensor_batch"):
            pixels = self.torch.from_numpy(values.pixel_values).to(self.device)
        with timed(self.profiler, "inference"), self.torch.inference_mode():
            vectors = self.model.get_image_features(pixel_values=pixels)
            if self.device == "cuda":
                self.torch.cuda.synchronize()
        return vectors.float().cpu().numpy()

    def prepare_image(self, image: Image.Image):
        with timed(self.profiler, "preprocess"):
            return self.processor(images=[image], return_tensors="np").pixel_values[0]

    def encode_prepared(self, items) -> np.ndarray:
        self.load_model()
        with timed(self.profiler, "tensor_batch"):
            pixels = np.stack(items)
        try:
            with timed(self.profiler, "inference"), self.torch.inference_mode():
                value = self.model.get_image_features(
                    pixel_values=self.torch.from_numpy(pixels).to(self.device))
                if self.device == "cuda":
                    self.torch.cuda.synchronize()
                return value.float().cpu().numpy()
        except self.torch.cuda.OutOfMemoryError:
            self.torch.cuda.empty_cache()
            if len(items) > 1:
                split = max(1, len(items)//2)
                self.batch_size = split
                return np.concatenate([self.encode_prepared(items[i:i+split]) for i in range(0, len(items), split)])
            self._cpu_fallback("显存不足")
            return self.encode_prepared(items)
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            self._cpu_fallback(str(exc))
            return self.encode_prepared(items)

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.load_model()
        try:
            return self._images_once(images)
        except self.torch.cuda.OutOfMemoryError:
            self.torch.cuda.empty_cache()
            if len(images) > 1:
                split_size = max(1, len(images) // 2)
                self.batch_size = split_size
                return np.concatenate([self.encode_images(images[i:i+split_size])
                                       for i in range(0, len(images), split_size)])
            self._cpu_fallback("显存不足")
            return self._images_once(images)
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            self._cpu_fallback(str(exc))
            return self._images_once(images)

    def encode_text(self, text: str) -> np.ndarray:
        self.load_model()
        if not text.strip():
            raise ValueError("请输入图片内容")
        values = self.processor(text=[text], padding=True, truncation=True,
                                max_length=52, return_tensors="pt")
        def encode():
            with self.torch.inference_mode():
                # Chinese-CLIP projects CLS, and intentionally disables BERT's pooler.
                # Some Transformers releases' get_text_features assume a pooler.
                hidden = self.model.text_model(
                    **{key: value.to(self.device) for key, value in values.items()},
                    return_dict=True,
                ).last_hidden_state
                vector = self.model.text_projection(hidden[:, 0, :])
            return vector[0].float().cpu().numpy()
        try:
            return encode()
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            self._cpu_fallback(str(exc))
            return encode()

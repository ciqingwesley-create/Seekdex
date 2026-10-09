from pathlib import Path
from types import SimpleNamespace
import hashlib
import sys
import numpy as np
from PIL import Image
import pytest

from seekdex.ai import model_cache
from seekdex.ai.backend import ChineseClipBackend


def test_explicit_model_download_and_checksum(tmp_path, monkeypatch):
    calls = []
    def download(repo, name, *, revision, local_dir):
        calls.append((repo, name, revision))
        (local_dir / name).write_bytes(b"weights" if name.endswith("safetensors") else b"config")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    monkeypatch.setattr(model_cache, "WEIGHT_SHA256", hashlib.sha256(b"weights").hexdigest())
    assert not model_cache.installed(tmp_path)
    assert model_cache.download_model(tmp_path)
    assert model_cache.installed(tmp_path)
    assert len(calls) == 4 and all(c[2] == model_cache.REVISION for c in calls)


def test_download_cancel_leaves_no_installed_marker(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=lambda *a, **k: None))
    assert not model_cache.download_model(tmp_path, cancelled=lambda: True)
    assert not model_cache.installed(tmp_path)


def test_corrupt_weight_rejected_and_retry_possible(tmp_path, monkeypatch):
    def download(repo, name, *, revision, local_dir):
        (local_dir / name).write_bytes(b"corrupt")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    with pytest.raises(ValueError, match="校验失败"):
        model_cache.download_model(tmp_path)
    assert not model_cache.installed(tmp_path)
    assert not (model_cache.snapshot_dir(tmp_path) / "model.safetensors").exists()


def test_model_loading_forces_local_only_and_safe_weights(tmp_path, monkeypatch):
    calls = []
    class Model:
        @classmethod
        def from_pretrained(cls, folder, **kwargs):
            calls.append(kwargs)
            return cls()
        def eval(self): return self
        def to(self, device): return self
    class Processor:
        @classmethod
        def from_pretrained(cls, folder, **kwargs):
            calls.append(kwargs)
            return cls()
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(ChineseCLIPModel=Model, ChineseCLIPProcessor=Processor))
    monkeypatch.setattr("seekdex.ai.backend.installed", lambda _: True)
    backend = ChineseClipBackend("cpu", tmp_path)
    backend.load_model()
    backend.load_model()
    assert len(calls) == 2 and all(c["local_files_only"] for c in calls)
    assert calls[1]["use_safetensors"] is True


def test_cuda_oom_reduces_batch_without_real_gpu(monkeypatch):
    class OOM(RuntimeError): pass
    backend = ChineseClipBackend()
    backend.device = "cuda"
    backend.torch = SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=OOM, empty_cache=lambda: None))
    monkeypatch.setattr(backend, "load_model", lambda: None)
    def encode(images):
        if len(images) > 1:
            raise OOM("test memory limit")
        return np.ones((len(images), 512))
    monkeypatch.setattr(backend, "_images_once", encode)
    images = [Image.new("RGB", (8, 8)) for _ in range(4)]
    try:
        assert backend.encode_images(images).shape == (4, 512)
        assert backend.batch_size == 1
    finally:
        for image in images:
            image.close()


def test_lazy_ui_update_preserves_semantic_score(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from dataclasses import replace
    from PySide6.QtWidgets import QApplication
    from seekdex.ui import ResultModel
    from seekdex.search import SearchResult
    app = QApplication.instance() or QApplication([])
    model = ResultModel()
    item = SearchResult(tmp_path / "a.jpg", 10, 1, mtime_ns=1,
                        is_image=True, file_uid="stable", similarity=0.61)
    model.append([item])
    model.append([replace(item, width=32, height=24, similarity=None)])
    assert model.results[0].similarity == 0.61
    assert model.results[0].file_uid == "stable"
    assert model.data(model.index(0, 8)) == "0.610"


def test_text_uses_cls_when_model_pooler_disabled(monkeypatch):
    from contextlib import nullcontext
    backend = ChineseClipBackend()
    monkeypatch.setattr(backend, "load_model", lambda: None)
    class Tensor:
        def __init__(self, values): self.values = np.asarray(values)
        def to(self, device): return self
        def __getitem__(self, key): return Tensor(self.values[key])
        def float(self): return self
        def cpu(self): return self
        def numpy(self): return self.values
    backend.torch = SimpleNamespace(inference_mode=nullcontext)
    backend.processor = lambda **kwargs: {"input_ids": Tensor([[1, 2]])}
    hidden = np.array([[[1, 2, 3], [9, 9, 9]]])
    backend.model = SimpleNamespace(
        text_model=lambda **kwargs: SimpleNamespace(last_hidden_state=hidden, pooler_output=None),
        text_projection=lambda values: Tensor(values),
    )
    assert np.array_equal(backend.encode_text("一只猫"), [1, 2, 3])

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from seekdex import bundled_models as bundle
from seekdex.ai import model_cache as ai
from seekdex.ocr import model_cache as ocr
from seekdex.runtime_output import prepare_windowed_output
from seekdex.model_download import direct_download


def test_windowed_output_has_writable_sinks(monkeypatch):
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    monkeypatch.setenv("HF_HUB_DISABLE_XET", "0")
    original = sys.stdout, sys.stderr
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    sinks = ()
    try:
        prepare_windowed_output()
        sinks = sys.stdout, sys.stderr
        assert all(stream is not None for stream in sinks)
        sys.stderr.write("progress")
        sys.stderr.flush()
        prepare_windowed_output()
        assert sinks == (sys.stdout, sys.stderr)
        import os
        assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
        assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    finally:
        sys.stdout, sys.stderr = original
        for stream in sinks:
            stream.close()


def test_existing_console_streams_are_preserved():
    original = sys.stdout, sys.stderr
    prepare_windowed_output()
    assert original == (sys.stdout, sys.stderr)


@pytest.fixture
def model_payload(tmp_path, monkeypatch):
    weights = b"test model"
    digest = hashlib.sha256(weights).hexdigest()
    monkeypatch.setattr(ai, "WEIGHT_SHA256", digest)
    monkeypatch.setattr(ocr, "model_files", lambda variant="small": {"det": ("det.onnx", digest)})
    root = tmp_path / "bundle"
    files = {}
    for name, expected in bundle.required_files().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = weights if expected else b"{}"
        path.write_bytes(content)
        files[name] = dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest = dict(schema=1, ai_model_id=ai.MODEL_ID, ocr_model_id=ocr.MODEL_ID, files=files)
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_preinstalled_import_is_offline_and_reusable(model_payload, tmp_path, monkeypatch):
    ai_root, ocr_root = tmp_path / "ai", tmp_path / "ocr"
    assert bundle.install_preinstalled(model_payload, ai_root=ai_root, ocr_root=ocr_root)
    assert ai.installed(ai_root)
    assert ocr.installed(ocr_root)
    monkeypatch.setattr(bundle, "sha256", lambda *args: pytest.fail("Unnecessary rehash on next startup"))
    assert bundle.install_preinstalled(model_payload, ai_root=ai_root, ocr_root=ocr_root)
    assert not list(tmp_path.rglob("*.db"))


def test_bundle_rejects_path_escape(model_payload):
    path = model_payload / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"]["../evil.bin"] = dict(bytes=1, sha256="0" * 64)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="列表"):
        bundle.read_manifest(model_payload)


def test_bundle_rejects_wrong_model_space(model_payload):
    path = model_payload / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["ai_model_id"] = "other-model"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        bundle.read_manifest(model_payload)


def test_bundle_corruption_preserves_old_model(model_payload, tmp_path):
    name = next(name for name in bundle.required_files() if name.endswith("safetensors"))
    (model_payload / name).write_bytes(b"x" * (model_payload / name).stat().st_size)
    ai_root = tmp_path / "ai"
    old = ai_root / name
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    with pytest.raises(ValueError, match="校验失败"):
        bundle.install_preinstalled(model_payload, ai_root=ai_root, ocr_root=tmp_path / "ocr")
    assert old.read_bytes() == b"old"
    assert not list(tmp_path.rglob("*.part"))


def test_bundle_cancel_keeps_database_and_target(model_payload, tmp_path):
    database = tmp_path / "database.sqlite3"
    database.write_bytes(b"existing database")
    with pytest.raises(InterruptedError):
        bundle.install_preinstalled(model_payload, ai_root=tmp_path / "ai", ocr_root=tmp_path / "ocr",
                                    cancelled=lambda: True)
    assert database.read_bytes() == b"existing database"
    assert not list(tmp_path.rglob("*.part"))


class Response:
    def __init__(self, content): self.content = content
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield self.content


def test_direct_download_checks_hash_and_preserves_existing(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response(b"bad"))
    target = tmp_path / "model.safetensors"
    target.write_bytes(b"old")
    with pytest.raises(ValueError, match="校验失败"):
        direct_download("https://example.invalid", target, "0" * 64, lambda: False, lambda _: None)
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_direct_download_can_cancel(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response(b"valid"))
    states = iter([False, True])
    assert not direct_download("https://example.invalid", tmp_path / "model", None,
                               lambda: next(states), lambda _: None)
    assert not list(tmp_path.iterdir())


def test_download_falls_back_from_head_failure(tmp_path, monkeypatch):
    class LocalEntryNotFoundError(Exception): pass
    def unavailable(*args, **kwargs): raise LocalEntryNotFoundError("HEAD failed")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=unavailable))
    import requests
    contents = {name: b"weights" if name.endswith("safetensors") else b"{}" for name in ai.FILES}
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        return Response(contents[url.rsplit("/", 1)[-1]])
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(ai, "WEIGHT_SHA256", hashlib.sha256(b"weights").hexdigest())
    assert ai.download_model(tmp_path)
    assert all(ai.REVISION in url for url in urls)
    assert ai.installed(tmp_path)
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pytest.fail("Must reuse verified model"))
    assert ai.download_model(tmp_path)


def test_download_network_failure_is_actionable(tmp_path, monkeypatch):
    import requests
    def unavailable(*args, **kwargs): raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "get", unavailable)
    with pytest.raises(RuntimeError, match="预装版") as failure:
        direct_download("https://example.invalid", tmp_path / "model", None, lambda: False, lambda _: None)
    assert isinstance(failure.value.__cause__, requests.ConnectionError)
    assert not list(tmp_path.glob("*.part"))

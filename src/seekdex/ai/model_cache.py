"""Explicit download only; inference uses an immutable, local snapshot."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from collections.abc import Callable
from ..paths import get_model_cache_dir

REPOSITORY = "OFA-Sys/chinese-clip-vit-base-patch16"
REVISION = "f4a64596bbcf9a2a94591b74b9dc39b2e4e77e3e"
MODEL_ID = f"{REPOSITORY}@{REVISION}:rgb224-exif-v1"
WEIGHT_SHA256 = "29cc0b2bcf6ff777f2e15742be92b110e4acbdb2068356e862c4637a4b15fe4f"
FILES = ("config.json", "preprocessor_config.json", "vocab.txt", "model.safetensors")


def snapshot_dir(cache_dir: Path | None = None) -> Path:
    return (cache_dir or get_model_cache_dir()) / f"chinese-clip-vit-b16-{REVISION[:12]}"


def dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None
               for name in ("torch", "transformers", "huggingface_hub", "safetensors"))


def installed(cache_dir: Path | None = None) -> bool:
    folder = snapshot_dir(cache_dir)
    return (folder / "verified.txt").is_file() and all((folder / name).is_file() for name in FILES)


def download_model(cache_dir: Path | None = None,
                   cancelled: Callable[[], bool] = lambda: False,
                   progress: Callable[[str], None] = lambda _: None) -> bool:
    from huggingface_hub import hf_hub_download
    folder = snapshot_dir(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if installed(cache_dir):
        return True
    (folder / "verified.txt").unlink(missing_ok=True)
    for number, name in enumerate(FILES, 1):
        if cancelled():
            return False
        progress(f"下载模型文件 {number}/{len(FILES)}：{name}（权重约 753 MB）")
        try:
            hf_hub_download(REPOSITORY, name, revision=REVISION, local_dir=folder)
        except Exception as exc:
            import requests
            if not (isinstance(exc, requests.RequestException) or
                    type(exc).__name__ in {"LocalEntryNotFoundError", "HfHubHTTPError", "FileMetadataError"}):
                raise
            import logging
            from ..model_download import direct_download
            logging.getLogger(__name__).warning("Hub metadata/download failed; trying fixed-revision GET: %s", type(exc).__name__)
            progress("模型服务器元数据请求失败，正在尝试直接下载同一版本…")
            if not direct_download(f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}",
                    folder / name, WEIGHT_SHA256 if name == "model.safetensors" else None,
                    cancelled, progress):
                return False
    if cancelled():
        return False
    progress("正在校验模型权重…")
    digest = hashlib.sha256()
    with (folder / "model.safetensors").open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled():
                return False
            digest.update(block)
    if digest.hexdigest() != WEIGHT_SHA256:
        (folder / "model.safetensors").unlink(missing_ok=True)
        raise ValueError("模型权重校验失败，请重新安装模型")
    (folder / "verified.txt").write_text(WEIGHT_SHA256, encoding="ascii")
    return True


if __name__ == "__main__":
    download_model(progress=print)

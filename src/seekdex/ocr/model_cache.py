"""Only explicit installation downloads; inference uses verified local files."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

from ..paths import get_model_cache_dir

RELEASE = "v3.9.2"
PREPROCESS_VERSION = "rgb-exif-long2048-det1536-v1"
FILES = {
    "small": {
        "det": ("PP-OCRv6_det_small.onnx", "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
        "rec": ("PP-OCRv6_rec_small.onnx", "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    },
    "medium": {
        "det": ("PP-OCRv6_det_medium.onnx", "92078b7355007ccfffcd4c8cd441a3afd4538904d06881b29a155e1e679907c2"),
        "rec": ("PP-OCRv6_rec_medium.onnx", "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b"),
    },
}
CLASSIFIER = ("ch_ppocr_mobile_v2.0_cls_mobile.onnx", "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c")


def model_id(variant: str = "small", max_side: int = 2048) -> str:
    weights = FILES[variant]
    return f"ppocrv6-{variant}-{weights['det'][1][:8]}-{weights['rec'][1][:8]}-rgb-exif-long{max_side}-det{min(max_side,1536)}-v1"


MODEL_ID = model_id()


def model_dir(root: Path | None = None, variant: str = "small") -> Path:
    return (root or get_model_cache_dir() / "ocr") / f"ppocrv6-{variant}-{RELEASE}"


def model_files(variant: str = "small") -> dict[str, tuple[str, str]]:
    return dict(FILES[variant], cls=CLASSIFIER)


def dependencies_available() -> bool:
    return all(importlib.util.find_spec(module) is not None for module in ("rapidocr", "onnxruntime"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def installed(root: Path | None = None, variant: str = "small", *, verify: bool = True) -> bool:
    folder = model_dir(root, variant)
    try:
        for name, expected in model_files(variant).values():
            path = folder / name
            if not path.is_file() or not path.stat().st_size:
                return False
            if verify and sha256(path) != expected:
                return False
        return True
    except OSError:
        return False


def install_models(root: Path | None = None, variant: str = "small",
                   cancelled: Callable[[], bool] = lambda: False,
                   progress: Callable[[str], None] = lambda _: None) -> bool:
    import requests
    folder = model_dir(root, variant)
    folder.mkdir(parents=True, exist_ok=True)
    for task, (name, expected) in model_files(variant).items():
        if cancelled():
            return False
        path = folder / name
        if path.is_file() and sha256(path) == expected:
            continue
        version = "PP-OCRv4" if task == "cls" else "PP-OCRv6"
        url = f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/{RELEASE}/onnx/{version}/{task}/{name}"
        temporary = folder / (name + "." + uuid4().hex + ".part")
        digest = hashlib.sha256()
        try:
            progress(f"正在下载 OCR {task} 模型…")
            with requests.get(url, stream=True, timeout=(10, 30)) as response, temporary.open("wb") as stream:
                response.raise_for_status()
                downloaded = 0
                for block in response.iter_content(256 * 1024):
                    if cancelled():
                        return False
                    stream.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    progress(f"正在下载 OCR {task}：{downloaded / 1048576:.1f} MiB")
            if digest.hexdigest() != expected:
                raise ValueError(f"OCR {task} 模型校验失败，请重试安装")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    (folder / "manifest.json").write_text(json.dumps({
        "release": RELEASE, "variant": variant, "model_id": model_id(variant),
        "preprocessing": PREPROCESS_VERSION, "sha256": model_files(variant),
    }, indent=2), encoding="utf-8")
    return True

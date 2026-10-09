"""Import only the two fixed model families from a preinstalled distribution."""
from collections.abc import Callable
from pathlib import Path
import hashlib
import json
import os
import sys
from uuid import uuid4

from .ai import model_cache as ai
from .ocr import model_cache as ocr
from .paths import get_model_cache_dir, get_ocr_model_root


def required_files() -> dict[str, str | None]:
    files = {f"{ai.snapshot_dir(Path('.')).name}/{name}":
             ai.WEIGHT_SHA256 if name == "model.safetensors" else None for name in ai.FILES}
    files.update({f"ocr/{ocr.model_dir(Path('.')).name}/{name}": digest
                  for name, digest in ocr.model_files().values()})
    return files


def sha256(path: Path, cancelled: Callable[[], bool] = lambda: False) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled():
                raise InterruptedError("预装模型导入已取消")
            digest.update(block)
    return digest.hexdigest()


def bundled_root() -> Path | None:
    # Legacy environment override is solely a compatibility entry point.
    override = os.environ.get("SEEKDEX_BUNDLE") or os.environ.get("LOCAL_IMAGE_SEARCH_BUNDLE")
    if override and Path(override).is_absolute():
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "preinstalled-models"
    return None


def read_manifest(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf8"))
    expected = required_files()
    if (manifest.get("schema") != 1 or manifest.get("ai_model_id") != ai.MODEL_ID or
            manifest.get("ocr_model_id") != ocr.MODEL_ID or
            set(manifest.get("files", {})) != set(expected)):
        raise ValueError("预装模型清单版本或文件列表不匹配")
    for name, digest in expected.items():
        entry = manifest["files"][name]
        if (not isinstance(entry.get("bytes"), int) or entry["bytes"] <= 0 or
                not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64 or
                (digest and entry["sha256"] != digest)):
            raise ValueError(f"预装模型清单校验失败：{name}")
    return manifest


def install_preinstalled(root: Path | None = None, *, ai_root: Path | None = None,
                         ocr_root: Path | None = None,
                         cancelled: Callable[[], bool] = lambda: False,
                         progress: Callable[[str], None] = lambda _: None) -> bool:
    root = root or bundled_root()
    if root is None or not (root / "manifest.json").is_file():
        return False
    manifest = read_manifest(root)
    ai_root = ai_root or get_model_cache_dir()
    ocr_root = ocr_root or get_ocr_model_root()
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    marker = ai_root / f"preinstalled-{identity[:16]}.done"
    if marker.is_file() and ai.installed(ai_root) and ocr.installed(ocr_root, verify=False):
        return True
    for name, entry in manifest["files"].items():
        if cancelled():
            raise InterruptedError("预装模型导入已取消")
        relative = Path(name)
        target = (ocr_root / Path(*relative.parts[1:]) if relative.parts[0] == "ocr"
                  else ai_root / relative)
        # A manifest is never allowed to introduce arbitrary paths or files.
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("预装模型源路径无效")
        if (target.is_file() and target.stat().st_size == entry["bytes"] and
                sha256(target, cancelled) == entry["sha256"]):
            continue
        if not source.is_file() or source.stat().st_size != entry["bytes"]:
            raise ValueError(f"预装模型文件缺失：{relative.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + "." + uuid4().hex + ".part")
        digest = hashlib.sha256()
        try:
            progress(f"正在导入预装模型：{relative.name}")
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                    if cancelled():
                        raise InterruptedError("预装模型导入已取消")
                    output_stream.write(block)
                    digest.update(block)
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"预装模型文件校验失败：{relative.name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    ai.snapshot_dir(ai_root).joinpath("verified.txt").write_text(ai.WEIGHT_SHA256, encoding="ascii")
    marker.write_text(identity, encoding="ascii")
    progress("预装识图与 OCR 模型已导入，可离线使用。")
    return True

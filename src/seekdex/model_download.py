"""Verified streaming fallback when a Hub metadata request cannot be completed."""
from collections.abc import Callable
from pathlib import Path
import hashlib
import json
import logging
from uuid import uuid4


def direct_download(url: str, target: Path, expected_sha256: str | None,
                    cancelled: Callable[[], bool], progress: Callable[[str], None]) -> bool:
    import requests
    temporary = target.with_name(target.name + "." + uuid4().hex + ".part")
    try:
        for attempt in range(2):
            if cancelled():
                return False
            try:
                digest = hashlib.sha256()
                downloaded = 0
                with requests.get(url, stream=True, timeout=(15, 30)) as response, temporary.open("wb") as stream:
                    response.raise_for_status()
                    for block in response.iter_content(1024 * 1024):
                        if cancelled():
                            return False
                        if not block:
                            continue
                        stream.write(block)
                        digest.update(block)
                        downloaded += len(block)
                        if downloaded % (8 * 1024 * 1024) < len(block):
                            progress(f"正在下载 {target.name}：{downloaded / 1048576:.1f} MiB")
                if not downloaded:
                    raise ValueError("模型文件为空")
                if expected_sha256 and digest.hexdigest() != expected_sha256:
                    raise ValueError("模型权重校验失败，请重试安装")
                if target.suffix == ".json":
                    json.loads(temporary.read_text(encoding="utf8"))
                if cancelled():
                    return False
                temporary.replace(target)
                return True
            except requests.RequestException as exc:
                logging.getLogger(__name__).warning("Model download attempt %d failed: %s", attempt + 1, type(exc).__name__)
                if attempt == 1:
                    raise RuntimeError(f"无法下载 {target.name}：请检查 Hugging Face 连接和代理设置，"
                                       "或使用预装版离线安装模型。详细原因见应用日志。") from exc
        return False
    finally:
        temporary.unlink(missing_ok=True)

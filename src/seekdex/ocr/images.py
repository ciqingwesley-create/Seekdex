"""OCR-resolution RGB images; RAW uses previews without demosaicing."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps

from ..search import RAW_EXTENSIONS


def _prepare(image: Image.Image, max_side: int) -> Image.Image:
    if max_side < 256:
        raise ValueError("OCR 输入长边限制过小")
    ratio = min(1.0, max_side / max(image.size))
    image.draft("RGB", (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))))
    oriented = ImageOps.exif_transpose(image)
    try:
        oriented.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return oriented.convert("RGB")
    finally:
        if oriented is not image:
            oriented.close()


def read_ocr_image(path: Path, max_side: int = 2048) -> Image.Image:
    if path.suffix.lower().lstrip(".") in RAW_EXTENSIONS:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            preview = raw.extract_thumb()
            if preview.format == rawpy.ThumbFormat.JPEG:
                with Image.open(BytesIO(preview.data)) as image:
                    source_size = image.size
                    result = _prepare(image, max_side)
            elif preview.format == rawpy.ThumbFormat.BITMAP:
                with Image.fromarray(preview.data) as image:
                    source_size = image.size
                    result = _prepare(image, max_side)
            else:
                raise ValueError("RAW 无可用内嵌预览，不执行完整 RAW 解码")
        result.info["ocr_limitations"] = "RAW 使用内嵌预览，小字识别可能受限" + (
            "；预览长边不足 1600 像素" if max(source_size) < 1600 else "")
        return result
    with Image.open(path) as image:
        return _prepare(image, max_side)

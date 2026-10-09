"""Persistent camera and oriented dimensions; never decode RAW pixels here."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import TYPE_CHECKING
from PIL import Image

from .capture_time import CaptureTime, capture_time_from_tags
from .exif_metadata import read_exif_tags, tag_integer, oriented_dimensions

if TYPE_CHECKING:
    from .index.database import FileIndex
    from .index.models import FileRecord

METADATA_VERSION = 1


def _clean(value: str | None) -> str:
    return " ".join((value or "").replace("\0", "").split())


def camera_display_name(make: str | None, model: str | None) -> str:
    """Conservative formatting, not a camera model or manufacturer lookup."""
    manufacturer, device = _clean(make), _clean(model)
    short = re.sub(r"\s+(?:corporation|inc\.?|co\.?\s*,?\s*ltd\.?)$", "", manufacturer, flags=re.I)
    label = short.capitalize() if short.isupper() else short
    for prefix in (manufacturer, short):
        if prefix and (device.casefold()==prefix.casefold() or device.casefold().startswith(prefix.casefold()+" ")):
            return (label+device[len(prefix):]).strip()
    return " ".join(part for part in (label, device) if part)


def camera_search_text(make: str | None, model: str | None) -> str:
    return _clean(" ".join((make or "", model or "", camera_display_name(make, model)))).casefold()


@dataclass(frozen=True)
class ImageMetadata:
    camera_make: str | None
    camera_model: str | None
    width: int | None
    height: int | None
    capture: CaptureTime
    error: str | None = None


def _raw_dimensions(path: Path, tags: dict) -> tuple[int, int]:
    # NEF's Image ImageWidth may describe its 160x120 thumbnail, not the sensor.
    # LibRaw open_file reads its headers; do not call unpack or postprocess.
    import rawpy
    orientation = tag_integer(tags.get("Image Orientation"))
    try:
        with rawpy.imread(str(path)) as raw:
            sizes = raw.sizes
            if orientation is None:
                orientation = {3:3, 5:8, 6:6}.get(getattr(sizes, "flip", 0), 1)
            return oriented_dimensions(sizes.width, sizes.height, orientation)
    except Exception:
        width = tag_integer(tags.get("EXIF ExifImageWidth"))
        height = tag_integer(tags.get("EXIF ExifImageLength"))
        # Also supports TIFF containers without RAW data, used by metadata tools.
        if not width or not height:
            if "Image SubIFDs" in tags:
                raise
            width = tag_integer(tags.get("Image ImageWidth"))
            height = tag_integer(tags.get("Image ImageLength"))
        if not width or not height:
            raise
        return oriented_dimensions(width, height, orientation)


def read_image_metadata(path: Path, mtime: float) -> ImageMetadata:
    tags = {}
    try:
        tags = read_exif_tags(path)
    except Exception:
        pass
    def raw_string(name: str) -> str | None:
        value = str(tags[name]).rstrip("\0") if name in tags else ""
        return value if value.strip() else None
    error = None
    width = height = None
    try:
        if path.suffix.casefold() in (".nef", ".nrw"):
            width, height = _raw_dimensions(path, tags)
        else:
            # Image.open and getexif only inspect headers; no load/verify/resize.
            with Image.open(path) as image:
                orientation = tag_integer(tags.get("Image Orientation"), 1)
                # PNG's getexif may call load to find trailing eXIf chunks.
                if "Image Orientation" not in tags and image.format in ("JPEG", "TIFF", "WEBP"):
                    orientation = image.getexif().get(274, 1)
                width, height = oriented_dimensions(*image.size, orientation)
        if width <= 0 or height <= 0:
            raise ValueError("Invalid image dimensions")
    except Exception as exc:
        width = height = None
        error = type(exc).__name__
    return ImageMetadata(raw_string("Image Make"), raw_string("Image Model"),
                         width, height, capture_time_from_tags(tags, mtime), error)


def ensure_image_metadata(record: FileRecord, database: FileIndex) -> FileRecord:
    if not record.is_image or record.metadata_version >= METADATA_VERSION:
        return record
    stat = record.path.stat()
    if (record.size, record.mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        record = replace(record, size=stat.st_size, mtime=stat.st_mtime, mtime_ns=stat.st_mtime_ns,
            width=None, height=None, camera_make=None, camera_model=None, metadata_version=0,
            metadata_error=None, capture_time=None, capture_time_text=None, capture_time_source=None)
        database.upsert(record)
    metadata = read_image_metadata(record.path, record.mtime)
    after = record.path.stat()
    if (record.size, record.mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("读取元数据期间文件发生变化，请刷新索引后重试")
    updated = replace(record, width=metadata.width, height=metadata.height,
        camera_make=metadata.camera_make, camera_model=metadata.camera_model,
        metadata_version=METADATA_VERSION, metadata_error=metadata.error,
        capture_time=record.capture_time if record.capture_time is not None else metadata.capture.timestamp,
        capture_time_text=record.capture_time_text or metadata.capture.text,
        capture_time_source=record.capture_time_source or metadata.capture.source)
    if not database.update_image_metadata(updated):
        raise OSError("文件索引已变化，请刷新后重试")
    return updated

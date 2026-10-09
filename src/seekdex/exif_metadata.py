"""Shared header-only EXIF access, with no MakerNote or preview extraction."""
from __future__ import annotations

from pathlib import Path
import exifread


def read_exif_tags(path: Path) -> dict:
    with path.open("rb") as stream:
        return exifread.process_file(stream, details=False, extract_thumbnail=False)


def tag_integer(value: object, default: int | None = None) -> int | None:
    try:
        values = getattr(value, "values", value)
        return int(values[0] if isinstance(values, (list, tuple)) else values)
    except (TypeError, ValueError, IndexError, OverflowError):
        return default


def oriented_dimensions(width: int, height: int, orientation: int | None) -> tuple[int, int]:
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)

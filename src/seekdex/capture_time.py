"""Read capture timestamps from image headers without decoding pixels."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .exif_metadata import read_exif_tags

if TYPE_CHECKING:
    from .index.database import FileIndex
    from .index.models import FileRecord


@dataclass(frozen=True)
class CaptureTime:
    timestamp: float
    text: str
    source: str


def _parse(value: object, offset: object = "") -> datetime | None:
    text = str(value).strip().strip("\0")
    try:
        date = datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S")
        zone = str(offset).strip()
        if len(zone) == 6 and zone[0] in "+-" and zone[3] == ":":
            minutes = int(zone[1:3]) * 60 + int(zone[4:6])
            date = date.replace(tzinfo=timezone(timedelta(minutes=minutes if zone[0] == "+" else -minutes)))
        return date
    except (ValueError, OverflowError):
        return None


def read_capture_time(path: Path, mtime: float, is_image: bool = True) -> CaptureTime:
    tags = {}
    if is_image:
        try:
            tags = read_exif_tags(path)
        except Exception:
            pass
    return capture_time_from_tags(tags, mtime)


def capture_time_from_tags(tags: dict, mtime: float) -> CaptureTime:
    """Reuse the same EXIF read when completing camera and size metadata."""
    if tags:
        try:
            for field, source, offset in (
                ("DateTimeOriginal", "exif_datetime_original", "OffsetTimeOriginal"),
                ("DateTimeDigitized", "exif_datetime_digitized", "OffsetTimeDigitized"),
            ):
                for group in ("EXIF", "Image"):
                    date = _parse(tags.get(f"{group} {field}", ""), tags.get(f"EXIF {offset}", ""))
                    if date is not None:
                        return CaptureTime(date.timestamp(), date.isoformat(timespec="seconds"), source)
            # GPS records an explicit UTC date/time, unlike the editable Image DateTime tag.
            gps_date = tags.get("GPS GPSDate")
            gps_clock = tags.get("GPS GPSTimeStamp")
            if gps_date is not None and gps_clock is not None:
                values = gps_clock.values
                date = datetime.strptime(str(gps_date), "%Y:%m:%d").replace(tzinfo=timezone.utc)
                date += timedelta(hours=float(values[0]), minutes=float(values[1]), seconds=float(values[2]))
                return CaptureTime(date.timestamp(), date.isoformat(timespec="seconds"), "exif_gps_datetime")
        except Exception:
            pass
    date = datetime.fromtimestamp(mtime)
    return CaptureTime(mtime, date.isoformat(timespec="seconds"), "filesystem_mtime")


def ensure_capture_time(record: FileRecord, database: FileIndex) -> FileRecord:
    if record.capture_time is not None and record.capture_time_source and record.capture_time_text:
        return record
    capture = read_capture_time(record.path, record.mtime, record.is_image)
    updated = replace(record, capture_time=capture.timestamp,
                      capture_time_source=capture.source, capture_time_text=capture.text)
    database.update_capture_time(updated)
    return updated


def source_label(source: str | None) -> str:
    return {
        "exif_datetime_original": "EXIF 原始拍摄时间",
        "exif_datetime_digitized": "EXIF 数字化时间",
        "exif_gps_datetime": "EXIF GPS 时间",
        "filesystem_mtime": "文件修改时间",
    }.get(source or "", "待读取")

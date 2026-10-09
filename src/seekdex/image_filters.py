"""Shared image filter semantics for SQL and progressive scan results."""
from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING
from .image_metadata import camera_search_text, METADATA_VERSION

if TYPE_CHECKING:
    from .search import SearchOptions
    from .index.models import FileRecord

RESOLUTION_LEVELS = {"fhd": (1920, 1080), "4k": (3840, 2160), "8k": (7680, 4320)}


def validate_image_filters(options: SearchOptions) -> None:
    if options.resolution not in ("", *RESOLUTION_LEVELS):
        raise ValueError("未知的分辨率快捷条件")
    if options.orientation not in ("all", "landscape", "portrait", "square"):
        raise ValueError("未知的图片方向")
    for lower, upper, label in (
        (options.min_width, options.max_width, "宽度"),
        (options.min_height, options.max_height, "高度"),
        (options.min_megapixels, options.max_megapixels, "像素数量"),
    ):
        if any(v is not None and (not isfinite(v) or v < 0) for v in (lower, upper)):
            raise ValueError(f"{label}必须为非负有限数值")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"最小{label}不能大于最大{label}")


def has_image_filters(options: SearchOptions) -> bool:
    return bool(options.camera.strip() or options.resolution or options.orientation != "all" or
        any(v is not None for v in (options.min_width, options.max_width, options.min_height,
            options.max_height, options.min_megapixels, options.max_megapixels)))


def image_filter_sql(options: SearchOptions) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if has_image_filters(options):
        clauses.append("is_image=1")
        # Legacy dimensions may be unrotated. Preserve them, but verify headers
        # once before using them for the new filters.
        clauses.append("metadata_version>=?")
        params.append(METADATA_VERSION)
    if options.camera.strip():
        clauses.append("instr(camera_search_text, ?) > 0")
        params.append(" ".join(options.camera.split()).casefold())
    for column, lower, upper in (("width", options.min_width, options.max_width),
                                ("height", options.min_height, options.max_height),
                                ("width * height", options.min_megapixels, options.max_megapixels)):
        multiplier = 1_000_000 if column == "width * height" else 1
        if lower is not None:
            clauses.append(f"{column}>=?")
            params.append(lower*multiplier)
        if upper is not None:
            clauses.append(f"{column}<=?")
            params.append(upper*multiplier)
    if options.resolution:
        long_edge, short_edge = RESOLUTION_LEVELS[options.resolution]
        clauses.extend(("max(width,height)>=?", "min(width,height)>=?"))
        params.extend((long_edge, short_edge))
    if options.orientation != "all":
        operator = {"landscape":">", "portrait":"<", "square":"="}[options.orientation]
        clauses.append(f"width {operator} height")
    return clauses, params


def matches_image_filters(record: FileRecord, options: SearchOptions) -> bool:
    if not has_image_filters(options):
        return True
    if not record.is_image or record.metadata_version < METADATA_VERSION:
        return False
    if options.camera.strip() and " ".join(options.camera.split()).casefold() not in camera_search_text(record.camera_make, record.camera_model):
        return False
    for value, lower, upper in ((record.width, options.min_width, options.max_width),
                               (record.height, options.min_height, options.max_height),
                               (record.width*record.height if record.width is not None and record.height is not None else None,
                                options.min_megapixels*1_000_000 if options.min_megapixels is not None else None,
                                options.max_megapixels*1_000_000 if options.max_megapixels is not None else None)):
        if lower is not None and (value is None or value < lower):
            return False
        if upper is not None and (value is None or value > upper):
            return False
    if options.resolution or options.orientation != "all":
        if record.width is None or record.height is None:
            return False
        if options.resolution:
            long_edge, short_edge = RESOLUTION_LEVELS[options.resolution]
            if max(record.width, record.height)<long_edge or min(record.width, record.height)<short_edge:
                return False
        if options.orientation=="landscape" and record.width <= record.height:
            return False
        if options.orientation=="portrait" and record.height <= record.width:
            return False
        if options.orientation=="square" and record.width != record.height:
            return False
    return True

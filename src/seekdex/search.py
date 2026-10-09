"""Filesystem search and thumbnail extraction; no GUI objects live here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterator

from PIL import Image, ImageOps, UnidentifiedImageError
import rawpy

from .capture_time import read_capture_time
from .exif_metadata import oriented_dimensions


IMAGE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "ico", "avif"}
)
RAW_EXTENSIONS = frozenset({"nef", "nrw"})
THUMBNAIL_SIZE = (480, 360)


@dataclass(frozen=True)
class SearchOptions:
    folder: Path
    recursive: bool = True
    filename: str = ""
    extensions: frozenset[str] = frozenset()
    modified_from: float | None = None  # inclusive POSIX timestamp
    modified_before: float | None = None  # exclusive POSIX timestamp
    time_type: str = "modified"
    camera: str = ""
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    min_megapixels: float | None = None
    max_megapixels: float | None = None
    resolution: str = ""
    orientation: str = "all"
    ocr_text: str = ""
    folders: tuple[Path, ...] = ()
    max_results: int = 0

    def __post_init__(self) -> None:
        from .image_filters import validate_image_filters
        validate_image_filters(self)

    def folder_scope(self) -> SearchOptions:
        return SearchOptions(self.folder, recursive=self.recursive, folders=self.folders)


@dataclass(frozen=True)
class SearchResult:
    path: Path
    size: int
    modified: float
    width: int | None = None
    height: int | None = None
    thumbnail: bytes | None = None
    mtime_ns: int = 0
    is_image: bool = False
    capture_time: float | None = None
    capture_time_source: str | None = None
    capture_time_text: str | None = None
    file_id: int | None = None
    file_uid: str | None = None
    similarity: float | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    metadata_version: int = 0


def parse_extensions(value: str) -> frozenset[str]:
    """Accept comma, semicolon or whitespace separated extensions."""
    parts = value.replace(",", " ").replace(";", " ").split()
    return frozenset(part.lower().removeprefix("*.").removeprefix(".") for part in parts if part.strip("*."))


def _files(folder: Path, recursive: bool, cancelled: Callable[[], bool]) -> Iterator[Path]:
    pending = [folder]
    while pending and not cancelled():
        directory = pending.pop()
        try:
            for entry in directory.iterdir():
                if cancelled():
                    return
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if recursive:
                            pending.append(entry)
                    elif entry.is_file():
                        yield entry
                except OSError:
                    continue
        except OSError:
            continue


def _image_details(path: Path, with_thumbnail: bool = True) -> tuple[int, int, bytes | None]:
    with Image.open(path) as image:
        width, height = oriented_dimensions(*image.size, image.getexif().get(274, 1))
    with Image.open(path) as image:
        image.verify()
    if not with_thumbnail:
        return width, height, None
    with Image.open(path) as image:
        return width, height, _thumbnail_bytes(image)


def _thumbnail_bytes(image: Image.Image, fallback_orientation: int = 1) -> bytes:
    """Make a detailed, compact preview for the results table."""
    if image.format == "JPEG":
        image.draft("RGB", THUMBNAIL_SIZE)
    if not image.getexif().get(274) and fallback_orientation != 1:
        image.getexif()[274] = fallback_orientation
    oriented = ImageOps.exif_transpose(image)
    oriented.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
    rgba = oriented.convert("RGBA")
    background = Image.new("RGB", rgba.size, "white")
    background.paste(rgba, mask=rgba.getchannel("A"))
    output = BytesIO()
    background.save(output, format="JPEG", quality=88, subsampling=0)
    rgba.close()
    background.close()
    oriented.close()
    return output.getvalue()


def _raw_details(path: Path, with_thumbnail: bool = True) -> tuple[int, int, bytes | None]:
    """Use the embedded JPEG when available; render RAW only as a fallback."""
    with rawpy.imread(str(path)) as raw:
        orientation = {3:3, 5:8, 6:6}.get(getattr(raw.sizes, "flip", 0), 1)
        width, height = oriented_dimensions(raw.sizes.width, raw.sizes.height, orientation)
        if not with_thumbnail:
            return width, height, None
        try:
            embedded = raw.extract_thumb()
            if embedded.format == rawpy.ThumbFormat.JPEG:
                with Image.open(BytesIO(embedded.data)) as source:
                    return width, height, _thumbnail_bytes(source, orientation)
            elif embedded.format == rawpy.ThumbFormat.BITMAP:
                with Image.fromarray(embedded.data) as preview:
                    return width, height, _thumbnail_bytes(preview, orientation)
            else:
                raise ValueError("Unsupported RAW thumbnail format")
        except (rawpy.LibRawError, OSError, ValueError, UnidentifiedImageError):
            with Image.fromarray(raw.postprocess(half_size=True, output_bps=8)) as preview:
                return width, height, _thumbnail_bytes(preview)


def is_image_path(path: Path) -> bool:
    return path.suffix.lower().removeprefix(".") in IMAGE_EXTENSIONS | RAW_EXTENSIONS


def inspect_image(path: Path, with_thumbnail: bool = False) -> tuple[int, int, bytes | None]:
    if path.suffix.lower().removeprefix(".") in RAW_EXTENSIONS:
        return _raw_details(path, with_thumbnail)
    return _image_details(path, with_thumbnail)


def render_thumbnail(path: Path) -> bytes:
    _, _, thumbnail = inspect_image(path, with_thumbnail=True)
    if thumbnail is None:
        raise ValueError(f"No thumbnail available: {path}")
    return thumbnail


def search(
    options: SearchOptions, cancelled: Callable[[], bool] = lambda: False
) -> Iterator[SearchResult]:
    """Yield matching files, skipping inaccessible entries and broken images."""
    name_query = options.filename.casefold().strip()
    from .image_filters import has_image_filters, matches_image_filters
    from .image_metadata import read_image_metadata, METADATA_VERSION
    for path in _files(options.folder, options.recursive, cancelled):
        if cancelled():
            return
        if name_query and name_query not in path.name.casefold():
            continue
        extension = path.suffix.lower().removeprefix(".")
        if options.extensions and extension not in options.extensions:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        image = is_image_path(path)
        metadata = read_image_metadata(path, stat.st_mtime) if image and has_image_filters(options) else None
        if has_image_filters(options) and not matches_image_filters(SearchResult(
            path, stat.st_size, stat.st_mtime, metadata.width if metadata else None,
            metadata.height if metadata else None, is_image=image,
            camera_make=metadata.camera_make if metadata else None,
            camera_model=metadata.camera_model if metadata else None,
            metadata_version=METADATA_VERSION if metadata else 0), options):
            continue
        capture = read_capture_time(path, stat.st_mtime, image) if options.time_type == "capture" else None
        selected_time = datetime.fromisoformat(capture.text).replace(tzinfo=None).timestamp() if capture else stat.st_mtime
        if options.modified_from is not None and selected_time < options.modified_from:
            continue
        if options.modified_before is not None and selected_time >= options.modified_before:
            continue
        if image:
            try:
                width, height, thumbnail = inspect_image(path, with_thumbnail=True)
            except Exception:
                if extension not in RAW_EXTENSIONS:
                    continue
                width = height = thumbnail = None
        else:
            width = height = thumbnail = None
        yield SearchResult(path, stat.st_size, stat.st_mtime, width, height, thumbnail,
                           stat.st_mtime_ns, image, capture.timestamp if capture else None,
                           capture.source if capture else None, capture.text if capture else None,
                           camera_make=metadata.camera_make if metadata else None,
                           camera_model=metadata.camera_model if metadata else None,
                           metadata_version=METADATA_VERSION if metadata else 0)

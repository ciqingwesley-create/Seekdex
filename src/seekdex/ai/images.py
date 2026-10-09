"""Bounded RGB inputs. Nikon RAW uses embedded preview, never raw postprocess."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps
from ..search import RAW_EXTENSIONS
from .profiling import IndexProfiler, timed
from time import perf_counter


class _TimedStream:
    def __init__(self, stream, profiler):
        self.stream, self.profiler = stream, profiler
    def read(self, *args):
        start = perf_counter()
        try:
            return self.stream.read(*args)
        finally:
            self.profiler.add("file_read", perf_counter()-start)
    def __getattr__(self, name):
        return getattr(self.stream, name)


def _prepare(image: Image.Image, profiler: IndexProfiler | None = None) -> Image.Image:
    read_before = profiler.seconds['file_read'] if profiler else 0
    started = perf_counter()
    try:
        image.draft("RGB", (448, 448))
        image.load()
    finally:
        if profiler:
            profiler.add("pillow_decode", max(0, perf_counter()-started-(profiler.seconds['file_read']-read_before)))
    with timed(profiler, "preprocess"):
        image.thumbnail((448, 448), Image.Resampling.LANCZOS)
        oriented = ImageOps.exif_transpose(image)
    try:
        with timed(profiler, "preprocess"):
            return oriented.convert("RGB")
    finally:
        if oriented is not image:
            oriented.close()


def read_ai_image(path: Path, profiler: IndexProfiler | None = None) -> Image.Image:
    if path.suffix.lower().lstrip(".") in RAW_EXTENSIONS:
        import rawpy
        with timed(profiler, "file_read"):
            raw = rawpy.imread(str(path))
        with raw:
            with timed(profiler, "preview_extract"):
                preview = raw.extract_thumb()
            if preview.format == rawpy.ThumbFormat.JPEG:
                with Image.open(BytesIO(preview.data)) as image:
                    return _prepare(image, profiler)
            if preview.format == rawpy.ThumbFormat.BITMAP:
                with Image.fromarray(preview.data) as image:
                    return _prepare(image, profiler)
        raise ValueError("RAW 无可用内嵌预览；跳过 AI 推理，不解码完整 RAW")
    with timed(profiler, "file_read"):
        stream = path.open('rb')
    try:
        image = Image.open(_TimedStream(stream, profiler) if profiler else stream)
        with image:
            return _prepare(image, profiler)
    finally:
        stream.close()

"""Versioned, content-signature-based thumbnail files."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .paths import path_key


CACHE_VERSION = "jpeg-480x360-oriented-v2"


class ThumbnailCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def cache_path(self, path: Path, mtime_ns: int, size: int) -> Path:
        signature = f"{CACHE_VERSION}\0{path_key(path)}\0{mtime_ns}\0{size}"
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.jpg"

    def reuse_for_path(self, source: Path, target: Path, mtime_ns: int, size: int) -> None:
        old = self.cache_path(source, mtime_ns, size)
        new = self.cache_path(target, mtime_ns, size)
        for suffix in (".jpg", ".missing"):
            previous, destination = old.with_suffix(suffix), new.with_suffix(suffix)
            try:
                if previous.is_file() and not destination.exists():
                    try:
                        os.link(previous, destination)
                    except OSError:
                        with destination.open("xb") as output, previous.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
            except OSError:
                pass

    def get_or_create(
        self, path: Path, mtime_ns: int, size: int, render: Callable[[], bytes | None]
    ) -> bytes | None:
        target = self.cache_path(path, mtime_ns, size)
        missing = target.with_suffix(".missing")
        try:
            data = target.read_bytes()
            if data.startswith(b"\xff\xd8"):
                return data
        except FileNotFoundError:
            pass
        except OSError:
            pass
        if missing.exists():
            return None

        data = render()
        destination = target if data else missing
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + f".{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data or b"")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            # A read-only cache directory must not prevent showing a preview.
            pass
        return data

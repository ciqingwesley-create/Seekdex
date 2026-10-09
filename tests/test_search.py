from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
import numpy as np
import rawpy

from seekdex.search import SearchOptions, parse_extensions, search


def test_extension_parser() -> None:
    assert parse_extensions(".JPG, png; *.WebP") == frozenset({"jpg", "png", "webp"})


def test_filters_recursion_and_image_metadata(tmp_path: Path) -> None:
    Image.new("RGB", (120, 80), "red").save(tmp_path / "Photo.JPG")
    nested = tmp_path / "nested"
    nested.mkdir()
    Image.new("RGB", (42, 31), "blue").save(nested / "other.png")
    (tmp_path / "notes.txt").write_text("hello")

    results = list(search(SearchOptions(tmp_path, recursive=False, extensions=parse_extensions("jpg"))))
    assert [result.path.name for result in results] == ["Photo.JPG"]
    assert (results[0].width, results[0].height) == (120, 80)
    assert results[0].thumbnail.startswith(b"\xff\xd8")

    results = list(search(SearchOptions(tmp_path, filename="OTHER", recursive=True)))
    assert [result.path.name for result in results] == ["other.png"]

    results = list(search(SearchOptions(tmp_path, filename="notes")))
    assert len(results) == 1
    assert results[0].thumbnail is None
    assert results[0].width is None


def test_modified_range_is_start_inclusive_end_exclusive(tmp_path: Path) -> None:
    from os import utime

    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.touch()
    new.touch()
    start = datetime(2025, 1, 1).timestamp()
    end = datetime(2025, 1, 2).timestamp()
    utime(old, (start - 1, start - 1))
    utime(new, (start, start))
    results = list(search(SearchOptions(tmp_path, modified_from=start, modified_before=end)))
    assert [result.path.name for result in results] == ["new.txt"]


def test_corrupt_image_is_skipped_and_cancellation_stops(tmp_path: Path) -> None:
    (tmp_path / "broken.png").write_bytes(b"not an image")
    (tmp_path / "valid.txt").write_text("ok")
    assert [r.path.name for r in search(SearchOptions(tmp_path))] == ["valid.txt"]
    assert list(search(SearchOptions(tmp_path), lambda: True)) == []


def test_unsupported_raw_remains_searchable(tmp_path: Path) -> None:
    (tmp_path / "camera.nef").write_bytes(b"unsupported raw")
    results = list(search(SearchOptions(tmp_path)))
    assert [result.path.name for result in results] == ["camera.nef"]
    assert results[0].width is None and results[0].thumbnail is None


def test_inaccessible_subfolder_is_skipped(tmp_path: Path, monkeypatch) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (tmp_path / "visible.txt").touch()
    original = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == blocked:
            raise PermissionError("access denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    assert [r.path.name for r in search(SearchOptions(tmp_path))] == ["visible.txt"]


def test_nef_uses_embedded_preview_and_full_resolution(tmp_path: Path, monkeypatch) -> None:
    nef = tmp_path / "photo.NEF"
    nef.touch()
    preview = BytesIO()
    Image.new("RGB", (1200, 800), "green").save(preview, format="JPEG")

    class FakeRaw:
        sizes = type("Sizes", (), {"width": 6048, "height": 4024})()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_thumb(self):
            return rawpy.Thumbnail(rawpy.ThumbFormat.JPEG, preview.getvalue())

    monkeypatch.setattr(rawpy, "imread", lambda _: FakeRaw())
    results = list(search(SearchOptions(tmp_path, extensions=parse_extensions("nef"))))
    assert len(results) == 1
    assert (results[0].width, results[0].height) == (6048, 4024)
    with Image.open(BytesIO(results[0].thumbnail)) as image:
        assert image.size == (480, 320)


def test_nef_without_embedded_preview_uses_raw_decode(tmp_path: Path, monkeypatch) -> None:
    nef = tmp_path / "photo.nef"
    nef.touch()

    class FakeRaw:
        sizes = type("Sizes", (), {"width": 4000, "height": 3000})()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_thumb(self):
            raise rawpy.LibRawNoThumbnailError("no thumbnail")

        def postprocess(self, **kwargs):
            assert kwargs == {"half_size": True, "output_bps": 8}
            return np.zeros((50, 60, 3), dtype=np.uint8)

    monkeypatch.setattr(rawpy, "imread", lambda _: FakeRaw())
    results = list(search(SearchOptions(tmp_path)))
    assert len(results) == 1
    assert (results[0].width, results[0].height) == (4000, 3000)
    assert results[0].thumbnail.startswith(b"\xff\xd8")

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from seekdex.image_loader import load_visible_image
from seekdex.index.database import FileIndex
from seekdex.search import SearchOptions, SearchResult
from seekdex.thumbnail_cache import ThumbnailCache
from seekdex.worker import SearchThread


def _run_worker(options: SearchOptions, database: Path, cache: Path) -> list[SearchResult]:
    results: list[SearchResult] = []
    completion: list[tuple[int, bool, str]] = []
    worker = SearchThread(options, database_path=database, cache_dir=cache)
    worker.batch_ready.connect(lambda batch: results.extend(batch))
    worker.search_done.connect(lambda count, cancelled, error: completion.append((count, cancelled, error)))
    worker.run()
    assert completion == [(len(results), False, "")]
    return results


def test_search_uses_index_and_invalidates_edited_thumbnail(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    photo = root / "photo.jpg"
    Image.new("RGB", (40, 30), "red").save(photo)
    database_path = tmp_path / "app" / "index.db"
    cache_path = tmp_path / "cache"
    options = SearchOptions(root)

    first = _run_worker(options, database_path, cache_path)
    assert len(first) == 1 and first[0].thumbnail is None
    assert first[0].width is None
    assert database_path.is_file()
    assert list(cache_path.glob("*.jpg")) == []

    with FileIndex(database_path) as database:
        cache = ThumbnailCache(cache_path)
        loaded = load_visible_image(first[0], database, cache)
        assert loaded.thumbnail and (loaded.width, loaded.height) == (40, 30)
        assert next(database.query(options)).width == 40
        assert len(list(cache_path.glob("*.jpg"))) == 1

        import seekdex.image_loader as loader_module
        real_inspect = loader_module.inspect_image

        def unexpected(*args, **kwargs):
            raise AssertionError("cached result should not decode")

        monkeypatch.setattr(loader_module, "inspect_image", unexpected)
        again = load_visible_image(first[0], database, cache)
        assert again.thumbnail == loaded.thumbnail
        assert (again.width, again.height) == (40, 30)

        monkeypatch.setattr(loader_module, "inspect_image", real_inspect)
        old_ns = photo.stat().st_mtime_ns
        Image.new("RGB", (50, 35), "blue").save(photo)
        changed_ns = old_ns + 2_000_000_000
        os.utime(photo, ns=(changed_ns, changed_ns))
        changed = load_visible_image(first[0], database, cache)
        assert changed.thumbnail != loaded.thumbnail
        assert (changed.width, changed.height) == (50, 35)
        assert (next(database.query(options)).width, next(database.query(options)).height) == (50, 35)
        assert len(list(cache_path.glob("*.jpg"))) == 2

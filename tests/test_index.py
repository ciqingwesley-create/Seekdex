from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.index.models import FileRecord
from seekdex.paths import get_app_data_dir, get_cache_dir
from seekdex.search import SearchOptions, parse_extensions
from seekdex.thumbnail_cache import ThumbnailCache


def _record(path: Path, root: Path) -> FileRecord:
    stat = path.stat()
    return FileRecord(
        path=path, name=path.name, extension=path.suffix.lower().lstrip("."),
        size=stat.st_size, mtime=stat.st_mtime, mtime_ns=stat.st_mtime_ns,
        is_image=False, width=None, height=None, root_path=root,
        indexed_at=1.0,
    )


def _names(database: FileIndex, options: SearchOptions) -> set[str]:
    return {record.name for record in database.query(options)}


def test_database_creation_upsert_delete_and_unique_path(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_text("a")
    second.write_text("b")
    db_path = tmp_path / "app" / "index.db"
    with FileIndex(db_path, batch_size=2) as database:
        database.upsert(_record(first, root))
        database.upsert(_record(second, root))
        original_id = next(r.id for r in database.query(SearchOptions(root)) if r.name == "a.txt")
        database.upsert(replace(_record(first, root), size=123))
        rows = list(database.query(SearchOptions(root)))
        assert len(rows) == 2
        assert next(r for r in rows if r.name == "a.txt").id == original_id
        assert next(r for r in rows if r.name == "a.txt").size == 123
        other_id = next(r.id for r in rows if r.name == "b.txt")
        with pytest.raises(sqlite3.IntegrityError):
            database.connection.execute(
                "UPDATE files SET path=? WHERE id=?", (str(first), other_id)
            )
        assert database.delete_path(next(r["path_key"] for r in database.connection.execute(
            "SELECT path_key FROM files WHERE id=?", (other_id,)
        ))) == 1
        assert _names(database, SearchOptions(root)) == {"a.txt"}
        indexes = {row[1] for row in database.connection.execute("PRAGMA index_list(files)")}
        assert {"idx_files_name_fold", "idx_files_extension", "idx_files_mtime",
                "idx_files_root_path"} <= indexes
    assert db_path.is_file()


def test_index_queries_filters_and_directory_boundary(tmp_path: Path) -> None:
    root = tmp_path / "root"
    photo = root / "Photo"
    sibling = root / "Photos2"
    nested = photo / "2026"
    nested.mkdir(parents=True)
    sibling.mkdir()
    Image.new("RGB", (30, 20), "red").save(photo / "Äpfel.JPG")
    Image.new("RGB", (10, 8), "blue").save(nested / "winter.png")
    Image.new("RGB", (5, 5), "green").save(sibling / "wrong.jpg")
    old = 1_700_000_000
    new = old + 86400
    os.utime(photo / "Äpfel.JPG", (old, old))
    os.utime(nested / "winter.png", (new, new))
    with FileIndex(tmp_path / "app" / "index.db") as database:
        assert Indexer(database).refresh(root).completed
        assert database.has_coverage(photo)
        assert _names(database, SearchOptions(photo, recursive=False)) == {"Äpfel.JPG"}
        assert _names(database, SearchOptions(photo)) == {"Äpfel.JPG", "winter.png"}
        assert _names(database, SearchOptions(photo, filename="äPF")) == {"Äpfel.JPG"}
        assert _names(database, SearchOptions(photo, extensions=parse_extensions("png"))) == {"winter.png"}
        assert _names(database, SearchOptions(photo, modified_from=new, modified_before=new + 1)) == {"winter.png"}
        assert _names(database, SearchOptions(nested)) == {"winter.png"}


def test_incremental_add_change_delete_and_unchanged_image_skip(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "photos"
    sub = root / "sub"
    sub.mkdir(parents=True)
    a = root / "a.jpg"
    b = root / "b.png"
    c = sub / "c.jpg"
    Image.new("RGB", (20, 10), "red").save(a)
    Image.new("RGB", (21, 11), "blue").save(b)
    Image.new("RGB", (22, 12), "green").save(c)
    with FileIndex(tmp_path / "app" / "index.db") as database:
        indexer = Indexer(database)
        first = indexer.refresh(root)
        assert (first.added, first.completed) == (3, True)
        a_id = next(r.id for r in database.query(SearchOptions(root)) if r.name == "a.jpg")

        def unexpected(*args, **kwargs):
            raise AssertionError("bulk indexing must not decode images")
        monkeypatch.setattr(Image, "open", unexpected)
        import rawpy
        monkeypatch.setattr(rawpy, "imread", unexpected)
        second = indexer.refresh(root)
        assert second.unchanged == 3

        Image.new("RGB", (30, 15), "orange").save(a)
        Image.new("RGB", (25, 15), "purple").save(root / "new.png")
        b.unlink()
        c.unlink()
        sub.rmdir()
        third = indexer.refresh(root)
        assert (third.added, third.updated, third.deleted) == (1, 1, 2)
        rows = list(database.query(SearchOptions(root)))
        assert {r.name for r in rows} == {"a.jpg", "new.png"}
        changed_a = next(r for r in rows if r.name == "a.jpg")
        assert changed_a.id == a_id
        assert (changed_a.width, changed_a.height) == (None, None)


def test_incremental_detects_mtime_or_size_change(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    photo = root / "image.png"
    Image.new("RGB", (20, 10), "red").save(photo)
    with FileIndex(tmp_path / "app" / "index.db") as database:
        indexer = Indexer(database)
        indexer.refresh(root)
        original = photo.stat()
        newer_ns = original.st_mtime_ns + 2_000_000_000
        os.utime(photo, ns=(newer_ns, newer_ns))
        assert indexer.refresh(root).updated == 1
        assert indexer.refresh(root).unchanged == 1

        Image.new("RGB", (40, 30), "blue").save(photo)
        assert photo.stat().st_size != original.st_size
        os.utime(photo, ns=(newer_ns, newer_ns))
        assert indexer.refresh(root).updated == 1
        record = next(database.query(SearchOptions(root)))
        assert (record.width, record.height) == (None, None)


def test_corrupt_picture_skipped_raw_remains_searchable(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    (root / "broken.jpg").write_bytes(b"not an image")
    (root / "camera.nef").write_bytes(b"unsupported raw")
    (root / "notes.txt").write_text("ok")
    with FileIndex(tmp_path / "app" / "index.db") as database:
        stats = Indexer(database).refresh(root)
        assert stats.skipped == 0
        rows = list(database.query(SearchOptions(root)))
        assert {r.name for r in rows} == {"camera.nef", "notes.txt", "broken.jpg"}
        raw = next(r for r in rows if r.name == "camera.nef")
        assert raw.is_image and raw.width is None and raw.height is None


def test_inaccessible_directory_preserves_previous_records(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "photos"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "keep.txt").touch()
    with FileIndex(tmp_path / "app" / "index.db") as database:
        indexer = Indexer(database)
        indexer.refresh(root)
        original = Path.iterdir

        def guarded(directory: Path):
            if directory == blocked:
                raise PermissionError("blocked")
            return original(directory)

        monkeypatch.setattr(Path, "iterdir", guarded)
        stats = indexer.refresh(root)
        assert not stats.completed
        assert database.coverage_state(root, True) == "partial"
        assert _names(database, SearchOptions(root)) == {"keep.txt"}


def test_cancelled_index_keeps_database_usable(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    for number in range(20):
        (root / f"{number}.txt").write_text("x")
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 5

    with FileIndex(tmp_path / "app" / "index.db", batch_size=2) as database:
        indexer = Indexer(database)
        partial = indexer.refresh(root, cancelled)
        assert partial.cancelled and not partial.completed
        assert not database.has_coverage(root)
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        complete = indexer.refresh(root)
        assert complete.completed
        assert len(list(database.query(SearchOptions(root)))) == 20


def test_storage_locations_follow_platform_conventions(tmp_path: Path) -> None:
    assert get_app_data_dir(platform="win32", environ={"LOCALAPPDATA": "C:/Users/Alice/AppData/Local"}, home=tmp_path) == Path("C:/Users/Alice/AppData/Local/Seekdex")
    assert get_cache_dir(platform="win32", environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local"}, home=tmp_path).name == "thumbnails"
    assert get_app_data_dir(platform="linux", environ={}, home=tmp_path) == tmp_path / ".local" / "share" / "Seekdex"
    assert get_cache_dir(platform="linux", environ={"XDG_CACHE_HOME": str(tmp_path / "cache")}, home=tmp_path) == tmp_path / "cache" / "Seekdex" / "thumbnails"


def test_thumbnail_cache_reuses_and_invalidates_on_signature_change(tmp_path: Path) -> None:
    source = tmp_path / "picture.jpg"
    Image.new("RGB", (10, 10), "red").save(source)
    cache = ThumbnailCache(tmp_path / "app" / "thumbnails")
    stat = source.stat()
    calls = 0

    def render() -> bytes:
        nonlocal calls
        calls += 1
        return b"\xff\xd8preview"

    first = cache.get_or_create(source, stat.st_mtime_ns, stat.st_size, render)
    assert first == b"\xff\xd8preview"
    assert cache.cache_path(source, stat.st_mtime_ns, stat.st_size).is_file()
    assert cache.get_or_create(source, stat.st_mtime_ns, stat.st_size, render) == first
    assert calls == 1
    changed_ns = stat.st_mtime_ns + 2_000_000_000
    os.utime(source, ns=(changed_ns, changed_ns))
    changed = source.stat()
    assert cache.cache_path(source, changed.st_mtime_ns, changed.st_size) != cache.cache_path(source, stat.st_mtime_ns, stat.st_size)
    cache.get_or_create(source, changed.st_mtime_ns, changed.st_size, render)
    assert calls == 2
    source.write_bytes(source.read_bytes() + b"extra")
    os.utime(source, ns=(changed_ns, changed_ns))
    resized = source.stat()
    cache.get_or_create(source, resized.st_mtime_ns, resized.st_size, render)
    assert calls == 3

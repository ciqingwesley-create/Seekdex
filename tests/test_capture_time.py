from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from PIL import Image

from seekdex.capture_time import ensure_capture_time, read_capture_time
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.search import SearchOptions
from seekdex.worker import SearchThread


def _photo(path: Path, tags: dict[int, str]) -> None:
    exif = Image.Exif()
    exif[34665] = tags
    Image.new("RGB", (30, 20), "red").save(path, exif=exif)


def test_datetime_original_has_priority(tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    _photo(photo, {36867: "2026:05:06 14:23:10", 36868: "2026:06:07 01:02:03"})
    capture = read_capture_time(photo, photo.stat().st_mtime)
    assert capture.source == "exif_datetime_original"
    assert capture.text == "2026-05-06T14:23:10"


def test_digitized_used_when_original_invalid(tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    _photo(photo, {36867: "0000:00:00 00:00:00", 36868: "2026:06:07 01:02:03"})
    capture = read_capture_time(photo, photo.stat().st_mtime)
    assert capture.source == "exif_datetime_digitized"
    assert capture.text == "2026-06-07T01:02:03"


def test_exif_offset_preserves_camera_calendar_date(tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    _photo(photo, {36867: "2026:05:06 00:23:10", 36881: "+08:00"})
    capture = read_capture_time(photo, photo.stat().st_mtime)
    assert capture.text == "2026-05-06T00:23:10+08:00"
    assert capture.timestamp == datetime.fromisoformat(capture.text).timestamp()


def test_no_exif_and_corrupt_image_fall_back_to_mtime(tmp_path: Path) -> None:
    for name, data in (("broken.jpg", b"broken"), ("note.txt", b"plain")):
        path = tmp_path / name
        path.write_bytes(data)
        capture = read_capture_time(path, 1_700_000_000, name.endswith("jpg"))
        assert capture.timestamp == 1_700_000_000
        assert capture.source == "filesystem_mtime"


def test_raw_tiff_headers_read_without_raw_decode(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "camera.nef"
    exif = Image.Exif()
    exif[36867] = "2026:05:06 14:23:10"
    Image.new("RGB", (10, 8)).save(raw, format="TIFF", exif=exif)
    import rawpy
    monkeypatch.setattr(rawpy, "imread", lambda *args: (_ for _ in ()).throw(AssertionError("RAW decoded")))
    capture = read_capture_time(raw, raw.stat().st_mtime)
    assert capture.source == "exif_datetime_original"


def test_cached_capture_survives_refresh_and_invalidates_on_change(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    photo = root / "a.jpg"
    _photo(photo, {36867: "2026:05:06 14:23:10"})
    with FileIndex(tmp_path / "index.db") as database:
        Indexer(database).refresh(root)
        record = ensure_capture_time(next(database.query(SearchOptions(root))), database)
        database.commit()
        import seekdex.capture_time as module
        original = module.read_capture_time
        calls = []
        def counted(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        monkeypatch.setattr(module, "read_capture_time", counted)
        Indexer(database).refresh(root)
        again = ensure_capture_time(next(database.query(SearchOptions(root))), database)
        assert again.capture_time == record.capture_time and calls == []
        old_ns = photo.stat().st_mtime_ns
        _photo(photo, {36867: "2025:01:02 03:04:05"})
        os.utime(photo, ns=(old_ns + 2_000_000_000, old_ns + 2_000_000_000))
        Indexer(database).refresh(root)
        changed = next(database.query(SearchOptions(root)))
        assert changed.capture_time is None
        assert ensure_capture_time(changed, database).capture_time_text == "2025-01-02T03:04:05"
        assert len(calls) == 1


def test_capture_date_search_resolves_unknown_metadata(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    photo = root / "a.jpg"
    _photo(photo, {36867: "2025:01:02 03:04:05"})
    (root / "note.txt").touch()
    selected = datetime(2025, 1, 2).timestamp()
    db_path = tmp_path / "index.db"
    with FileIndex(db_path) as database:
        Indexer(database).refresh(root)
    worker = SearchThread(SearchOptions(root, modified_from=selected,
                          modified_before=selected + 86400, time_type="capture"), database_path=db_path)
    results, done = [], []
    worker.batch_ready.connect(lambda batch: results.extend(batch))
    worker.search_done.connect(lambda *args: done.append(args))
    worker.run()
    assert done == [(1, False, "")]
    assert results[0].path == photo and results[0].capture_time_source == "exif_datetime_original"


def test_database_migrates_original_files_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("""CREATE TABLE files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, path_key TEXT NOT NULL UNIQUE,
            parent_key TEXT NOT NULL, name TEXT NOT NULL, name_fold TEXT NOT NULL,
            extension TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
            mtime_ns INTEGER NOT NULL, is_image INTEGER NOT NULL, width INTEGER, height INTEGER,
            root_path TEXT NOT NULL, indexed_at REAL NOT NULL)""")
        connection.execute("INSERT INTO files VALUES (7, 'old.jpg', 'old.jpg', '.', 'old.jpg', 'old.jpg', 'jpg', 10, 1, 1000, 1, 30, 20, '.', 1)")
    with FileIndex(db_path) as database:
        row = database.connection.execute("SELECT * FROM files WHERE id=7").fetchone()
        assert row["width"] == 30 and row["height"] == 20
        assert row["capture_time"] is None and row["file_uid"]
        uid = row["file_uid"]
    with FileIndex(db_path) as database:
        assert database.get_by_id(7).file_uid == uid


def test_capture_filter_uses_original_camera_date_with_offset(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    photo = root / "a.jpg"
    _photo(photo, {36867: "2026:05:06 00:23:10", 36881: "+14:00"})
    selected = datetime(2026, 5, 6).timestamp()
    with FileIndex(tmp_path / "index.db") as database:
        Indexer(database).refresh(root)
        record = ensure_capture_time(next(database.query(SearchOptions(root))), database)
        from seekdex.worker import matches
        options = SearchOptions(root, modified_from=selected, modified_before=selected + 86400, time_type="capture")
        assert matches(record, options)
        assert len(list(database.query(options))) == 1

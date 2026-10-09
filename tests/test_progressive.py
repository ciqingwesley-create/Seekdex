from __future__ import annotations

import sqlite3
from pathlib import Path

from PIL import Image

from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.search import SearchOptions
from seekdex.worker import SearchThread, result_from_record


def _run(options: SearchOptions, db_path: Path, *, refresh: bool = False):
    batches = []
    done = []
    worker = SearchThread(options, refresh=refresh, database_path=db_path)
    worker.batch_ready.connect(lambda batch: batches.append(batch))
    worker.search_done.connect(lambda *args: done.append(args))
    worker.run()
    return batches, done


def test_unindexed_search_streams_before_scan_finishes_without_decoding(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "match.jpg"
    Image.new("RGB", (18, 12), "red").save(target)
    for number in range(300):
        (root / f"other-{number:04}.txt").touch()
    original = Path.iterdir
    finished = False

    def ordered(directory: Path):
        nonlocal finished
        if directory != root:
            yield from original(directory)
            return
        yield target
        for entry in sorted(original(directory)):
            if entry != target:
                yield entry
        finished = True

    monkeypatch.setattr(Path, "iterdir", ordered)
    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("scan decoded an image")
    ))
    db_path = tmp_path / "app" / "index.db"
    early = []
    worker = SearchThread(SearchOptions(root, filename="match"), database_path=db_path)
    worker.batch_ready.connect(lambda batch: early.append((batch, finished)))
    worker.run()
    assert early and early[0][1] is False
    assert early[0][0][0].width is None and early[0][0][0].thumbnail is None
    with FileIndex(db_path) as database:
        assert len(list(database.query(SearchOptions(root)))) == 301
        assert database.coverage_state(root, True) == "complete"


def test_partial_index_returns_saved_hits_before_resuming(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for number in range(30):
        (root / f"item-{number:02}.txt").touch()
    db_path = tmp_path / "app" / "index.db"
    checks = 0

    def cancel_soon() -> bool:
        nonlocal checks
        checks += 1
        return checks > 8

    with FileIndex(db_path) as database:
        assert Indexer(database).refresh(root, cancel_soon).cancelled
        assert database.coverage_state(root, True) == "partial"
        saved = len(list(database.query(SearchOptions(root))))
        assert 0 < saved < 30

    batches = []
    original = Indexer.refresh

    def checked_refresh(self, *args, **kwargs):
        assert batches, "partial rows should be emitted before scanning resumes"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Indexer, "refresh", checked_refresh)
    worker = SearchThread(SearchOptions(root), database_path=db_path)
    worker.batch_ready.connect(lambda batch: batches.extend(batch))
    worker.run()
    assert len(batches) == 30
    assert len({result.path for result in batches}) == 30
    with FileIndex(db_path) as database:
        assert database.coverage_state(root, True) == "complete"


def test_search_cancel_keeps_partial_database_usable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for number in range(50):
        (root / f"hit-{number:02}.txt").touch()
    db_path = tmp_path / "app" / "index.db"
    worker = SearchThread(SearchOptions(root), database_path=db_path)
    batches = []
    done = []

    def cancel_after_first(batch):
        batches.extend(batch)
        worker.cancel()

    worker.batch_ready.connect(cancel_after_first)
    worker.search_done.connect(lambda *args: done.append(args))
    worker.run()
    assert done and done[0][1] is True and done[0][2] == ""
    with FileIndex(db_path) as database:
        assert database.coverage_state(root, True) == "partial"
        assert len(list(database.query(SearchOptions(root)))) >= 1
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _, completed = _run(SearchOptions(root), db_path)
    assert completed == [(50, False, "")]


def test_complete_index_search_skips_filesystem_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").touch()
    db_path = tmp_path / "app" / "index.db"
    with FileIndex(db_path) as database:
        assert Indexer(database).refresh(root).completed
    monkeypatch.setattr(Indexer, "refresh", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("complete index was rescanned")
    ))
    batches, done = _run(SearchOptions(root), db_path)
    assert sum(map(len, batches)) == 1
    assert done == [(1, False, "")]


def test_refresh_removes_stale_displayed_results(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    gone = root / "gone.txt"
    gone.touch()
    db_path = tmp_path / "app" / "index.db"
    with FileIndex(db_path) as database:
        Indexer(database).refresh(root)
    gone.unlink()
    worker = SearchThread(SearchOptions(root), refresh=True, database_path=db_path)
    shown = []
    removed = []
    done = []
    worker.batch_ready.connect(lambda batch: shown.extend(batch))
    worker.paths_removed.connect(lambda keys: removed.extend(keys))
    worker.search_done.connect(lambda *args: done.append(args))
    worker.run()
    assert len(shown) == 1 and len(removed) == 1
    assert done == [(0, False, "")]
    with FileIndex(db_path) as database:
        assert list(database.query(SearchOptions(root))) == []


def test_direct_only_scan_does_not_visit_descendants(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.txt").touch()
    (sub / "b.txt").touch()
    original = Path.iterdir

    def guarded(directory: Path):
        if directory == sub:
            raise AssertionError("direct-only scan visited child directory")
        return original(directory)

    monkeypatch.setattr(Path, "iterdir", guarded)
    db_path = tmp_path / "app" / "index.db"
    batches, done = _run(SearchOptions(root, recursive=False), db_path)
    assert done == [(1, False, "")]
    assert sum(map(len, batches)) == 1
    with FileIndex(db_path) as database:
        assert database.coverage_state(root, False) == "complete"
        assert database.coverage_state(root, True) == "partial"


def test_old_completed_root_schema_migrates_without_deleting_index(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE roots (root_key TEXT PRIMARY KEY, root_path TEXT NOT NULL, last_completed_at REAL NOT NULL)"
        )
        from seekdex.paths import path_key
        connection.execute("INSERT INTO roots VALUES (?, ?, ?)", (path_key(root), str(root), 1.0))
    with FileIndex(db_path) as database:
        assert database.coverage_state(root, True) == "complete"
        assert database.connection.execute("SELECT state, recursive FROM roots").fetchone()[:] == ("complete", 1)


def test_gui_queues_only_visible_thumbnails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from seekdex.ui import MainWindow

    root = tmp_path / "photos"
    root.mkdir()
    for number in range(100):
        Image.new("RGB", (24, 16), "red").save(root / f"photo-{number:03}.jpg")
    db_path = tmp_path / "app" / "index.db"
    cache_dir = tmp_path / "cache"
    with FileIndex(db_path) as database:
        Indexer(database).refresh(root)
        rows = [result_from_record(row) for row in database.query(SearchOptions(root))]

    app = QApplication.instance() or QApplication([])
    window = MainWindow(database_path=db_path, cache_dir=cache_dir)
    try:
        window.show()
        app.processEvents()
        window.model.append(rows)
        window._loading_enabled = True
        window._generation += 1
        window._queue_visible()
        app.processEvents()
        with window._image_loader._condition:
            queued = len(window._image_loader._pending) + len(window._image_loader._done)
        assert 0 < queued < 30
        assert len(list(cache_dir.glob("*.jpg"))) < 30
    finally:
        window.close()
        app.processEvents()

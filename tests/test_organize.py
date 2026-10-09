from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from seekdex.index.database import FileIndex
from seekdex.organize.engine import Organizer
from seekdex.organize.models import DEFAULT_TEMPLATE
from seekdex.organize.planner import prepare_plan
from seekdex.organize.templates import render_relative, safe_target, validate_template
from seekdex.paths import path_key
from seekdex.thumbnail_cache import ThumbnailCache


def _source(tmp_path: Path, name: str = "IMG.jpg") -> Path:
    folder = tmp_path / "source"
    folder.mkdir(exist_ok=True)
    path = folder / name
    if path.suffix == ".jpg":
        exif = Image.Exif()
        exif[34665] = {36867: "2026:05:06 14:23:10"}
        Image.new("RGB", (32, 20), "red").save(path, exif=exif)
    else:
        path.write_bytes(b"ordinary content")
    return path


def test_default_template_and_padding() -> None:
    date = datetime(2026, 5, 6, 14, 23, 10)
    assert render_relative(DEFAULT_TEMPLATE, date, Path("DSC_1234.NEF")) == Path("2026y/5m/6d/DSC_1234.NEF")
    assert render_relative("{year}/{month:02}/{day:02}/{stem}{ext}", date, Path("DSC_1234.NEF")) == Path("2026/05/06/DSC_1234.NEF")
    assert render_relative("{hour:02}{minute:02}{second:02}/{filename}", date, Path("a.jpg")) == Path("142310/a.jpg")


@pytest.mark.parametrize("template", ["", "../{filename}", "/{filename}", "C:/{filename}",
    "{year.__class__}/{filename}", "{filename[0]}", "{filename!r}", "{unknown}",
    "{month:100000}", "{month:{day}}", "{year}/../{filename}", "CON/{filename}",
    "a./{filename}", "//server/share/{filename}", "{filename}:ads", "a//{filename}"])
def test_unsafe_templates_rejected(template: str) -> None:
    with pytest.raises(ValueError):
        validate_template(template)


def test_target_escape_and_symlink_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "dest"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_target(root, Path("../escape.jpg"))
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows may lack symlink privilege. Simulate pathlib's resolved link target.
        original = Path.resolve
        def linked_resolution(path: Path, *args, **kwargs):
            if path.is_relative_to(root / "linked"):
                return outside / path.relative_to(root / "linked")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", linked_resolution)
    with pytest.raises(ValueError):
        safe_target(root, Path("linked/a.jpg"))


def test_conflict_numbering_existing_and_same_batch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    second = tmp_path / "other" / source.name
    second.parent.mkdir()
    second.write_bytes(source.read_bytes())
    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / source.name).write_bytes(b"existing")
    with FileIndex(tmp_path / "index.db") as database:
        plan = prepare_plan([source, second], destination, database, template="{filename}", conflict_policy="rename")
        assert [item.target.name for item in plan.items] == ["IMG_1.jpg", "IMG_2.jpg"]
        assert all(item.status == "ready" and item.conflict for item in plan.items)


def test_skip_conflict_and_dry_run_do_not_change_files(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "dest"
    destination.mkdir()
    existing = destination / source.name
    existing.write_bytes(b"keep")
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], destination, database, action="move", template="{filename}")
        assert plan.items[0].status == "skip"
        assert organizer.execute(plan)[0].status == "preview"
        assert organizer.history() == []
        assert organizer.execute(plan, dry_run=False)[0].status == "skipped"
        assert source.is_file() and existing.read_bytes() == b"keep"
        fresh = prepare_plan([source], tmp_path / "new-dest", database, action="move")
        organizer.execute(fresh)
        assert source.is_file() and not fresh.target_root.exists()


def test_move_updates_stable_id_and_reuses_cache_then_undo(tmp_path: Path) -> None:
    source = _source(tmp_path)
    original_content = source.read_bytes()
    original_stat = source.stat()
    with FileIndex(tmp_path / "index.db") as database:
        cache = ThumbnailCache(tmp_path / "cache")
        organizer = Organizer(database, cache)
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        before = database.get_by_key(path_key(source))
        cache.get_or_create(source, original_stat.st_mtime_ns, original_stat.st_size, lambda: b"\xff\xd8cached")
        target = plan.items[0].target
        outcome = organizer.execute(plan, dry_run=False)[0]
        assert outcome.status == "success"
        assert not source.exists() and target.read_bytes() == original_content
        assert target.stat().st_mtime_ns == original_stat.st_mtime_ns
        moved = database.get_by_key(path_key(target))
        assert moved.id == before.id and moved.file_uid == before.file_uid
        assert database.get_by_key(path_key(source)) is None
        assert cache.cache_path(target, original_stat.st_mtime_ns, original_stat.st_size).is_file()
        assert organizer.undo(outcome.operation_id).status == "success"
        assert source.read_bytes() == original_content and not target.exists()
        restored = database.get_by_key(path_key(source))
        assert restored.id == before.id and restored.file_uid == before.file_uid
        original_log = next(row for row in organizer.history() if row["id"] == outcome.operation_id)
        assert original_log["success"] == 1 and original_log["undo_status"] == "success"


def test_copy_creates_independent_record_and_safe_delete(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database)
        source_record = database.get_by_key(path_key(source))
        outcome = organizer.execute(plan, dry_run=False)[0]
        target = plan.items[0].target
        assert outcome.status == "success" and target.read_bytes() == source.read_bytes()
        copied = database.get_by_key(path_key(target))
        assert copied.id != source_record.id and copied.file_uid != source_record.file_uid
        assert target.stat().st_ino != source.stat().st_ino
        assert copied.capture_time == source_record.capture_time
        assert organizer.undo(outcome.operation_id).status == "success"
        assert source.is_file() and not target.exists()
        assert database.get_by_key(path_key(target)) is None


def test_undo_move_conflict_never_overwrites(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        outcome = organizer.execute(plan, dry_run=False)[0]
        source.write_bytes(b"new file")
        undo = organizer.undo(outcome.operation_id)
        assert undo.status == "failed" and "同名" in undo.message
        assert source.read_bytes() == b"new file" and plan.items[0].target.is_file()


def test_modified_copy_cannot_be_deleted_by_undo(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database)
        outcome = organizer.execute(plan, dry_run=False)[0]
        target = plan.items[0].target
        stat = target.stat()
        content = target.read_bytes()
        target.write_bytes(bytes([content[0] ^ 1]) + content[1:])
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert organizer.undo(outcome.operation_id).status == "failed"
        assert target.exists()


def test_target_appearing_after_preview_is_preserved(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        plan = prepare_plan([source], tmp_path / "dest", database, action="move", template="{filename}")
        target = plan.items[0].target
        target.parent.mkdir()
        target.write_bytes(b"arrived after preview")
        outcome = Organizer(database, ThumbnailCache(tmp_path / "cache")).execute(plan, dry_run=False)[0]
        assert outcome.status == "failed" and source.exists()
        assert target.read_bytes() == b"arrived after preview"


def test_one_file_failure_does_not_stop_batch(tmp_path: Path, monkeypatch) -> None:
    first = _source(tmp_path, "a.txt")
    second = _source(tmp_path, "b.txt")
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([first, second], tmp_path / "dest", database, action="move", template="{filename}")
        original = Path.unlink
        def guarded(path: Path, *args, **kwargs):
            if path == first:
                raise PermissionError("locked source")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", guarded)
        outcomes = organizer.execute(plan, dry_run=False)
        assert [item.status for item in outcomes] == ["failed", "success"]
        assert first.exists() and not second.exists()
        assert not plan.items[0].target.exists() and plan.items[1].target.exists()
        assert len(organizer.history()) == 2


def test_database_failure_after_move_restores_source(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        before = database.get_by_key(path_key(source))
        monkeypatch.setattr(organizer, "_sync_index", lambda *args: (_ for _ in ()).throw(OSError("database unavailable")))
        outcome = organizer.execute(plan, dry_run=False)[0]
        assert outcome.status == "failed" and source.is_file()
        assert not plan.items[0].target.exists()
        assert database.get_by_id(before.id).path == source


def test_cross_volume_move_fallback_and_cancel(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    second = _source(tmp_path, "second.txt")
    import seekdex.organize.engine as module
    monkeypatch.setattr(module.os, "link", lambda *args: (_ for _ in ()).throw(OSError("cross-device")))
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source, second], tmp_path / "dest", database, action="move", template="{filename}")
        finished = []
        outcomes = organizer.execute(plan, dry_run=False, cancelled=lambda: bool(finished),
                                     progress=lambda *args: finished.append(True))
        assert len(outcomes) == 1 and outcomes[0].status == "success"
        assert not source.exists() and second.exists()


def test_unchanged_preview_reuses_capture_time(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        first = prepare_plan([source], tmp_path / "dest", database)
        import seekdex.capture_time as module
        calls = []
        def counted(*args, **kwargs):
            calls.append(True)
            raise AssertionError("should not re-read EXIF")
        monkeypatch.setattr(module, "read_capture_time", counted)
        second = prepare_plan([source], tmp_path / "dest", database)
        assert first.items[0].capture_time == second.items[0].capture_time
        assert second.items[0].status == "ready" and calls == []


def test_transaction_failure_does_not_commit_partial_relocation(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db", batch_size=1) as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        original_status = organizer._status
        def interrupted_status(operation_id, status, *args, **kwargs):
            if status == "success":
                raise OSError("journal finalization failed")
            return original_status(operation_id, status, *args, **kwargs)
        monkeypatch.setattr(organizer, "_status", interrupted_status)
        outcome = organizer.execute(plan, dry_run=False)[0]
        assert outcome.status == "failed" and source.exists()
        assert database.get_by_key(path_key(source)) is not None
        assert database.get_by_key(path_key(plan.items[0].target)) is None


def test_replaced_copy_with_same_contents_cannot_be_deleted(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database)
        result = organizer.execute(plan, dry_run=False)[0]
        target = plan.items[0].target
        stat, content = target.stat(), target.read_bytes()
        replacement = target.with_name("replacement.jpg")
        replacement.write_bytes(content)
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, target)
        assert organizer.undo(result.operation_id).status == "failed"
        assert target.exists()


def test_source_change_after_preview_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        source.write_bytes(b"edited")
        result = organizer.execute(plan, dry_run=False)[0]
        assert result.status == "failed" and source.read_bytes() == b"edited"
        assert not plan.items[0].target.exists()


def test_same_preview_cannot_execute_twice(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database)
        assert organizer.execute(plan, dry_run=False)[0].status == "success"
        with pytest.raises(ValueError):
            organizer.execute(plan, dry_run=False)


def test_interrupted_completed_move_recovers_index(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    db_path = tmp_path / "index.db"
    with FileIndex(db_path) as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        original_id = plan.items[0].file_id
        def power_loss(*args):
            raise SystemExit("simulated shutdown after source unlink")
        monkeypatch.setattr(organizer, "_sync_index", power_loss)
        with pytest.raises(SystemExit):
            organizer.execute(plan, dry_run=False)
        assert not source.exists() and plan.items[0].target.exists()
    with FileIndex(db_path) as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        organizer.recover_interrupted()
        assert database.get_by_id(original_id).path == plan.items[0].target
        assert organizer.history()[0]["status"] == "success"


def test_simultaneous_organization_is_blocked_without_changes(tmp_path: Path) -> None:
    from PySide6.QtCore import QLockFile
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db") as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database, action="move")
        lock = QLockFile(str(tmp_path / "index.db.organize.lock"))
        lock.setStaleLockTime(0)
        assert lock.tryLock(0)
        try:
            with pytest.raises(RuntimeError):
                organizer.execute(plan, dry_run=False)
            assert source.exists() and not plan.items[0].target.exists()
        finally:
            lock.unlock()


def test_copy_delete_index_failure_can_be_reconciled(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    with FileIndex(tmp_path / "index.db", batch_size=1) as database:
        organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
        plan = prepare_plan([source], tmp_path / "dest", database)
        outcome = organizer.execute(plan, dry_run=False)[0]
        original_status = organizer._status
        def fail_finalize(operation_id, status, *args, **kwargs):
            if status == "success":
                raise OSError("failed to finalize deletion journal")
            return original_status(operation_id, status, *args, **kwargs)
        monkeypatch.setattr(organizer, "_status", fail_finalize)
        assert organizer.undo(outcome.operation_id).status == "failed"
        assert source.is_file() and not plan.items[0].target.exists()
        assert database.get_by_key(path_key(plan.items[0].target)) is not None
        monkeypatch.setattr(organizer, "_status", original_status)
        organizer.recover_interrupted()
        assert database.get_by_key(path_key(plan.items[0].target)) is None
        original = next(entry for entry in organizer.history() if entry["id"] == outcome.operation_id)
        assert original["undo_status"] == "success"

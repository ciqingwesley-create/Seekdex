"""Lightweight, progressive filesystem indexing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from time import time

from ..paths import path_key
from ..capture_time import read_capture_time
from ..search import is_image_path
from .database import FileIndex
from .models import FileRecord, ScanStats


class Indexer:
    def __init__(self, database: FileIndex) -> None:
        self.database = database

    def refresh(
        self,
        root: Path,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[ScanStats], None] | None = None,
        on_record: Callable[[FileRecord], None] | None = None,
        recursive: bool = True,
        on_deleted: Callable[[str], None] | None = None,
    ) -> ScanStats:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        stats = ScanStats()
        pending = [root]
        visited: set[str] = set()
        inaccessible: set[str] = set()
        self.database.mark_root_partial(root, recursive)

        while pending:
            if cancelled():
                stats.cancelled = True
                self.database.commit()
                return stats
            directory = pending.pop()
            parent_key = path_key(directory)
            previous = self.database.records_in_parent(parent_key)
            complete_directory = True
            try:
                for entry in directory.iterdir():
                    if cancelled():
                        stats.cancelled = True
                        self.database.commit()
                        return stats
                    try:
                        stat = entry.lstat()
                        if S_ISDIR(stat.st_mode):
                            if recursive:
                                pending.append(entry)
                            continue
                        if not S_ISREG(stat.st_mode):
                            continue
                    except OSError:
                        stats.skipped += 1
                        complete_directory = False
                        continue

                    stats.scanned += 1
                    key = path_key(entry)
                    old = previous.pop(key, None)
                    extension = entry.suffix.lower().removeprefix(".")
                    same_file = (
                        old is not None
                        and old.mtime_ns == stat.st_mtime_ns
                        and old.size == stat.st_size
                        and old.name == entry.name
                        and old.extension == extension
                    )
                    if same_file:
                        if old.path != entry or old.root_path != root:
                            record = replace(old, path=entry, root_path=root, indexed_at=time())
                            self.database.upsert(record)
                            stats.updated += 1
                        else:
                            record = old
                            stats.unchanged += 1
                    else:
                        # Image decoding belongs to the visible-result loader.
                        image = is_image_path(entry)
                        fallback = read_capture_time(entry, stat.st_mtime, False) if not image else None
                        record = FileRecord(
                            path=entry, name=entry.name, extension=extension,
                            size=stat.st_size, mtime=stat.st_mtime,
                            mtime_ns=stat.st_mtime_ns, is_image=image,
                            width=None, height=None, root_path=root,
                            indexed_at=time(), id=old.id if old else None,
                            capture_time=fallback.timestamp if fallback else None,
                            capture_time_source=fallback.source if fallback else None,
                            capture_time_text=fallback.text if fallback else None,
                        )
                        self.database.upsert(record)
                        if old is None:
                            stats.added += 1
                        else:
                            stats.updated += 1
                    if on_record is not None:
                        on_record(record)
                    if progress is not None and stats.scanned % 100 == 0:
                        progress(stats)
            except OSError:
                complete_directory = False
                stats.skipped += 1

            if complete_directory:
                visited.add(parent_key)
                for stale_key in previous:
                    stats.deleted += self.database.delete_path(stale_key)
                    if on_deleted is not None:
                        on_deleted(stale_key)
            else:
                inaccessible.add(parent_key)

        if recursive:
            failed_paths = [PurePosixPath(key) for key in inaccessible]
            for parent_key in self.database.parent_keys_under(root):
                if parent_key in visited:
                    continue
                parent = PurePosixPath(parent_key)
                if any(parent.is_relative_to(failed) for failed in failed_paths):
                    continue
                stale_keys = self.database.records_in_parent(parent_key)
                stats.deleted += self.database.delete_parent(parent_key)
                if on_deleted is not None:
                    for stale_key in stale_keys:
                        on_deleted(stale_key)

        if not inaccessible:
            self.database.mark_root_complete(root, recursive)
            stats.completed = True
        self.database.commit()
        if progress is not None:
            progress(stats)
        return stats

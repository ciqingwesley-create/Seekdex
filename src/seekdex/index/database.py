"""SQLite schema and file queries. One connection belongs to one worker thread."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from time import time
from uuid import uuid4

from ..paths import descendant_bounds, path_key, unique_roots
from ..search import SearchOptions
from ..image_filters import image_filter_sql
from ..image_metadata import camera_search_text, camera_display_name
from .models import FileRecord


class FileIndex:
    def __init__(self, database_path: Path, batch_size: int = 500) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._pending = 0
        self._batch_size = batch_size
        self._transaction_depth = 0
        self._create_schema()

    def __enter__(self) -> FileIndex:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                path_key TEXT NOT NULL UNIQUE,
                parent_key TEXT NOT NULL,
                name TEXT NOT NULL,
                name_fold TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                mtime_ns INTEGER NOT NULL,
                is_image INTEGER NOT NULL,
                width INTEGER,
                height INTEGER,
                root_path TEXT NOT NULL,
                indexed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roots (
                root_key TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                last_completed_at REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'complete',
                recursive INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_files_name_fold ON files(name_fold);
            CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension);
            CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
            CREATE INDEX IF NOT EXISTS idx_files_root_path ON files(root_path);
            CREATE INDEX IF NOT EXISTS idx_files_parent_key ON files(parent_key);
            CREATE INDEX IF NOT EXISTS idx_files_parent_ext_time
                ON files(parent_key, extension, mtime);
            """
        )
        # Serialize migrations across the search, thumbnail and status connections.
        self.connection.execute("BEGIN IMMEDIATE")
        # Databases from the previous release contain only completed recursive roots.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(roots)")}
        if "state" not in columns:
            self.connection.execute("ALTER TABLE roots ADD COLUMN state TEXT NOT NULL DEFAULT 'complete'")
        if "recursive" not in columns:
            self.connection.execute("ALTER TABLE roots ADD COLUMN recursive INTEGER NOT NULL DEFAULT 1")
        file_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(files)")}
        for name, kind in (("capture_time", "REAL"), ("capture_time_source", "TEXT"),
                           ("capture_time_text", "TEXT"), ("file_uid", "TEXT"),
                           ("camera_make", "TEXT"), ("camera_model", "TEXT"),
                           ("camera_search_text", "TEXT NOT NULL DEFAULT ''"),
                           ("metadata_version", "INTEGER NOT NULL DEFAULT 0"), ("metadata_error", "TEXT")):
            if name not in file_columns:
                self.connection.execute(f"ALTER TABLE files ADD COLUMN {name} {kind}")
        for row in self.connection.execute("SELECT id FROM files WHERE file_uid IS NULL").fetchall():
            self.connection.execute("UPDATE files SET file_uid=? WHERE id=?", (uuid4().hex, row[0]))
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_uid ON files(file_uid)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_files_capture_time ON files(capture_time)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_images_width ON files(width) WHERE is_image=1")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_images_height ON files(height) WHERE is_image=1")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS organize_batches (
                id TEXT PRIMARY KEY, created_at REAL NOT NULL, action TEXT NOT NULL,
                target_root TEXT NOT NULL, template TEXT NOT NULL, conflict_policy TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_operations (
                id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, file_id INTEGER,
                source_path TEXT NOT NULL, target_path TEXT NOT NULL,
                operation TEXT NOT NULL, operated_at REAL NOT NULL,
                status TEXT NOT NULL, success INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', size INTEGER, mtime_ns INTEGER,
                sha256 TEXT, target_dev TEXT, target_ino TEXT,
                undone_at REAL, undo_status TEXT, undo_error TEXT,
                related_operation_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_operations_batch ON file_operations(batch_id);
            CREATE TABLE IF NOT EXISTS image_embeddings (
                file_uid TEXT NOT NULL REFERENCES files(file_uid) ON DELETE CASCADE,
                model_id TEXT NOT NULL, embedding BLOB NOT NULL,
                embedding_dimension INTEGER NOT NULL, dtype TEXT NOT NULL DEFAULT 'float32',
                indexed_at REAL NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                PRIMARY KEY (file_uid, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_embeddings_model ON image_embeddings(model_id);
            CREATE TABLE IF NOT EXISTS ai_state (
                id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO ai_state(id, revision) VALUES (1, 0);
            CREATE TRIGGER IF NOT EXISTS embedding_insert AFTER INSERT ON image_embeddings
                BEGIN UPDATE ai_state SET revision=revision+1 WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS embedding_update AFTER UPDATE ON image_embeddings
                BEGIN UPDATE ai_state SET revision=revision+1 WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS embedding_delete AFTER DELETE ON image_embeddings
                BEGIN UPDATE ai_state SET revision=revision+1 WHERE id=1; END;
            CREATE TRIGGER IF NOT EXISTS file_embedding_invalidate AFTER UPDATE OF size,mtime_ns ON files
                WHEN OLD.size!=NEW.size OR OLD.mtime_ns!=NEW.mtime_ns
                BEGIN DELETE FROM image_embeddings WHERE file_uid=NEW.file_uid; END;
        """)
        self.connection.commit()

        if not self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='indexed_directories'").fetchone():
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS indexed_directories (
                    root_key TEXT PRIMARY KEY, root_path TEXT NOT NULL, recursive INTEGER NOT NULL DEFAULT 1,
                    added_at REAL NOT NULL, scan_status TEXT NOT NULL DEFAULT 'not_indexed',
                    ai_status TEXT NOT NULL DEFAULT 'not_indexed', ocr_status TEXT NOT NULL DEFAULT 'not_indexed',
                    last_error TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO indexed_directories(root_key,root_path,recursive,added_at,scan_status)
                    SELECT root_key,root_path,recursive,last_completed_at,state FROM roots;
            """)

        from ..ocr.store import create_schema
        self.ocr_available = create_schema(self.connection)
        self.connection.commit()

    def _changed(self) -> None:
        self._pending += 1
        if self._pending >= self._batch_size and not self._transaction_depth:
            self.commit()

    @contextmanager
    def transaction(self):
        self.commit()
        self._transaction_depth += 1
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.commit()
        except BaseException:
            self.connection.rollback()
            self._pending = 0
            raise
        finally:
            self._transaction_depth -= 1

    def commit(self) -> None:
        self.connection.commit()
        self._pending = 0

    def upsert(self, record: FileRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO files (
                path, path_key, parent_key, name, name_fold, extension, size, mtime,
                mtime_ns, is_image, width, height, root_path, indexed_at,
                capture_time, capture_time_source, capture_time_text, file_uid,
                camera_make, camera_model, camera_search_text, metadata_version, metadata_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path_key) DO UPDATE SET
                path=excluded.path, parent_key=excluded.parent_key,
                name=excluded.name, name_fold=excluded.name_fold,
                extension=excluded.extension, size=excluded.size,
                mtime=excluded.mtime, mtime_ns=excluded.mtime_ns,
                is_image=excluded.is_image, width=excluded.width,
                height=excluded.height, root_path=excluded.root_path,
                indexed_at=excluded.indexed_at, capture_time=excluded.capture_time,
                capture_time_source=excluded.capture_time_source,
                capture_time_text=excluded.capture_time_text, camera_make=excluded.camera_make,
                camera_model=excluded.camera_model, camera_search_text=excluded.camera_search_text,
                metadata_version=excluded.metadata_version, metadata_error=excluded.metadata_error
            """,
            (
                str(record.path), path_key(record.path), path_key(record.path.parent),
                record.name, record.name.casefold(), record.extension, record.size,
                record.mtime, record.mtime_ns, int(record.is_image), record.width,
                record.height, str(record.root_path), record.indexed_at,
                record.capture_time, record.capture_time_source, record.capture_time_text,
                record.file_uid or uuid4().hex,
                record.camera_make, record.camera_model, camera_search_text(record.camera_make, record.camera_model),
                record.metadata_version, record.metadata_error,
            ),
        )
        self._changed()

    def delete_path(self, key: str) -> int:
        count = self.connection.execute("DELETE FROM files WHERE path_key=?", (key,)).rowcount
        if count:
            self._changed()
        return count

    def delete_parent(self, parent_key: str) -> int:
        count = self.connection.execute(
            "DELETE FROM files WHERE parent_key=?", (parent_key,)
        ).rowcount
        if count:
            self._changed()
        return count

    def records_in_parent(self, parent_key: str) -> dict[str, FileRecord]:
        rows = self.connection.execute(
            "SELECT * FROM files WHERE parent_key=?", (parent_key,)
        )
        return {row["path_key"]: _record(row) for row in rows}

    def parent_keys_under(self, folder: Path) -> set[str]:
        key = path_key(folder)
        low, high = descendant_bounds(key)
        rows = self.connection.execute(
            """SELECT DISTINCT parent_key FROM files
               WHERE parent_key=? OR (parent_key>=? AND parent_key<?)""",
            (key, low, high),
        )
        return {row[0] for row in rows}

    def has_coverage(self, folder: Path) -> bool:
        return self.coverage_state(folder, True) == "complete"

    def coverage_state(self, folder: Path, recursive: bool) -> str:
        folder_key = PurePosixPath(path_key(folder))
        exact: str | None = None
        ancestor_complete = False
        for row in self.connection.execute("SELECT root_key, state, recursive FROM roots"):
            root_key = PurePosixPath(row[0])
            if folder_key == root_key:
                if row["state"] == "complete" and (row["recursive"] or not recursive):
                    exact = "complete"
                else:
                    exact = "partial"
            elif folder_key.is_relative_to(root_key) and row["recursive"] and row["state"] == "complete":
                ancestor_complete = True
        return exact or ("complete" if ancestor_complete else "not_indexed")

    def mark_root_partial(self, root: Path, recursive: bool = True) -> None:
        self.connection.execute(
            """INSERT INTO roots (root_key, root_path, last_completed_at, state, recursive)
               VALUES (?, ?, 0, 'partial', ?)
               ON CONFLICT(root_key) DO UPDATE SET root_path=excluded.root_path,
                 state='partial', recursive=excluded.recursive""",
            (path_key(root), str(root), int(recursive)),
        )
        self.commit()

    def mark_root_complete(self, root: Path, recursive: bool = True) -> None:
        self.connection.execute(
            """INSERT INTO roots (root_key, root_path, last_completed_at, state, recursive)
               VALUES (?, ?, ?, 'complete', ?)
               ON CONFLICT(root_key) DO UPDATE SET
                 root_path=excluded.root_path,
                 last_completed_at=excluded.last_completed_at,
                 state='complete', recursive=excluded.recursive""",
            (path_key(root), str(root), time(), int(recursive)),
        )
        self._changed()

    def get_by_key(self, key: str) -> FileRecord | None:
        row = self.connection.execute("SELECT * FROM files WHERE path_key=?", (key,)).fetchone()
        return _record(row) if row else None

    def get_by_id(self, file_id: int) -> FileRecord | None:
        row = self.connection.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return _record(row) if row else None

    def update_capture_time(self, record: FileRecord) -> None:
        self.connection.execute(
            """UPDATE files SET capture_time=?, capture_time_source=?, capture_time_text=?
               WHERE path_key=? AND mtime_ns=? AND size=?""",
            (record.capture_time, record.capture_time_source, record.capture_time_text,
             path_key(record.path), record.mtime_ns, record.size),
        )
        self._changed()

    def relocate(self, file_id: int, target: Path, root: Path) -> None:
        stat = target.stat()
        # Only the location changes: internal ID/UID and future associated data survive.
        self.connection.execute(
            """UPDATE files SET path=?, path_key=?, parent_key=?, name=?, name_fold=?,
               extension=?, size=?, mtime=?, mtime_ns=?, root_path=?, indexed_at=? WHERE id=?""",
            (str(target), path_key(target), path_key(target.parent), target.name,
             target.name.casefold(), target.suffix.lower().lstrip('.'), stat.st_size,
             stat.st_mtime, stat.st_mtime_ns, str(root), time(), file_id),
        )
        self._changed()

    def update_image_details(
        self, key: str, mtime_ns: int, size: int, width: int, height: int
    ) -> bool:
        count = self.connection.execute(
            """UPDATE files SET width=?, height=? WHERE path_key=?
               AND mtime_ns=? AND size=? AND is_image=1""",
            (width, height, key, mtime_ns, size),
        ).rowcount
        if count:
            self._changed()
        return bool(count)

    def update_image_metadata(self, record: FileRecord) -> bool:
        count = self.connection.execute(
            """UPDATE files SET width=?, height=?, camera_make=?, camera_model=?, camera_search_text=?,
               metadata_version=?, metadata_error=?, capture_time=?, capture_time_source=?, capture_time_text=?
               WHERE path_key=? AND mtime_ns=? AND size=? AND is_image=1
               AND (? IS NULL OR file_uid=?)""",
            (record.width, record.height, record.camera_make, record.camera_model,
             camera_search_text(record.camera_make, record.camera_model), record.metadata_version,
             record.metadata_error, record.capture_time, record.capture_time_source, record.capture_time_text,
             path_key(record.path), record.mtime_ns, record.size, record.file_uid, record.file_uid),
        ).rowcount
        if count:
            self._changed()
        return bool(count)

    def metadata_status(self, options: SearchOptions, version: int) -> tuple[list[str], int, int]:
        clauses, params = self._where(options.folder_scope())
        where = " AND ".join(clauses+["is_image=1"])
        total, pending = self.connection.execute(
            "SELECT count(*), COALESCE(sum(metadata_version<?),0) FROM files WHERE "+where,
            [version]+params).fetchone()
        devices = {camera_display_name(row[0], row[1]) for row in self.connection.execute(
            "SELECT DISTINCT camera_make,camera_model FROM files WHERE "+where+
            " AND (camera_make IS NOT NULL OR camera_model IS NOT NULL)", params)}
        return sorted(devices, key=str.casefold), pending, total

    def metadata_candidates(self, options: SearchOptions, version: int) -> Iterator[FileRecord]:
        clauses, params = self._where(options.folder_scope())
        where = " AND ".join(clauses+["is_image=1", "metadata_version<?", "id>?"])
        last_id = -1
        # Keyset pages permit batch commits without holding an active SELECT cursor.
        while rows := self.connection.execute("SELECT * FROM files WHERE "+where+
            " ORDER BY id LIMIT 100", params+[version, last_id]).fetchall():
            for row in rows:
                last_id = row["id"]
                yield _record(row)

    def _where(self, options: SearchOptions) -> tuple[list[str], list[object]]:
        scope, params = [], []
        for root in unique_roots(options.folders or (options.folder,), options.recursive):
            key = path_key(root)
            if options.recursive:
                low, high = descendant_bounds(key)
                scope.append("(parent_key=? OR (parent_key>=? AND parent_key<?))")
                params.extend((key, low, high))
            else:
                scope.append("parent_key=?")
                params.append(key)
        clauses = ["(" + " OR ".join(scope) + ")" if scope else "0"]
        if name := options.filename.casefold().strip():
            clauses.append("instr(name_fold, ?) > 0")
            params.append(name)
        if options.extensions:
            marks = ", ".join("?" for _ in options.extensions)
            clauses.append(f"extension IN ({marks})")
            params.extend(sorted(options.extensions))
        capture = options.time_type == "capture"
        time_column = "COALESCE(substr(capture_time_text, 1, 19), strftime('%Y-%m-%dT%H:%M:%S', mtime, 'unixepoch', 'localtime'))" if capture else "mtime"
        if options.modified_from is not None:
            clauses.append(time_column + ">=?")
            params.append(datetime.fromtimestamp(options.modified_from).isoformat(timespec="seconds") if capture else options.modified_from)
        if options.modified_before is not None:
            clauses.append(time_column + "<?")
            params.append(datetime.fromtimestamp(options.modified_before).isoformat(timespec="seconds") if capture else options.modified_before)
        extra, extra_params = image_filter_sql(options)
        return clauses+extra, params+extra_params

    def query(self, options: SearchOptions, *, snapshot: bool = False) -> Iterator[FileRecord]:
        clauses, params = self._where(options)
        sql = "SELECT * FROM files WHERE " + " AND ".join(clauses) + " ORDER BY name_fold, path_key"
        if options.ocr_text.strip():
            from ..ocr.store import OCRStore
            sql, params = OCRStore(self).query_sql(" AND ".join(clauses), params, options.ocr_text)
        cursor = self.connection.execute(sql, params)
        if snapshot:
            # A caller that fills missing metadata must release the read statement
            # before writing: another WAL writer can otherwise invalidate its snapshot.
            try:
                rows = cursor.fetchall()
            finally:
                cursor.close()
            for row in rows:
                yield _record(row)
            return
        try:
            while rows := cursor.fetchmany(128):
                for row in rows:
                    yield _record(row)
        finally:
            cursor.close()


def _record(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"], path=Path(row["path"]), name=row["name"],
        extension=row["extension"], size=row["size"], mtime=row["mtime"],
        mtime_ns=row["mtime_ns"], is_image=bool(row["is_image"]),
        width=row["width"], height=row["height"],
        root_path=Path(row["root_path"]), indexed_at=row["indexed_at"],
        capture_time=row["capture_time"], capture_time_source=row["capture_time_source"],
        capture_time_text=row["capture_time_text"], file_uid=row["file_uid"],
        camera_make=row["camera_make"], camera_model=row["camera_model"],
        metadata_version=row["metadata_version"], metadata_error=row["metadata_error"],
    )

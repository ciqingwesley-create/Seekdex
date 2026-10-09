"""Versioned OCR text, transactional FTS synchronization and stale-source guards."""
from __future__ import annotations

import json
import sqlite3
from time import time
from typing import TYPE_CHECKING

from ..index.models import FileRecord
from .interfaces import OCRResult
from .model_cache import MODEL_ID
from .text import fts_query, normalize_text, query_terms, search_tokens, tokens

if TYPE_CHECKING:
    from ..index.database import FileIndex


def create_schema(connection: sqlite3.Connection) -> bool:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS ocr_results (
            id INTEGER PRIMARY KEY,
            file_uid TEXT NOT NULL REFERENCES files(file_uid) ON DELETE CASCADE,
            ocr_model_id TEXT NOT NULL, ocr_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL, search_tokens TEXT NOT NULL,
            indexed_at REAL NOT NULL, source_fingerprint TEXT NOT NULL,
            size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            detected_blocks TEXT NOT NULL DEFAULT '[]', confidence REAL,
            limitations TEXT NOT NULL DEFAULT '',
            UNIQUE(file_uid, ocr_model_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ocr_model ON ocr_results(ocr_model_id);
        CREATE TRIGGER IF NOT EXISTS file_ocr_invalidate AFTER UPDATE OF size,mtime_ns ON files
            WHEN OLD.size!=NEW.size OR OLD.mtime_ns!=NEW.mtime_ns
            BEGIN DELETE FROM ocr_results WHERE file_uid=NEW.file_uid; END;
    """)
    had_fts = connection.execute("SELECT 1 FROM sqlite_master WHERE name='ocr_fts'").fetchone() is not None
    try:
        connection.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
                search_tokens, content='ocr_results', content_rowid='id',
                tokenize='unicode61 remove_diacritics 0'
            );
            CREATE TRIGGER IF NOT EXISTS ocr_insert AFTER INSERT ON ocr_results BEGIN
                INSERT INTO ocr_fts(rowid, search_tokens) VALUES(NEW.id, NEW.search_tokens);
            END;
            CREATE TRIGGER IF NOT EXISTS ocr_delete AFTER DELETE ON ocr_results BEGIN
                INSERT INTO ocr_fts(ocr_fts,rowid,search_tokens) VALUES('delete',OLD.id,OLD.search_tokens);
            END;
            CREATE TRIGGER IF NOT EXISTS ocr_update AFTER UPDATE ON ocr_results BEGIN
                INSERT INTO ocr_fts(ocr_fts,rowid,search_tokens) VALUES('delete',OLD.id,OLD.search_tokens);
                INSERT INTO ocr_fts(rowid,search_tokens) VALUES(NEW.id,NEW.search_tokens);
            END;
        """)
        if not had_fts:
            # Recover text indexed before FTS5 became available without rerunning OCR.
            connection.execute("INSERT INTO ocr_fts(ocr_fts) VALUES('rebuild')")
        return True
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc):
            raise
        return False


def fingerprint(record: FileRecord) -> str:
    return f"stat-v1:{record.size}:{record.mtime_ns}"


class OCRStore:
    def __init__(self, database: FileIndex, model_id: str = MODEL_ID) -> None:
        self.database, self.model_id = database, model_id

    def get(self, record: FileRecord):
        return self.database.connection.execute(
            "SELECT * FROM ocr_results WHERE file_uid=? AND ocr_model_id=? AND size=? AND mtime_ns=?",
            (record.file_uid, self.model_id, record.size, record.mtime_ns),
        ).fetchone()

    def save(self, record: FileRecord, result: OCRResult) -> bool:
        current = self.database.connection.execute(
            "SELECT 1 FROM files WHERE file_uid=? AND size=? AND mtime_ns=? AND is_image=1",
            (record.file_uid, record.size, record.mtime_ns),
        ).fetchone()
        if current is None:
            return False
        if not self.database.ocr_available:
            raise RuntimeError("当前 SQLite 不支持 FTS5；普通搜索仍可使用")
        self.database.connection.execute("""
            INSERT INTO ocr_results(file_uid,ocr_model_id,ocr_text,normalized_text,search_tokens,
                indexed_at,source_fingerprint,size,mtime_ns,detected_blocks,confidence,limitations)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(file_uid,ocr_model_id) DO UPDATE SET
                ocr_text=excluded.ocr_text,normalized_text=excluded.normalized_text,
                search_tokens=excluded.search_tokens,indexed_at=excluded.indexed_at,
                source_fingerprint=excluded.source_fingerprint,size=excluded.size,mtime_ns=excluded.mtime_ns,
                detected_blocks=excluded.detected_blocks,confidence=excluded.confidence,
                limitations=excluded.limitations
        """, (record.file_uid, self.model_id, result.text, normalize_text(result.text),
               search_tokens(result.text), time(), fingerprint(record), record.size, record.mtime_ns,
               json.dumps(result.blocks, ensure_ascii=False), result.confidence, result.limitations))
        self.database._changed()
        return True

    def matches(self, record: FileRecord, query: str) -> bool:
        row = self.get(record)
        terms = query_terms(query)
        return bool(row and terms
                    and set(tokens(query, query=True)).issubset(row["search_tokens"].split())
                    and all(term in row["normalized_text"] for term in terms))

    def query_sql(self, where: str, params: list, query: str) -> tuple[str, list]:
        if not self.database.ocr_available:
            raise RuntimeError("当前 SQLite 不支持 FTS5")
        match = fts_query(query)
        if not match:
            return f"SELECT * FROM files WHERE {where} AND 0", params
        terms = query_terms(query)
        # FTS narrows candidates first; exact checks reject disconnected CJK bigrams.
        checks = " AND ".join("instr(o.normalized_text,?)>0" for _ in terms)
        sql = f"""
            WITH candidates AS (SELECT * FROM files WHERE {where})
            SELECT candidates.* FROM ocr_fts
            JOIN ocr_results o ON o.id=ocr_fts.rowid
            JOIN candidates ON candidates.file_uid=o.file_uid
            WHERE ocr_fts MATCH ? AND o.ocr_model_id=?
              AND candidates.size=o.size AND candidates.mtime_ns=o.mtime_ns
              AND candidates.is_image=1 AND {checks}
            ORDER BY bm25(ocr_fts), candidates.name_fold, candidates.path_key
        """
        return sql, params + [match, self.model_id] + terms

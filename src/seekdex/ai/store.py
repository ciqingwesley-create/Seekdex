"""Normalized float32 vectors, multiple immutable model/preprocessing versions."""
from __future__ import annotations

from time import time
import numpy as np
from ..index.database import FileIndex
from ..index.models import FileRecord
from .profiling import timed, IndexProfiler


def normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32)
    if values.ndim not in (1, 2) or not values.size or not np.isfinite(values).all():
        raise ValueError("无效向量")
    norm = np.linalg.norm(values, axis=-1, keepdims=True)
    if np.any(norm <= 1e-12):
        raise ValueError("零向量不能归一化")
    return np.ascontiguousarray(values / norm, dtype=np.float32)


class EmbeddingStore:
    def __init__(self, database: FileIndex, model_id: str, dimension: int) -> None:
        self.database, self.model_id, self.dimension = database, model_id, dimension

    def get(self, record: FileRecord) -> np.ndarray | None:
        row = self.database.connection.execute(
            """SELECT embedding FROM image_embeddings WHERE file_uid=? AND model_id=?
               AND embedding_dimension=? AND size=? AND mtime_ns=? AND dtype='float32'""",
            (record.file_uid, self.model_id, self.dimension, record.size, record.mtime_ns),
        ).fetchone()
        if row is None:
            return None
        vector = np.frombuffer(row[0], dtype="<f4")
        return vector.copy() if vector.size == self.dimension else None

    def valid_uids(self) -> set[str]:
        return {row[0] for row in self.database.connection.execute(
            """SELECT e.file_uid FROM image_embeddings e JOIN files f USING(file_uid)
               WHERE e.model_id=? AND e.embedding_dimension=? AND e.dtype='float32'
               AND e.size=f.size AND e.mtime_ns=f.mtime_ns""",
            (self.model_id, self.dimension))}

    def save(self, record: FileRecord, vector: np.ndarray) -> bool:
        value = normalize(vector)
        return self._save_normalized(record, value)

    def _save_normalized(self, record: FileRecord, value: np.ndarray) -> bool:
        if value.shape != (self.dimension,):
            raise ValueError("向量维度与模型不一致")
        count = self.database.connection.execute(
            """INSERT INTO image_embeddings
               (file_uid, model_id, embedding, embedding_dimension, dtype, indexed_at, size, mtime_ns)
               SELECT file_uid, ?, ?, ?, 'float32', ?, size, mtime_ns FROM files
               WHERE file_uid=? AND size=? AND mtime_ns=?
               ON CONFLICT(file_uid, model_id) DO UPDATE SET
               embedding=excluded.embedding, embedding_dimension=excluded.embedding_dimension,
               dtype=excluded.dtype, indexed_at=excluded.indexed_at,
               size=excluded.size, mtime_ns=excluded.mtime_ns""",
            (self.model_id, value.astype("<f4").tobytes(), self.dimension, time(),
             record.file_uid, record.size, record.mtime_ns),
        ).rowcount
        if count:
            self.database._changed()
        return bool(count)

    def save_batch(self, records: list[FileRecord], vectors: np.ndarray,
                   profiler: IndexProfiler | None = None) -> list[bool]:
        with timed(profiler, "embedding_normalize"):
            values = np.asarray(vectors, dtype=np.float32)
            if values.shape != (len(records), self.dimension):
                raise ValueError("批次向量数量或维度不一致")
            norms = np.linalg.norm(values, axis=1)
            valid = np.isfinite(values).all(axis=1) & (norms > 1e-12) & np.isfinite(norms)
            values = values.copy()
            values[valid] /= norms[valid, None]
        with timed(profiler, "sqlite"), self.database.transaction():
            return [bool(ok) and self._save_normalized(record, value)
                    for record, value, ok in zip(records, values, valid)]

    def revision(self) -> int:
        return self.database.connection.execute("SELECT revision FROM ai_state WHERE id=1").fetchone()[0]

    def matrix(self) -> tuple[list[str], np.ndarray]:
        uids: list[str] = []
        chunks: list[np.ndarray] = []
        rows = self.database.connection.execute(
            """SELECT e.file_uid, e.embedding FROM image_embeddings e JOIN files f USING(file_uid)
               WHERE e.model_id=? AND e.embedding_dimension=? AND e.dtype='float32'
               AND e.size=f.size AND e.mtime_ns=f.mtime_ns ORDER BY e.file_uid""",
            (self.model_id, self.dimension))
        for row in rows:
            vector = np.frombuffer(row[1], dtype="<f4")
            if vector.size == self.dimension and np.isfinite(vector).all():
                uids.append(row[0])
                chunks.append(vector)
        return uids, (np.stack(chunks) if chunks else np.empty((0, self.dimension), dtype=np.float32))

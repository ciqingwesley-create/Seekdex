"""Cached exact cosine retrieval; candidates are selected before similarity work."""
from __future__ import annotations

import numpy as np
from .interfaces import VectorSearchBackend
from .store import EmbeddingStore, normalize


class NumpyVectorSearch(VectorSearchBackend):
    def __init__(self) -> None:
        self._key: tuple | None = None
        self._rows: dict[str, int] = {}
        self._matrix = np.empty((0, 0), dtype=np.float32)

    def refresh(self, store: EmbeddingStore) -> None:
        key = (str(store.database.database_path.resolve()), store.model_id,
               store.dimension, store.revision())
        if key != self._key:
            uids, self._matrix = store.matrix()
            self._rows = {uid: i for i, uid in enumerate(uids)}
            self._key = key

    def search(self, query: np.ndarray, candidates: set[str], top_k: int,
               exclude: str | None = None) -> list[tuple[str, float]]:
        if top_k < 1:
            return []
        vector = normalize(query)
        if vector.ndim != 1 or vector.size != self._matrix.shape[1]:
            raise ValueError("查询向量维度不一致")
        selected = sorted((self._rows[uid], uid) for uid in candidates
                          if uid in self._rows and uid != exclude)
        if not selected:
            return []
        # Compute in bounded blocks; no second full 100k-image matrix copy.
        scores = np.empty(len(selected), dtype=np.float32)
        for offset in range(0, len(selected), 4096):
            part = selected[offset:offset+4096]
            scores[offset:offset+len(part)] = self._matrix[[row for row, _ in part]] @ vector
        count = min(top_k, len(selected))
        # Include all boundary ties before applying UID order, not an arbitrary subset.
        boundary = np.partition(scores, len(scores)-count)[len(scores)-count]
        top = np.flatnonzero(scores >= boundary)
        top = sorted(top, key=lambda i: (-float(scores[i]), selected[i][1]))
        return [(selected[i][1], float(np.clip(scores[i], -1, 1))) for i in top[:count]]

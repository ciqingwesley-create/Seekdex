"""Small, read-only semantic checks; no model download or index rebuild."""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Sequence
import numpy as np
from ..index.database import FileIndex
from ..search import SearchOptions
from ..worker import matches
from .store import EmbeddingStore, normalize


def embedding_stability(embeddings: np.ndarray) -> dict:
    values = np.asarray(embeddings, dtype=np.float32)
    if values.ndim != 2 or len(values) < 2:
        raise ValueError('至少提供两次同一文本的向量')
    unit = normalize(values).astype(np.float64)
    unit /= np.linalg.norm(unit, axis=1, keepdims=True)
    cosine = np.clip(unit @ unit.T, -1, 1)
    return {'minimum_pairwise_cosine': float(cosine.min()),
            'maximum_absolute_difference': float(np.abs(values-values[0]).max()),
            'identical': bool(np.array_equal(values, np.broadcast_to(values[0], values.shape))),
            'pairwise_cosine': cosine.tolist()}


def cosine_matrix(images: np.ndarray, texts: np.ndarray) -> np.ndarray:
    left, right = normalize(images), normalize(texts)
    if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
        raise ValueError('图片与文本必须是相同维度的二维向量矩阵')
    return left @ right.T


def filter_trace(database: FileIndex, options: SearchOptions, store: EmbeddingStore) -> dict:
    """Use cached metadata only; explicitly count unresolved capture dates."""
    base = options.folder_scope()
    records = [r for r in database.query(base) if r.is_image]
    basic = len(records)
    name_options = replace(base, filename=options.filename)
    records = [r for r in records if matches(r, name_options)]
    after_name = len(records)
    extension_options = replace(name_options, extensions=options.extensions)
    records = [r for r in records if matches(r, extension_options)]
    after_extension = len(records)
    records = [r for r in records if matches(r, replace(extension_options, camera=options.camera))]
    after_camera = len(records)
    records = [r for r in records if matches(r, replace(options, modified_from=None, modified_before=None))]
    after_image_filters = len(records)
    unresolved = sum(r.capture_time is None for r in records) if options.time_type=='capture' and (
        options.modified_from is not None or options.modified_before is not None) else 0
    records = [r for r in records if matches(r, options)]
    valid = store.valid_uids()
    return {'folder': str(options.folder), 'recursive': options.recursive,
            'filename': options.filename, 'extensions': sorted(options.extensions),
            'time_type': options.time_type, 'modified_from': options.modified_from,
            'modified_before': options.modified_before,
            'basic_index_images': basic, 'after_filename': after_name,
            'after_extension': after_extension, 'after_date_cached': len(records),
            'after_camera': after_camera, 'after_image_filters': after_image_filters,
            'capture_dates_not_loaded': unresolved,
            'embedding_candidates': sum(r.file_uid in valid for r in records)}


def retrieval_metrics(scores: np.ndarray, expected: Sequence[set[int]],
                      top_k: tuple[int, ...] = (1, 5, 10)) -> dict:
    """scores: images × queries. Report positive coverage and hit rate separately."""
    values = np.asarray(scores)
    if values.ndim != 2 or values.shape[1] != len(expected) or not np.isfinite(values).all():
        raise ValueError('分数矩阵与查询数量不一致')
    if any(not targets or min(targets)<0 or max(targets)>=len(values) for targets in expected):
        raise ValueError('每个查询都必须有有效的人工标注正例')
    order = np.argsort(-values, axis=0, kind='stable')
    result = {'query_count':len(expected), 'image_count':len(values)}
    for k in top_k:
        if k < 1:
            raise ValueError('K 必须为正数')
        hits = [len(set(order[:k,q]) & targets) for q,targets in enumerate(expected)]
        result[f'recall@{k}'] = float(np.mean([hit/len(targets) for hit,targets in zip(hits,expected)]))
        result[f'hit@{k}'] = float(np.mean([hit>0 for hit in hits]))
    return result

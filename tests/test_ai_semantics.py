from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

from seekdex.ai.diagnostics import embedding_stability, cosine_matrix, filter_trace, retrieval_metrics
from seekdex.ai.store import EmbeddingStore
from seekdex.ai.vectors import NumpyVectorSearch
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.search import SearchOptions


def test_stability_detects_dropout_like_noise():
    exact = np.tile(np.arange(1,6,dtype=np.float32),(20,1))
    result = embedding_stability(exact)
    assert result['identical'] and result['maximum_absolute_difference']==0
    assert result['minimum_pairwise_cosine']==pytest.approx(1)
    exact[2,0] += 1
    assert not embedding_stability(exact)['identical']


def test_cosine_math_not_negative_or_broadcast():
    images = np.array([[3,4,0],[0,0,5],[-3,-4,0]],dtype=np.float32)
    texts = np.array([[6,8,0],[0,0,2]],dtype=np.float32)
    assert np.allclose(cosine_matrix(images,texts),[[1,0],[0,1],[-1,0]])
    with pytest.raises(ValueError):
        cosine_matrix(images,np.ones((2,4)))


@pytest.mark.parametrize('k',[1,3,20,99])
def test_top_k_matches_independent_cosine_sort_and_uid(k):
    rng = np.random.default_rng(21)
    matrix = rng.normal(size=(30,8)).astype(np.float32)
    matrix /= np.linalg.norm(matrix,axis=1,keepdims=True)
    query = rng.normal(size=8).astype(np.float32)
    query /= np.linalg.norm(query)
    # Exact boundary ties must use UID consistently.
    matrix[:7] = query
    search = NumpyVectorSearch()
    search._matrix = matrix
    search._rows = {f'{i:02}':i for i in range(30)}
    expected = sorted(search._rows, key=lambda uid:(-float(matrix[int(uid)] @ query),uid))[:k]
    found = search.search(query,set(search._rows),k)
    assert [uid for uid,_ in found]==expected
    assert all(np.isclose(score,float(matrix[int(uid)] @ query)) for uid,score in found)


def test_float32_blob_rows_remain_paired_and_normalized(tmp_path):
    root = tmp_path/'photos'
    root.mkdir()
    for name in ('a','b','c'):
        Image.new('RGB',(20,20),'red').save(root/(name+'.png'))
    with FileIndex(tmp_path/'index.db') as database:
        Indexer(database).refresh(root)
        records = list(database.query(SearchOptions(root)))
        store = EmbeddingStore(database,'test',3)
        store.save_batch(records,np.diag([2,3,4]).astype(np.float32))
        uids,matrix = store.matrix()
        for i,record in enumerate(records):
            assert np.array_equal(matrix[uids.index(record.file_uid)],np.eye(3)[i])
        assert np.allclose(np.linalg.norm(matrix,axis=1),1)


def test_filter_trace_clearing_does_not_leave_state(tmp_path):
    root = tmp_path/'photos'
    root.mkdir()
    Image.new('RGB',(20,20),'red').save(root/'spider.jpg')
    Image.new('RGB',(20,20),'green').save(root/'glass.png')
    with FileIndex(tmp_path/'index.db') as database:
        Indexer(database).refresh(root)
        base = SearchOptions(root)
        store = EmbeddingStore(database,'test',3)
        store.save_batch(list(database.query(base)),np.ones((2,3)))
        restricted = replace(base,filename='spider',extensions=frozenset({'jpg'}),modified_before=1)
        trace = filter_trace(database,restricted,store)
        assert (trace['basic_index_images'],trace['after_filename'],trace['after_extension'],trace['after_date_cached'])==(2,1,1,0)
        cleared = filter_trace(database,base,store)
        assert cleared['embedding_candidates']==2
        assert {r.name for r in database.query(base)}=={'spider.jpg','glass.png'}


def test_ui_reads_cleared_filters_fresh(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from seekdex.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path/'index.db',tmp_path/'cache')
    try:
        window.folder.setText(str(tmp_path))
        window.filename.setText('old-filter')
        window.extensions.setText('jpg')
        window.from_enabled.setChecked(True)
        window.to_enabled.setChecked(True)
        window.time_type.setCurrentIndex(1)
        before = window._search_options()
        assert before.filename=='old-filter' and before.modified_from is not None
        window.filename.clear()
        window.extensions.clear()
        window.from_enabled.setChecked(False)
        window.to_enabled.setChecked(False)
        window.time_type.setCurrentIndex(0)
        after = window._search_options()
        assert after.filename=='' and not after.extensions
        assert after.modified_from is None and after.modified_before is None
        assert after.time_type=='modified'
    finally:
        window.close()


def test_recall_positive_coverage_separate_from_hit_rate():
    scores = np.array([[.9,.1],[.8,.9],[.1,.8]],dtype=np.float32)
    result = retrieval_metrics(scores,[{0,1},{1}])
    assert result['recall@1']==.75 and result['hit@1']==1
    assert result['recall@5']==1 and result['recall@10']==1

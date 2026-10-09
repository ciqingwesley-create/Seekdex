from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import sqlite3
import numpy as np
from PIL import Image
import pytest

from seekdex.ai.interfaces import EmbeddingBackend
from seekdex.ai.backend import ChineseClipBackend, select_device
from seekdex.ai.service import AIService
from seekdex.ai.store import EmbeddingStore, normalize
from seekdex.ai.vectors import NumpyVectorSearch
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.organize.engine import Organizer
from seekdex.organize.planner import prepare_plan
from seekdex.paths import path_key
from seekdex.search import SearchOptions
from seekdex.worker import SearchThread
from seekdex.thumbnail_cache import ThumbnailCache


class FakeBackend(EmbeddingBackend):
    model_id = "fake-v1"
    embedding_dimension = 3
    batch_size = 2
    def __init__(self):
        self.encoded = 0
        self.batches = []
    def load_model(self):
        pass
    def encode_images(self, images):
        self.encoded += len(images)
        self.batches.append(len(images))
        return np.asarray([np.asarray(image, dtype=np.float32).mean(axis=(0, 1)) for image in images])
    def encode_text(self, text):
        return {"红色": np.array([1, 0, 0]), "绿色": np.array([0, 1, 0]),
                "蓝色": np.array([0, 0, 1])}[text]


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for name, color in (("a.png", "red"), ("b.png", "green"), ("c.png", "blue")):
        Image.new("RGB", (40, 30), color).save(root / name)
    (root / "note.txt").write_text("ordinary")
    with FileIndex(tmp_path / "index.db") as database:
        Indexer(database).refresh(root)
        yield root, database, FakeBackend()


def test_ai_schema_migrates_existing_database(library):
    root, database, _ = library
    before = [(r.id, r.file_uid, r.path) for r in database.query(SearchOptions(root))]
    database.connection.execute("DROP TABLE image_embeddings")
    database.commit()
    with FileIndex(database.database_path) as migrated:
        assert [(r.id, r.file_uid, r.path) for r in migrated.query(SearchOptions(root))] == before
        assert migrated.connection.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0] == 0


def test_store_roundtrip_and_uid(library):
    root, database, backend = library
    record = database.get_by_key(path_key(root / "a.png"))
    store = EmbeddingStore(database, backend.model_id, 3)
    assert store.save(record, np.array([3, 4, 0]))
    assert np.allclose(store.get(record), [0.6, 0.8, 0])
    assert store.valid_uids() == {record.file_uid}
    assert database.connection.execute("SELECT file_uid,dtype,embedding_dimension FROM image_embeddings").fetchone()[:] == (record.file_uid, "float32", 3)


@pytest.mark.parametrize("value", [np.array([0, 0]), np.array([np.nan, 1]), np.array([np.inf, 1]), np.ones((2, 2, 2))])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError):
        normalize(value)


def test_normalization_batch():
    assert np.allclose(normalize(np.array([[3, 4], [0, 2]])), [[0.6, 0.8], [0, 1]])


def test_dimension_rejected(library):
    root, database, _ = library
    store = EmbeddingStore(database, "test", 3)
    with pytest.raises(ValueError):
        store.save(database.get_by_key(path_key(root / "a.png")), np.array([1, 2]))


def test_build_batches_resume_and_text_ranking(library):
    root, database, backend = library
    service = AIService(database, backend)
    first = service.build(SearchOptions(root))
    assert (first.total, first.completed, first.failed) == (3, 3, 0)
    assert backend.batches == [2, 1]
    again = service.build(SearchOptions(root))
    assert again.existing == 3 and again.completed == 0 and backend.encoded == 3
    results = service.search(SearchOptions(root), text="蓝色", top_k=2)
    assert len(results) == 2 and results[0].path.name == "c.png"
    assert results[0].similarity == pytest.approx(1)


def test_candidate_metadata_filters_before_similarity(library):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    options = SearchOptions(root, filename="B", extensions=frozenset({"png"}))
    assert [r.path.name for r in service.search(options, text="红色")] == ["b.png"]
    assert service.search(replace(options, extensions=frozenset({"jpg"})), text="红色") == []
    assert service.search(replace(options, modified_before=1), text="红色") == []


def test_capture_time_filter(library):
    from datetime import datetime
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    record = database.get_by_key(path_key(root / "a.png"))
    database.update_capture_time(replace(record, capture_time=datetime(2020, 1, 1).timestamp(),
                                        capture_time_text="2020-01-01T12:00:00", capture_time_source="exif_datetime_original"))
    options = SearchOptions(root, time_type="capture", modified_from=datetime(2020, 1, 1).timestamp(),
                            modified_before=datetime(2020, 1, 2).timestamp())
    assert [r.path.name for r in service.search(options, text="红色")] == ["a.png"]


def test_ai_camera_resolution_combination_preserves_existing_vectors(library):
    from datetime import datetime
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    record = database.get_by_key(path_key(root / "a.png"))
    before = service.store.get(record).copy()
    revision = service.store.revision()
    date = datetime(2026, 5, 6)
    database.update_image_metadata(replace(record, width=7360, height=4912,
        camera_make="NIKON CORPORATION", camera_model="NIKON D800", metadata_version=1,
        capture_time=date.timestamp(), capture_time_text=date.isoformat(), capture_time_source="exif_datetime_original"))
    options = SearchOptions(root, filename="a", camera="Nikon D800", extensions=frozenset({"png"}),
        min_megapixels=30, orientation="landscape", time_type="capture",
        modified_from=date.timestamp(), modified_before=date.timestamp()+86400)
    results = service.search(options, text="红色")
    assert [r.path.name for r in results] == ["a.png"]
    assert np.array_equal(service.store.get(database.get_by_id(record.id)), before)
    assert service.store.revision() == revision and backend.encoded == 3
    assert service.search_counts == (1, 1)


def test_image_query_reuses_vector_and_excludes_self(library):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    results = service.search(SearchOptions(root), image_path=root / "a.png")
    assert len(results) == 2 and all(r.path.name != "a.png" for r in results)
    assert backend.encoded == 3


def test_image_query_generates_missing_vector(library):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root), [root / "b.png"])
    assert service.search(SearchOptions(root), image_path=root / "a.png")[0].path.name == "b.png"
    assert backend.encoded == 2


def test_move_preserves_embedding_and_copy_gets_new_uid(library, tmp_path):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    old = database.get_by_key(path_key(root / "a.png"))
    plan = prepare_plan([old.path], tmp_path / "moved", database, action="move", template="{filename}")
    organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
    result = organizer.execute(plan, dry_run=False)[0]
    assert result.status == "success"
    moved = database.get_by_key(path_key(result.target))
    assert moved.file_uid == old.file_uid
    assert service.store.get(moved) is not None
    service.build(SearchOptions(result.target.parent))
    assert backend.encoded == 3
    copied = organizer.execute(prepare_plan([moved.path], tmp_path / "copies", database,
                    action="copy", template="{filename}"), dry_run=False)[0]
    new = database.get_by_key(path_key(copied.target))
    assert new.file_uid != old.file_uid and service.store.get(new) is None
    assert organizer.undo(result.operation_id).status == "success"
    restored = database.get_by_key(path_key(old.path))
    assert restored.file_uid == old.file_uid
    assert np.array_equal(service.store.get(restored), service.store.get(moved))
    service.build(SearchOptions(root))
    assert backend.encoded == 3


@pytest.mark.parametrize("change", ["mtime", "size"])
def test_changed_file_invalidates_embedding(library, change):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    path = root / "a.png"
    stat = path.stat()
    if change == "mtime":
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    else:
        with path.open("ab") as stream:
            stream.write(b"padding")
    Indexer(database).refresh(root)
    assert service.store.get(database.get_by_key(path_key(path))) is None
    service.build(SearchOptions(root))
    assert backend.encoded == 4


def test_model_version_indexes_separately(library):
    root, database, backend = library
    AIService(database, backend).build(SearchOptions(root))
    other = FakeBackend()
    other.model_id = "fake-v2"
    assert AIService(database, other).build(SearchOptions(root)).completed == 3
    assert database.connection.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0] == 6


def test_cancel_keeps_completed_then_resumes(library):
    root, database, backend = library
    service = AIService(database, backend)
    stats = service.build(SearchOptions(root), cancelled=lambda: backend.encoded >= 2)
    assert stats.cancelled and stats.completed == 2
    assert len(service.store.valid_uids()) == 2
    resumed = service.build(SearchOptions(root))
    assert resumed.existing == 2 and resumed.completed == 1 and backend.encoded == 3


def test_broken_image_does_not_stop_batch(library):
    root, database, backend = library
    (root / "broken.jpg").write_bytes(b"not an image")
    result = AIService(database, backend).build(SearchOptions(root))
    assert result.completed == 3 and result.failed == 1
    assert database.get_by_key(path_key(root / "broken.jpg")) is not None


def test_no_raw_full_decode(tmp_path, monkeypatch):
    import rawpy
    from seekdex.ai.images import read_ai_image
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_thumb(self): raise ValueError("no preview")
        def postprocess(self, **kwargs): raise AssertionError("full RAW decoded")
    monkeypatch.setattr(rawpy, "imread", lambda _: Raw())
    with pytest.raises(ValueError, match="no preview"):
        read_ai_image(tmp_path / "sample.nef")


def test_image_input_is_bounded(tmp_path):
    from seekdex.ai.images import read_ai_image
    path = tmp_path / "large.jpg"
    Image.new("RGB", (4000, 3000), "green").save(path)
    with read_ai_image(path) as image:
        assert max(image.size) <= 448 and image.mode == "RGB"


def test_matrix_cache_reused_and_invalidated(library, monkeypatch):
    root, database, backend = library
    vectors = NumpyVectorSearch()
    service = AIService(database, backend, vectors)
    service.build(SearchOptions(root))
    service.search(SearchOptions(root), text="红色")
    first = vectors._matrix
    monkeypatch.setattr(service.store, "matrix", lambda: (_ for _ in ()).throw(AssertionError("reloaded")))
    service.search(SearchOptions(root), text="蓝色")
    assert vectors._matrix is first
    database.delete_path(path_key(root / "a.png"))
    with pytest.raises(AssertionError, match="reloaded"):
        service.search(SearchOptions(root), text="蓝色")


def test_unavailable_model_does_not_break_ordinary_search(library, monkeypatch):
    root, database, _ = library
    monkeypatch.setattr("seekdex.ai.backend.installed", lambda _: False)
    with pytest.raises(RuntimeError, match="未安装"):
        ChineseClipBackend().load_model()
    found, done = [], []
    task = SearchThread(SearchOptions(root), database_path=database.database_path)
    task.batch_ready.connect(lambda batch: found.extend(batch))
    task.search_done.connect(lambda *values: done.append(values))
    task.run()
    assert len(found) == 4 and done == [(4, False, "")]


def test_auto_device_real_kernel_failure_falls_back():
    class Cuda:
        def is_available(self): return True
    class Torch:
        cuda = Cuda()
        def ones(self, *args, **kwargs): raise RuntimeError("unsupported architecture")
    device, reason = select_device(Torch(), "auto")
    assert device == "cpu" and "unsupported architecture" in reason
    assert select_device(Torch(), "cpu") == ("cpu", "")


def test_deleted_file_cascades_vectors(library):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    database.delete_path(path_key(root / "a.png"))
    assert len(service.store.valid_uids()) == 2
    assert database.connection.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0] == 2


def test_deleted_then_reappearing_path_cannot_reuse_old_embedding(library):
    root, database, backend = library
    service = AIService(database, backend)
    service.build(SearchOptions(root))
    path = root / "a.png"
    old = database.get_by_key(path_key(path))
    path.unlink()
    Indexer(database).refresh(root)
    assert old.file_uid not in service.store.valid_uids()
    Image.new("RGB", (20, 20), "blue").save(path)
    Indexer(database).refresh(root)
    returned = database.get_by_key(path_key(path))
    assert returned.file_uid != old.file_uid and service.store.get(returned) is None
    resumed = service.build(SearchOptions(root))
    assert resumed.existing == 2 and resumed.completed == 1


def test_inference_source_change_is_discarded(library):
    root, database, backend = library
    original = backend.encode_images
    def modify(images):
        vectors = original(images)
        path = root / "a.png"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000))
        return vectors
    backend.encode_images = modify
    stats = AIService(database, backend).build(SearchOptions(root), [root / "a.png"])
    assert stats.failed == 1 and stats.completed == 0

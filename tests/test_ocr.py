from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
from threading import Event

from PIL import Image
import pytest

from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.organize.engine import Organizer
from seekdex.organize.planner import prepare_plan
from seekdex.paths import path_key
from seekdex.search import SearchOptions
from seekdex.thumbnail_cache import ThumbnailCache
from seekdex.ocr.interfaces import OCRBackend, OCRResult
from seekdex.ocr.model_cache import MODEL_ID, model_id, installed, model_dir, model_files
from seekdex.ocr.service import OCRService
from seekdex.ocr.store import OCRStore, create_schema
from seekdex.ocr.text import normalize_text, query_terms, fts_query
from seekdex.ocr.images import read_ocr_image


class FakeOCR(OCRBackend):
    model_id = MODEL_ID
    backend_id = "fake"
    device = "Fake CPU"
    def __init__(self):
        self.calls = 0
        self.loads = 0
        self.fail_green = False
        self.empty_blue = False
        self.after = lambda: None
    def load_model(self):
        self.loads += 1
    def recognize_image(self, image):
        self.calls += 1
        red, green, blue = image.getpixel((0, 0))
        self.after()
        if green > red and green > blue:
            if self.fail_green:
                raise ValueError("fake failed image")
            text = "中文菜单 红烧牛肉 ChatGPT"
        elif blue > red and blue > green:
            text = "" if self.empty_blue else "Windows Update failed\n错误代码 0x80070005"
        else:
            text = "蜘蛛 Windows Update"
        return OCRResult(text, [{"text": text, "confidence": 0.25}], 0.25, .01, .02)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for name, color in (("a.png", "red"), ("b.png", "green"), ("c.png", "blue")):
        Image.new("RGB", (48, 32), color).save(root / name)
    sub = root / "sub"
    sub.mkdir()
    Image.new("RGB", (48, 32), "blue").save(sub / "d.jpg")
    (root / "note.txt").write_text("ordinary")
    with FileIndex(tmp_path / "index.db") as database:
        Indexer(database).refresh(root)
        yield root, database, FakeOCR()


def test_schema_migration_preserves_uid_ai_exif_and_logs(library):
    from seekdex.ai.store import EmbeddingStore
    import numpy as np
    root, database, _ = library
    record = database.get_by_key(path_key(root / "a.png"))
    database.update_image_metadata(replace(record, width=4000, height=3000,
        camera_make="Nikon", camera_model="D800", metadata_version=1,
        capture_time=123, capture_time_source="filesystem_mtime", capture_time_text="2020-01-01T12:00:00"))
    EmbeddingStore(database, "fake-model", 3).save(record, np.array([1, 0, 0]))
    database.connection.execute("INSERT INTO organize_batches VALUES ('old',1,'move','target','{filename}','skip')")
    database.connection.executescript("DROP TRIGGER ocr_insert; DROP TRIGGER ocr_update; DROP TRIGGER ocr_delete; DROP TABLE ocr_fts; DROP TABLE ocr_results")
    database.commit()
    with FileIndex(database.database_path) as migrated:
        current = migrated.get_by_id(record.id)
        assert current.file_uid == record.file_uid
        assert current.camera_model == "D800" and current.capture_time == 123
        assert current.width == 4000
        assert migrated.connection.execute("SELECT count(*) FROM image_embeddings").fetchone()[0] == 1
        assert migrated.connection.execute("SELECT count(*) FROM organize_batches").fetchone()[0] == 1
        assert migrated.ocr_available
        assert migrated.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 0


def test_save_update_original_text_low_confidence_and_fts(library):
    root, database, _ = library
    record = database.get_by_key(path_key(root / "a.png"))
    store = OCRStore(database)
    raw = "Ｗｉｎｄｏｗｓ  Update\r\n错误 代码 0x80070005"
    assert store.save(record, OCRResult(raw, [{"text":raw,"confidence":.05}], .05))
    database.commit()
    row = store.get(record)
    assert row["ocr_text"] == raw
    assert row["confidence"] == .05 and row["source_fingerprint"]
    assert row["normalized_text"] == "windows update 错误代码 0x80070005"
    assert list(database.query(SearchOptions(root, ocr_text="错误代码")))
    store.save(record, OCRResult("ChatGPT"))
    database.commit()
    assert not list(database.query(SearchOptions(root, ocr_text="错误代码")))
    assert [r.path for r in database.query(SearchOptions(root, ocr_text="ChatGPT"))] == [record.path]
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 1


@pytest.mark.parametrize("query, expected", [
    ("Windows", {"a.png", "c.png", "d.jpg"}),
    ("windows", {"a.png", "c.png", "d.jpg"}),
    ("WINDOWS", {"a.png", "c.png", "d.jpg"}),
    ("Update", {"a.png", "c.png", "d.jpg"}),
    ("0x80070005", {"c.png", "d.jpg"}),
    ("错误代码", {"c.png", "d.jpg"}),
    ("错误", {"c.png", "d.jpg"}),
    ("误", {"c.png", "d.jpg"}),
    ("中文菜单", {"b.png"}),
    ("菜单", {"b.png"}),
    ("红烧", {"b.png"}),
    ("ChatGPT", {"b.png"}),
    ("Windows 错误代码", {"c.png", "d.jpg"}),
    ("Update 0x80070005", {"c.png", "d.jpg"}),
    ("不存在", set()),
    ('" OR *', set()),
])
def test_multilingual_fts(library, query, expected):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    assert {r.name for r in database.query(SearchOptions(root, ocr_text=query))} == expected


def test_chinese_disconnected_bigrams_do_not_match(library):
    root, database, _ = library
    store = OCRStore(database)
    row = database.get_by_key(path_key(root / "a.png"))
    store.save(row, OCRResult("错误 误代 代码"))
    database.commit()
    assert not list(database.query(SearchOptions(root, ocr_text="错误代码")))


def test_fts_query_does_not_use_like_and_has_fts_query_plan(library):
    root, database, _ = library
    clauses, params = database._where(SearchOptions(root))
    sql, params = OCRStore(database).query_sql(" AND ".join(clauses), params, "错误代码 Windows")
    assert "LIKE" not in sql.upper()
    plan = " ".join(str(row[:]) for row in database.connection.execute("EXPLAIN QUERY PLAN " + sql, params))
    assert "VIRTUAL TABLE INDEX" in plan


def test_ocr_relevance_sorting(library):
    root, database, _ = library
    store = OCRStore(database)
    store.save(database.get_by_key(path_key(root / "a.png")), OCRResult("Windows " + "other " * 100))
    store.save(database.get_by_key(path_key(root / "b.png")), OCRResult("Windows Windows Windows"))
    assert [r.name for r in database.query(SearchOptions(root, ocr_text="Windows"))] == ["b.png", "a.png"]


def test_cancel_resume_and_reuse_even_empty_ocr(library):
    root, database, backend = library
    backend.empty_blue = True
    service = OCRService(database, backend)
    stats = service.build(SearchOptions(root), cancelled=lambda: backend.calls >= 1)
    assert stats.cancelled and stats.completed == 1
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 1
    stats = service.build(SearchOptions(root))
    assert stats.existing == 1 and stats.completed == 3 and not stats.cancelled
    count = backend.calls
    again = service.build(SearchOptions(root))
    assert again.existing == 4 and again.completed == 0 and backend.calls == count
    empty = database.get_by_key(path_key(root / "c.png"))
    assert service.store.get(empty)["ocr_text"] == ""


def test_source_change_invalidates_only_that_ocr_and_fts(library):
    root, database, backend = library
    service = OCRService(database, backend)
    service.build(SearchOptions(root))
    before = database.get_by_key(path_key(root / "c.png"))
    Image.new("RGB", (50, 40), "green").save(before.path)
    Indexer(database).refresh(root)
    current = database.get_by_key(path_key(before.path))
    assert current.file_uid == before.file_uid
    assert service.store.get(current) is None
    assert {r.name for r in database.query(SearchOptions(root, ocr_text="错误代码"))} == {"d.jpg"}
    count = backend.calls
    stats = service.build(SearchOptions(root))
    assert stats.completed == 1 and stats.existing == 3 and backend.calls == count+1


def test_mtime_only_change_and_model_preprocess_version(library):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    path = root / "a.png"
    old = path.stat()
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns+1_000_000_000))
    stats = OCRService(database, backend).build(SearchOptions(root))
    assert stats.completed == 1 and stats.existing == 3
    backend.model_id = model_id(max_side=1536)
    stats = OCRService(database, backend).build(SearchOptions(root))
    assert stats.completed == 4 and stats.existing == 0
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 8


def test_changed_during_recognition_is_not_saved(library):
    root, database, backend = library
    source = root / "a.png"
    backend.after = lambda: Image.new("RGB", (55, 40), "green").save(source)
    stats = OCRService(database, backend).build(SearchOptions(root), [source])
    assert stats.failed == 1 and stats.completed == 0
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 0


def test_deleted_uid_cascades_ocr_and_fts(library):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    database.delete_path(path_key(root / "b.png"))
    database.commit()
    assert not list(database.query(SearchOptions(root, ocr_text="ChatGPT")))
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 3


def test_move_and_undo_retain_ocr_copy_gets_new_uid(library, tmp_path):
    root, database, backend = library
    source = root / "a.png"
    service = OCRService(database, backend)
    service.build(SearchOptions(root), [source])
    original = database.get_by_key(path_key(source))
    organizer = Organizer(database, ThumbnailCache(tmp_path / "cache"))
    destination = tmp_path / "destination"
    plan = prepare_plan([source], destination, database, action="move", template="{filename}")
    operation = organizer.execute(plan, dry_run=False)[0]
    assert operation.status == "success"
    moved = database.get_by_key(path_key(destination / source.name))
    assert moved.file_uid == original.file_uid and service.store.get(moved)["ocr_text"]
    calls = backend.calls
    assert service.build(SearchOptions(destination), [moved.path]).existing == 1
    assert backend.calls == calls
    assert organizer.undo(operation.operation_id).status == "success"
    restored = database.get_by_key(path_key(source))
    assert restored.file_uid == original.file_uid and service.store.get(restored)
    plan = prepare_plan([source], destination, database, action="copy", template="{filename}")
    assert organizer.execute(plan, dry_run=False)[0].status == "success"
    copied = database.get_by_key(path_key(destination / source.name))
    assert copied.file_uid != original.file_uid and service.store.get(copied) is None


def test_ordinary_filters_and_directory_boundaries(library):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    options = SearchOptions(root, recursive=False, ocr_text="错误代码", extensions=frozenset({"png"}), filename="C")
    assert [r.name for r in database.query(options)] == ["c.png"]
    assert not list(database.query(replace(options, modified_before=1)))
    assert not list(database.query(replace(options, folder=root.parent / "photo")))
    record = database.get_by_key(path_key(root / "c.png"))
    database.update_image_metadata(replace(record, width=3840, height=2160,
        camera_make="NIKON CORPORATION", camera_model="NIKON D800", metadata_version=1))
    assert [r.name for r in database.query(replace(options, camera="D800", resolution="4k", orientation="landscape"))] == ["c.png"]
    assert not list(database.query(replace(options, min_megapixels=20)))


def test_ai_candidates_are_ocr_filtered_before_top_k(library):
    from seekdex.ai.service import AIService
    from test_ai import FakeBackend
    root, database, ocr = library
    OCRService(database, ocr).build(SearchOptions(root))
    backend = FakeBackend()
    ai = AIService(database, backend)
    ai.build(SearchOptions(root))
    results = ai.search(SearchOptions(root, ocr_text="错误代码"), text="蓝色", top_k=1)
    # Both blue images have identical fake embeddings; either is a valid Top-1 tie.
    assert len(results) == 1 and results[0].path.name in {"c.png", "d.jpg"}
    assert ai.search_counts == (2, 2)
    assert not ai.search(SearchOptions(root, ocr_text="missing"), text="蓝色")


def test_corrupt_and_engine_failure_do_not_stop_batch(library):
    root, database, backend = library
    (root / "broken.jpg").write_bytes(b"not an image")
    backend.fail_green = True
    stats = OCRService(database, backend).build(SearchOptions(root))
    assert stats.total == 5 and stats.completed == 3 and stats.failed == 2
    assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0] == 3


def test_batch_transactions_not_per_image(library):
    root, database, backend = library
    for number in range(20):
        Image.new("RGB", (20, 20), "red").save(root / f"extra-{number}.png")
    statements = []
    database.connection.set_trace_callback(statements.append)
    stats = OCRService(database, backend).build(SearchOptions(root))
    assert stats.completed == 24
    assert sum(sql == "BEGIN IMMEDIATE" for sql in statements) == 3
    assert sum(sql == "COMMIT" for sql in statements) < stats.completed


def test_all_indexed_scope_does_not_scan_unknown_directories(library, monkeypatch):
    root, database, backend = library
    monkeypatch.setattr(Indexer, "refresh", lambda *args, **kwargs: pytest.fail("must not scan"))
    stats = OCRService(database, backend).build(SearchOptions(root), all_indexed=True)
    assert stats.completed == 4


def test_recognize_batch_contract():
    backend = FakeOCR()
    with Image.new("RGB", (20, 20), "red") as image:
        results = backend.recognize_batch([image, image])
    assert len(results) == backend.calls == 2


def test_ocr_input_resolution_and_rotation(tmp_path):
    path = tmp_path / "large.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (3000, 2000), "red").save(path, exif=exif)
    with read_ocr_image(path, 1536) as image:
        assert max(image.size) <= 1536 and image.height > image.width


@pytest.mark.parametrize("format_name", ["JPEG", "BITMAP"])
def test_raw_preview_never_demosaics(tmp_path, monkeypatch, format_name):
    import rawpy
    import numpy as np
    from types import SimpleNamespace
    stream = BytesIO()
    Image.new("RGB", (500, 300), "red").save(stream, "JPEG")
    preview = SimpleNamespace(format=getattr(rawpy.ThumbFormat, format_name),
        data=stream.getvalue() if format_name=="JPEG" else np.zeros((300, 500, 3), dtype=np.uint8))
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_thumb(self): return preview
        def unpack(self): pytest.fail("must not unpack RAW")
        def postprocess(self): pytest.fail("must not demosaic RAW")
    monkeypatch.setattr(rawpy, "imread", lambda path: Raw())
    with read_ocr_image(tmp_path / "sample.NEF") as image:
        assert image.size == (500, 300)
        assert "1600" in image.info["ocr_limitations"]


def test_raw_without_preview_fails_safely(tmp_path, monkeypatch):
    import rawpy
    from types import SimpleNamespace
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_thumb(self): return SimpleNamespace(format=None)
    monkeypatch.setattr(rawpy, "imread", lambda path: Raw())
    with pytest.raises(ValueError, match="不执行完整 RAW"):
        read_ocr_image(tmp_path / "sample.NRW")


def test_openvino_unavailable_falls_back_without_model_download(monkeypatch, tmp_path):
    from seekdex.ocr.backend import RapidOCRBackend
    import seekdex.ocr.backend as module
    monkeypatch.setattr(module, "installed", lambda *args: True)
    backend = RapidOCRBackend("ov-gpu", tmp_path)
    attempted = []
    def create(choice):
        attempted.append(choice)
        if choice != "ort-cpu":
            raise RuntimeError("not supported")
        return object()
    monkeypatch.setattr(backend, "_create_engine", create)
    backend.load_model()
    assert attempted == ["ov-gpu", "ov-cpu", "ort-cpu"]
    assert backend.backend_id == "ort-cpu" and "not supported" in backend.device_message


def test_ocr_missing_model_never_downloads_implicitly(monkeypatch, tmp_path):
    from seekdex.ocr.backend import RapidOCRBackend
    import seekdex.ocr.backend as module
    monkeypatch.setattr(module, "installed", lambda *args: False)
    with pytest.raises(FileNotFoundError):
        RapidOCRBackend(root=tmp_path).load_model()


def test_model_corruption_is_detected(tmp_path):
    folder = model_dir(tmp_path)
    folder.mkdir(parents=True)
    for filename, _ in model_files().values():
        (folder / filename).write_bytes(b"corrupt model")
    assert installed(tmp_path, verify=False)
    assert not installed(tmp_path)


def test_tuning_cancel_and_preprocessing_identity(tmp_path):
    from seekdex.ocr.tuning import select_backend
    with pytest.raises(InterruptedError):
        select_backend([tmp_path / "a.jpg"], tmp_path, cancelled=lambda: True)
    assert model_id("small", 2048) != model_id("small", 1536)
    assert model_id("small") != model_id("medium")


def test_plaintext_highlight_is_html_safe():
    from seekdex.ocr.details import highlighted_text
    result = highlighted_text("<script>ChatGPT & Windows</script>", "ChatGPT")
    assert "<script>" not in result and "&lt;script&gt;" in result
    assert "background-color" in result
    assert normalize_text("１２３ＡＢＣ") == "123abc"


def test_missing_fts_rebuilds_saved_ocr_without_recognition(library):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    database.connection.executescript("DROP TRIGGER ocr_insert; DROP TRIGGER ocr_update; DROP TRIGGER ocr_delete; DROP TABLE ocr_fts")
    assert create_schema(database.connection)
    assert len(list(database.query(SearchOptions(root, ocr_text="Windows")))) == 3
    assert backend.calls == 4


def test_ocr_queries_cache_even_when_folder_coverage_is_missing(library):
    from seekdex.worker import SearchThread
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    database.connection.execute("DELETE FROM roots")
    database.commit()
    expected = [record.path for record in database.query(SearchOptions(root,ocr_text="Windows"))]
    worker = SearchThread(SearchOptions(root,ocr_text="Windows"),database_path=database.database_path)
    results, messages = [], []
    worker.batch_ready.connect(lambda batch: results.extend(record.path for record in batch))
    worker.progress.connect(messages.append)
    worker.run()
    assert messages[0] == "正在读取已有索引…"
    assert results == expected


def test_scan_ocr_matching_agrees_with_fts_word_boundaries(library):
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    store = OCRStore(database)
    record = database.get_by_key(path_key(root / "c.png"))
    assert store.matches(record, "Windows")
    assert not store.matches(record, "Wind")
    assert not list(database.query(SearchOptions(root, ocr_text="Wind")))


def test_openvino_runtime_fallback_keeps_ocr_identity(monkeypatch):
    from seekdex.ocr.backend import RapidOCRBackend
    import seekdex.ocr.backend as module
    monkeypatch.setattr(module,"installed",lambda *args: True)
    backend = RapidOCRBackend("ov-gpu")
    identity = backend.model_id
    monkeypatch.setattr(backend,"_create_engine",lambda choice: object())
    def recognize(image):
        if backend.backend_id=="ov-gpu":
            raise RuntimeError("device execution failed")
        return OCRResult("Windows")
    monkeypatch.setattr(backend,"_recognize",recognize)
    with Image.new("RGB",(10,10)) as image:
        assert backend.recognize_image(image).text == "Windows"
    assert backend.backend_id=="ov-cpu" and backend.model_id==identity
    assert "运行失败" in backend.device_message


def test_ocr_quality_metrics_use_actual_fts_results(library):
    from seekdex.ocr.benchmark import quality_metrics
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    samples = [dict(path=str(root/"b.png"), keywords=["中文菜单", "ChatGPT", "不存在"])]
    metrics = quality_metrics(database, OCRStore(database), samples)
    assert metrics["keywords"] == 3 and metrics["exact_keyword_hits"] == 2
    assert metrics["keyword_recall"] == pytest.approx(2/3)
    assert metrics["recall_at_k"]["5"] == pytest.approx(2/3)


def test_install_checks_download_before_replacing_model(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace
    import seekdex.ocr.model_cache as module
    expected = hashlib.sha256(b"valid model").hexdigest()
    monkeypatch.setattr(module,"model_files",lambda variant: {"det":("det.onnx",expected)})
    target = model_dir(tmp_path)/"det.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old model")
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self,*args): yield b"corrupt download"
    monkeypatch.setitem(sys.modules,"requests",SimpleNamespace(get=lambda *args,**kwargs: Response()))
    with pytest.raises(ValueError, match="校验失败"):
        module.install_models(tmp_path)
    assert target.read_bytes()==b"old model"
    assert not list(target.parent.glob("*.part"))


def test_worker_ocr_search_and_cancellation_keeps_database_usable(library):
    from seekdex.worker import SearchThread
    root, database, backend = library
    OCRService(database, backend).build(SearchOptions(root))
    worker = SearchThread(SearchOptions(root, ocr_text="错误代码"), database_path=database.database_path)
    batches, done = [], []
    worker.batch_ready.connect(lambda batch: (batches.extend(batch), worker.cancel()))
    worker.search_done.connect(lambda *args: done.append(args))
    worker.run()
    assert batches and done[0][1] and not done[0][2]
    assert list(database.query(SearchOptions(root, ocr_text="Windows")))


def test_ocr_backend_missing_does_not_disable_normal_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from seekdex.ui import MainWindow
    from seekdex.ai.panel import AIPanel
    from seekdex.ocr.panel import OCRPanel
    monkeypatch.setattr(AIPanel, "check_status", lambda self: None)
    monkeypatch.setattr(OCRPanel, "check_status", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "index.db", tmp_path / "cache")
    try:
        window.ocr._dependencies = False
        window.ocr._installed = False
        window.ocr.text.setText("Windows Update")
        assert window._search_options().ocr_text == "Windows Update"
        window.ocr.text.clear()
        assert not window._search_options().ocr_text
        assert window.search_button.isEnabled() and window.ai.isEnabled()
        assert window.organize_button.isEnabled()
        from seekdex.search import SearchResult
        window.model.append([SearchResult(tmp_path/"picture.png",1,1,width=100,height=60,is_image=True,file_uid="uid")])
        window.table.selectRow(0)
        assert window.ocr_details_button.isEnabled()
        assert "100 × 60" in window.image_details.text()
        window.model.clear()
        assert not window.ocr_details_button.isEnabled()
        assert "100 × 60" not in window.image_details.text()
    finally:
        window.close()
        app.processEvents()

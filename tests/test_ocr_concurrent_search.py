from __future__ import annotations

from threading import Event

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from seekdex.ai.panel import AIPanel
from seekdex.ai.store import EmbeddingStore
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.ocr.panel import OCRPanel
from seekdex.search import SearchOptions
from seekdex.ui import MainWindow
from test_ai import FakeBackend
from test_ocr import FakeOCR


def wait_until(predicate, timeout=10000):
    if predicate():
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(10)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(timeout)
    loop.exec()
    timer.stop()
    deadline.stop()
    assert predicate(), "Background task did not reach the expected state"


class PausedOCR(FakeOCR):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.after = self.pause

    def pause(self):
        # Eight completed images have already been committed when #9 starts.
        if self.calls == 9:
            self.entered.set()
            if not self.release.wait(10):
                raise TimeoutError("Test did not release OCR")


@pytest.fixture
def concurrent_window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(AIPanel, "check_status", lambda self: None)
    monkeypatch.setattr(OCRPanel, "check_status", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_metadata_status", lambda *args: None)
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "photos"
    root.mkdir()
    for i in range(12):
        Image.new("RGB", (48, 32), "blue").save(root / f"{i:02}.png")
    (root / "note.txt").write_text("ordinary", encoding="utf8")
    db_path = tmp_path / "index.db"
    ai = FakeBackend()
    ai.device = "Fake CPU"
    ai.device_message = "Test backend"
    with FileIndex(db_path) as database:
        Indexer(database).refresh(root)
        store = EmbeddingStore(database, ai.model_id, ai.embedding_dimension)
        for record in database.query(SearchOptions(root)):
            if record.is_image:
                store.save(record, np.array([0, 0, 1]))
    window = MainWindow(db_path, tmp_path / "cache")
    window.folder.setText(str(root))
    window.filename.clear()
    window.extensions.clear()
    window.from_enabled.setChecked(False)
    window.to_enabled.setChecked(False)
    window.image_filters.group.setChecked(False)
    from seekdex.preferences import SearchPreferences
    window._preferences = SearchPreferences(tmp_path / "settings.ini")
    window.ai._installed = window.ai._dependencies = True
    window.ai.runtime.backend = ai
    monkeypatch.setattr(window.ai.runtime, "get_backend", lambda requested: ai)
    backend = PausedOCR()
    monkeypatch.setattr(window.ocr.runtime, "get_backend", lambda requested: backend)
    window.ocr.device.setCurrentIndex(window.ocr.device.findData("ort-cpu"))
    window.show()
    try:
        yield window, backend, db_path
    finally:
        backend.release.set()
        window.close()
        wait_until(lambda: not window.isVisible())
        app.processEvents()


def start_paused_ocr(window, backend):
    window.ocr._start("index")
    wait_until(backend.entered.is_set)
    assert window.ocr.task.isRunning()


def test_ordinary_and_ocr_queries_work_while_indexing(concurrent_window):
    window, backend, db_path = concurrent_window
    start_paused_ocr(window, backend)
    assert window.search_button.isEnabled() and window.folder.isEnabled()
    assert window.ocr.cancel_button.isEnabled()
    assert not window.organize_button.isEnabled()
    window.filename.setText("note")
    window.search_button.click()
    wait_until(lambda: window._worker is None)
    assert [r.path.name for r in window.model.results] == ["note.txt"]
    assert window._loading_enabled
    assert window.ocr.task.isRunning() and window.ocr.cancel_button.isEnabled()
    window.filename.clear()
    window.ocr.text.setText("Windows Update")
    window.search_button.click()
    wait_until(lambda: window._worker is None)
    assert len(window.model.results) == 8
    assert window.ocr.task.isRunning()
    backend.release.set()
    wait_until(lambda: window.ocr.task is None)
    window.search_button.click()
    wait_until(lambda: window._worker is None)
    assert len(window.model.results) == 12
    with FileIndex(db_path) as database:
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_ai_search_and_similar_image_work_while_indexing(concurrent_window):
    window, backend, db_path = concurrent_window
    start_paused_ocr(window, backend)
    assert window.ai.search_button.isEnabled() and window.ai.similar_button.isEnabled()
    assert not window.ai.index_button.isEnabled()
    window.ai.text.setText("蓝色")
    window.ai.search_button.click()
    assert window.ocr.cancel_button.isEnabled()
    wait_until(lambda: window.ai.task is None)
    assert len(window.model.results) == 12, window.ai.status.text()
    assert all(r.similarity == pytest.approx(1) for r in window.model.results)
    assert window.ocr.task.isRunning()
    assert window.search_button.isEnabled() and not window.ai.index_button.isEnabled()
    window.ai.search(window.model.results[0].path)
    wait_until(lambda: window.ai.task is None)
    assert len(window.model.results) == 11
    with FileIndex(db_path) as database:
        assert database.connection.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0] == 12


@pytest.mark.parametrize("foreground", ["ordinary", "ai"])
def test_ocr_finishing_does_not_unlock_running_search(concurrent_window, foreground):
    window, backend, _ = concurrent_window
    start_paused_ocr(window, backend)
    if foreground == "ordinary":
        window._set_searching(True)
    else:
        window._ai_busy(True)
    backend.release.set()
    wait_until(lambda: window.ocr.task is None)
    assert not window.search_button.isEnabled()
    assert window.cancel_button.isEnabled() == (foreground == "ordinary")
    assert not window.ocr.isEnabled()
    if foreground == "ordinary":
        window._set_searching(False)
    else:
        window._ai_busy(False)
    assert window.search_button.isEnabled() and window.ai.index_button.isEnabled()


def test_search_cancel_keeps_ocr_running_and_cancel_state(concurrent_window):
    window, backend, _ = concurrent_window
    start_paused_ocr(window, backend)
    window._set_searching(True)
    window._cancel_search()
    assert not window.ocr.task.cancelled.is_set()
    backend.release.set()
    wait_until(lambda: window.ocr.task is None)
    assert not window.cancel_button.isEnabled()
    assert not window.search_button.isEnabled()
    window._set_searching(False)


def test_cancel_ocr_preserves_committed_data_and_search(concurrent_window):
    window, backend, db_path = concurrent_window
    start_paused_ocr(window, backend)
    window.ocr.cancel_button.click()
    backend.release.set()
    wait_until(lambda: window.ocr.task is None)
    assert "已取消" in window.ocr.status.text()
    assert window.search_button.isEnabled() and window.ai.search_button.isEnabled()
    with FileIndex(db_path) as database:
        assert 8 <= len(list(database.query(SearchOptions(window._search_options().folder, ocr_text="Windows")))) < 12
        assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_changing_search_folder_does_not_retarget_ocr(concurrent_window, tmp_path):
    window, backend, _ = concurrent_window
    start_paused_ocr(window, backend)
    original = window.ocr.task.options.folder
    other = tmp_path / "other"
    other.mkdir()
    (other / "other.txt").write_text("other", encoding="utf8")
    window.folder.setText(str(other))
    window.search_button.click()
    wait_until(lambda: window._worker is None)
    assert [r.path.name for r in window.model.results] == ["other.txt"]
    assert window.ocr.task.options.folder == original
    assert window.ocr.task.isRunning()


def test_closing_requests_both_cancellations(concurrent_window, monkeypatch):
    window, backend, _ = concurrent_window
    start_paused_ocr(window, backend)
    entered, release = Event(), Event()
    ai = window.ai.runtime.backend
    encode = ai.encode_text
    def paused_encode(text):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Test did not release AI")
        return encode(text)
    monkeypatch.setattr(ai, "encode_text", paused_encode)
    window.ai.text.setText("蓝色")
    window.ai.search_button.click()
    try:
        wait_until(entered.is_set)
        ocr_task, ai_task = window.ocr.task, window.ai.task
        window.close()
        assert ocr_task.cancelled.is_set() and ai_task.cancelled.is_set()
        assert window.isVisible()  # In-flight work finishes asynchronously.
    finally:
        backend.release.set()
        release.set()
    wait_until(lambda: not window.isVisible())


@pytest.mark.parametrize("kind", ["ordinary", "ai"])
def test_capture_date_query_with_concurrent_ocr_commit(tmp_path, monkeypatch, kind):
    from seekdex.capture_time import ensure_capture_time
    from seekdex.ocr.interfaces import OCRResult
    from seekdex.ocr.store import OCRStore
    from seekdex.ai.service import AIService
    from seekdex.worker import SearchThread
    import seekdex.worker as worker_module
    import seekdex.ai.service as ai_module
    root = tmp_path / "photos"
    root.mkdir()
    for i in range(140):
        Image.new("RGB", (8, 8), "blue").save(root / f"{i:03}.png")
    db_path = tmp_path / "index.db"
    with FileIndex(db_path) as writer:
        Indexer(writer).refresh(root)
        backend = FakeBackend()
        store = EmbeddingStore(writer, backend.model_id, 3)
        for record in writer.query(SearchOptions(root)):
            store.save(record, np.array([0, 0, 1]))
        writer.commit()
        committed = False
        def with_ocr_write(record, reader):
            nonlocal committed
            if not committed:
                committed = True
                with writer.transaction():
                    OCRStore(writer).save(record, OCRResult("Windows Update"))
            return ensure_capture_time(record, reader)
        monkeypatch.setattr(worker_module, "ensure_capture_time", with_ocr_write)
        monkeypatch.setattr(ai_module, "ensure_capture_time", with_ocr_write)
        options = SearchOptions(root, time_type="capture", modified_from=0)
        if kind == "ordinary":
            task = SearchThread(options, database_path=db_path)
            completed = []
            task.search_done.connect(lambda *args: completed.append(args))
            task.run()
            assert completed and not completed[0][2], completed
            assert completed[0][0] == 140
        else:
            with FileIndex(db_path) as reader:
                assert len(AIService(reader, backend).search(options, text="蓝色", top_k=200)) == 140
        assert committed

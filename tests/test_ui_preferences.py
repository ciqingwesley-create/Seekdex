from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from seekdex.preferences import SearchPreferences
from seekdex.result_table import ResultTable
from seekdex.search import SearchResult
from seekdex.ui import MainWindow, ResultModel


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from seekdex.ai.panel import AIPanel
    monkeypatch.setattr(AIPanel, "check_status", lambda self: None)
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def settle(app) -> None:
    for _ in range(5):
        app.processEvents()


def test_preferences_first_start_and_empty_edit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "profile" / "settings.ini"
    preferences = SearchPreferences(path)
    assert preferences.folder == str(tmp_path)
    assert preferences.recursive
    chosen = str(tmp_path / "照片")
    preferences.save(chosen, False)
    preferences.save("  ", False)
    reopened = SearchPreferences(path)
    assert reopened.folder == chosen
    assert not reopened.recursive


def test_window_restores_manually_entered_scope_after_close(tmp_path, app):
    db = tmp_path / "profile" / "index.db"
    chosen = str(tmp_path / "离线照片目录")
    window = MainWindow(db, tmp_path / "cache")
    window.folder.setText(chosen)
    window.recursive.setChecked(False)
    window.close()
    settle(app)
    reopened = MainWindow(db, tmp_path / "cache")
    try:
        assert reopened.folder.text() == chosen
        assert not reopened.recursive.isChecked()
        assert reopened._worker is None
        assert not reopened._loading_enabled
        assert reopened._search_options().folder == Path(chosen)
    finally:
        reopened.close()


def test_browse_saves_scope_immediately(tmp_path, app, monkeypatch):
    path = tmp_path / "settings.ini"
    window = MainWindow(tmp_path / "index.db", tmp_path / "cache", settings_path=path)
    chosen = str(tmp_path / "photos")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: chosen)
    try:
        window._browse()
        preferences = SearchPreferences(path)
        assert preferences.folder == chosen
        window.recursive.setChecked(False)
        assert not SearchPreferences(path).recursive
    finally:
        window.close()


@pytest.mark.parametrize("preview", [QSize(160, 120), QSize(240, 180), QSize(320, 240)])
def test_result_table_fits_window_and_preserves_score_column(app, preview):
    table = ResultTable()
    model = ResultModel()
    table.setModel(model)
    table.preview_size_changed.connect(model.set_preview_size)
    model.append([SearchResult(Path("photos") / ("long-name-" * 10 + ".jpg"), 100,
                               1_700_000_000, similarity=0.75)])
    try:
        table.set_preview_size(preview)
        table.resize(1100, 350)
        table.show()
        settle(app)
        small_path_width = table.columnWidth(2)
        for width in (1600, 2200, 1100):
            table.resize(width, 350)
            settle(app)
            assert table.horizontalHeader().length() == table.viewport().width()
            assert table.horizontalScrollBar().maximum() == 0
            assert table.iconSize().width() <= table.columnWidth(0) - 16
            assert table.iconSize().width() <= preview.width()
            if width == 2200:
                assert table.columnWidth(2) > small_path_width
                assert table.iconSize() == preview
        header = table.horizontalHeader()
        header.moveSection(header.visualIndex(8), 1)
        table.fit_columns()
        assert header.logicalIndex(1) == 8
        assert model.data(model.index(0, 8)) == "0.750"
        assert header.length() == table.viewport().width()
        assert not table.wordWrap()
    finally:
        table.close()


def test_main_window_adapts_without_manual_refit(tmp_path, app):
    window = MainWindow(tmp_path / "index.db", tmp_path / "cache")
    try:
        window.show()
        settle(app)
        first_width = window.table.columnWidth(2)
        window.resize(1800, 900)
        settle(app)
        assert window.table.columnWidth(2) > first_width
        assert window.table.horizontalHeader().length() == window.table.viewport().width()
        window.thumbnail_size.setCurrentIndex(2)
        settle(app)
        assert window.model.preview_size == window.table.iconSize()
        assert window.table.horizontalHeader().length() == window.table.viewport().width()
        window._ai_results([])
        assert window.table.horizontalHeader().logicalIndex(1) == 8
        assert window.table.horizontalHeader().length() == window.table.viewport().width()
    finally:
        window.close()


def test_tooltip_exposes_elided_metadata():
    model = ResultModel()
    model.append([SearchResult(Path("photos/image.jpg"), 100, 1_700_000_000,
                               capture_time_text="2026-10-07T14:30:00")])
    tooltip = model.data(model.index(0, 6), Qt.ToolTipRole)
    assert "2026-10-07T14:30:00" in tooltip
    assert str(Path("photos/image.jpg")) in tooltip

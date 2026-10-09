"""Lazy AI imports must fail visibly without touching existing embeddings."""
import builtins

import pytest
from PySide6.QtWidgets import QApplication

from seekdex.ai.worker import AIRuntime, AITask
from seekdex.search import SearchOptions


@pytest.mark.parametrize("module,needs_restart", [
    ("seekdex.paths", True),
    ("transformers", False),
])
def test_lazy_service_import_error_keeps_job_recoverable(tmp_path, monkeypatch, caplog,
                                                        module, needs_restart):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    original_import = builtins.__import__

    def broken_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "service" and level == 1 and globals.get("__package__") == "seekdex.ai":
            raise ImportError("cannot import name 'unique_roots'", name=module)
        return original_import(name, globals, locals, fromlist, level)

    task = AITask(AIRuntime(), "search", SearchOptions(tmp_path),
                  database_path=tmp_path / "index.db", text="苹果")
    completed = []
    task.completed.connect(lambda result, error: completed.append((result, error)))
    monkeypatch.setattr(builtins, "__import__", broken_import)
    task.run()

    assert completed[0][0] is None
    assert "ImportError" in completed[0][1]
    assert ("重新启动" in completed[0][1]) is needs_restart
    assert not (tmp_path / "index.db").exists()
    assert caplog.records[-1].exc_info is not None
    task.deleteLater()
    app.processEvents()

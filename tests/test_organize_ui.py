from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep

from seekdex.organize.worker import OrganizeTask


def test_directory_preview_excludes_nested_destination(tmp_path: Path) -> None:
    root = tmp_path / "source"
    destination = root / "organized"
    destination.mkdir(parents=True)
    (root / "a.txt").write_text("source")
    (destination / "already.txt").write_text("previous output")
    worker = OrganizeTask("preview", tmp_path / "index.db", tmp_path / "cache", dict(
        scope="directory", source_root=root, target_root=destination, sources=[],
        recursive=True, action="copy", conflict_policy="skip", template="{filename}",
    ))
    completed = []
    worker.completed.connect(lambda *args: completed.append(args))
    worker.run()
    plan, error, cancelled = completed[0]
    assert not error and not cancelled
    assert [item.source.name for item in plan.items] == ["a.txt"]
    assert not (destination / "a.txt").exists()


def test_dialog_requires_preview_and_confirmation_before_copy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from seekdex.organize.dialog import OrganizeDialog

    root = tmp_path / "source"
    root.mkdir()
    source = root / "a.txt"
    source.write_text("source")
    destination = tmp_path / "dest"
    app = QApplication.instance() or QApplication([])
    dialog = OrganizeDialog(root, [source], True, tmp_path / "index.db", tmp_path / "cache")
    def wait_task():
        deadline = monotonic() + 5
        while dialog._task is not None and monotonic() < deadline:
            app.processEvents()
            sleep(0.005)
        assert dialog._task is None
    try:
        dialog.show()
        assert not dialog.execute_button.isEnabled()
        dialog.target_root.setText(str(destination))
        dialog._preview()
        wait_task()
        assert dialog.plan is not None and dialog.execute_button.isEnabled()
        target = dialog.plan.items[0].target
        assert source.exists() and not target.exists()
        dialog.template.setText("{filename}")
        assert dialog.plan is None and not dialog.execute_button.isEnabled()
        dialog._preview()
        wait_task()
        target = dialog.plan.items[0].target
        confirmations = []
        def answer(*args):
            confirmations.append(args)
            return QMessageBox.Yes
        monkeypatch.setattr(QMessageBox, "question", answer)
        dialog._execute()
        wait_task()
        assert len(confirmations) == 1
        assert source.exists() and target.read_text() == "source"
        assert not dialog.execute_button.isEnabled()
        dialog._history()
        wait_task()
        assert dialog.history_model.entries[0]["operation"] == "copy"
        assert dialog.history_model.entries[0]["status"] == "success"
    finally:
        if dialog._task is not None:
            dialog._task.cancel()
            dialog._task.wait()
            app.processEvents()
        dialog.close()
        app.processEvents()

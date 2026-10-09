"""Organization tasks run outside the Qt UI thread."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from ..index.database import FileIndex
from ..search import _files
from ..thumbnail_cache import ThumbnailCache
from .engine import Organizer
from .planner import prepare_plan


class OrganizeTask(QThread):
    progress = Signal(str)
    completed = Signal(object, str, bool)

    def __init__(self, task: str, database_path: Path, cache_dir: Path, parameters: dict, parent=None):
        super().__init__(parent)
        self.task = task
        self.database_path = database_path
        self.cache_dir = cache_dir
        self.parameters = parameters
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        result = None
        error = ""
        try:
            with FileIndex(self.database_path) as database:
                organizer = Organizer(database, ThumbnailCache(self.cache_dir))
                if self.task == "preview":
                    values = self.parameters
                    target = values["target_root"].expanduser().resolve()
                    if values["scope"] == "directory":
                        folder = values["source_root"].expanduser().resolve()
                        if not folder.is_dir():
                            raise NotADirectoryError("源目录不存在或无法访问")
                        sources = (
                            path for path in _files(folder, values["recursive"], self._cancelled.is_set)
                            if target == folder or not target.is_relative_to(folder) or not path.is_relative_to(target)
                        )
                    else:
                        sources = values["sources"]
                    result = prepare_plan(
                        sources, target, database, action=values["action"], template=values["template"],
                        conflict_policy=values["conflict_policy"], cancelled=self._cancelled.is_set,
                        progress=lambda count: self.progress.emit(f"已预览 {count:,} 个文件…"),
                    )
                elif self.task == "execute":
                    result = organizer.execute(
                        self.parameters["plan"], dry_run=False, cancelled=self._cancelled.is_set,
                        progress=lambda done, total: self.progress.emit(f"已处理 {done:,} / {total:,} 个文件…"),
                    )
                elif self.task == "history":
                    organizer.recover_interrupted()
                    result = organizer.history()
                elif self.task == "undo":
                    result = organizer.undo(self.parameters["operation_id"])
                else:
                    raise ValueError("未知整理任务")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Organization task %s failed: %s",self.task,type(exc).__name__)
            error = str(exc) or type(exc).__name__
        self.completed.emit(result, error, self._cancelled.is_set())

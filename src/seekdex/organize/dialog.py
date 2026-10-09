"""Organization preview and journal UI; file I/O is delegated to OrganizeTask."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableView,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..capture_time import source_label
from ..paths import get_database_path, get_thumbnail_cache_dir
from .models import DEFAULT_TEMPLATE, OrganizePlan
from .worker import OrganizeTask

STATUS = {"ready": "可执行", "skip": "跳过", "error": "错误", "preview": "预览",
          "success": "成功", "failed": "失败", "skipped": "跳过", "pending": "待处理",
          "materialized": "目标已创建", "needs_review": "需人工核对"}
ACTION = {"move": "移动", "copy": "复制", "undo_move": "撤销移动", "delete_copy": "删除副本"}


class PreviewModel(QAbstractTableModel):
    headers = ("原始路径", "目标路径", "使用的时间", "时间来源", "操作", "冲突", "状态 / 说明")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plan: OrganizePlan | None = None
        self.outcomes = {}

    def set_plan(self, plan: OrganizePlan | None, outcomes=None):
        self.beginResetModel()
        self.plan = plan
        self.outcomes = {str(item.source): item for item in outcomes or []}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.plan is None else len(self.plan.items)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        return self.headers[section] if orientation == Qt.Horizontal and role == Qt.DisplayRole else None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or self.plan is None:
            return None
        item = self.plan.items[index.row()]
        outcome = self.outcomes.get(str(item.source))
        status, detail = (outcome.status, outcome.message) if outcome else (item.status, item.detail)
        values = (str(item.source), str(item.target or ""), item.capture_time_text,
                  source_label(item.capture_time_source), ACTION[self.plan.action],
                  "是（已编号）" if item.conflict and item.status == "ready" else "是" if item.conflict else "否",
                  STATUS.get(status, status) + (f"：{detail}" if detail else ""))
        return values[index.column()] if role in (Qt.DisplayRole, Qt.ToolTipRole) else None


class HistoryModel(QAbstractTableModel):
    headers = ("操作时间", "操作", "原路径", "新路径", "状态", "撤销状态", "说明")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[dict] = []

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        return self.headers[section] if orientation == Qt.Horizontal and role == Qt.DisplayRole else None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.entries[index.row()]
        values = (datetime.fromtimestamp(row["operated_at"]).strftime("%Y-%m-%d %H:%M:%S"),
                  ACTION.get(row["operation"], row["operation"]), row["source_path"], row["target_path"],
                  STATUS.get(row["status"], row["status"]),
                  STATUS.get(row["undo_status"], row["undo_status"] or ""),
                  row["error"] or row["undo_error"] or "")
        return values[index.column()] if role in (Qt.DisplayRole, Qt.ToolTipRole) else None


class OrganizeDialog(QDialog):
    files_changed = Signal()

    def __init__(self, source_root: Path, sources: list[Path], recursive: bool,
                 database_path: Path | None = None, cache_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按时间整理文件")
        self.resize(1200, 760)
        self.database_path = database_path or get_database_path()
        self.cache_dir = cache_dir or get_thumbnail_cache_dir()
        self.sources = sources
        self._task: OrganizeTask | None = None
        self._completion = None
        self.plan: OrganizePlan | None = None
        self._busy = False
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem("整理当前搜索结果", "results")
        self.scope.addItem("整理整个目录", "directory")
        self.source_root = QLineEdit(str(source_root))
        self.target_root = QLineEdit()
        self.target_root.setPlaceholderText("选择或输入目标目录")
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_root)
        source_browse = QPushButton("选择…")
        source_browse.clicked.connect(lambda: self._choose(self.source_root))
        source_row.addWidget(source_browse)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_root)
        target_browse = QPushButton("选择…")
        target_browse.clicked.connect(lambda: self._choose(self.target_root))
        target_row.addWidget(target_browse)
        self.recursive = QCheckBox("包含子目录")
        self.recursive.setChecked(recursive)
        self.action = QComboBox()
        self.action.addItem("复制", "copy")
        self.action.addItem("移动", "move")
        self.conflict = QComboBox()
        self.conflict.addItem("跳过重名文件", "skip")
        self.conflict.addItem("自动编号重命名", "rename")
        self.template = QLineEdit(DEFAULT_TEMPLATE)
        form.addRow("整理范围", self.scope)
        form.addRow("源目录", source_row)
        form.addRow("", self.recursive)
        form.addRow("目标目录", target_row)
        form.addRow("操作", self.action)
        form.addRow("重名处理", self.conflict)
        form.addRow("时间路径模板", self.template)
        preview_layout.addLayout(form)
        preview_layout.addWidget(QLabel("支持 year/month/day/hour/minute/second/filename/stem/ext；如 {month:02}。ext 含点。默认仅预览。"))
        self.preview_model = PreviewModel(self)
        self.preview_table = self._table(self.preview_model)
        preview_layout.addWidget(self.preview_table)
        self.path_details = QLabel("选择预览行可查看完整路径。")
        self.path_details.setWordWrap(True)
        self.path_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_layout.addWidget(self.path_details)
        self.preview_table.selectionModel().currentRowChanged.connect(self._preview_selection)
        buttons = QHBoxLayout()
        self.preview_button = QPushButton("生成预览")
        self.execute_button = QPushButton("确认并执行…")
        self.execute_button.setEnabled(False)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.preview_button.clicked.connect(self._preview)
        self.execute_button.clicked.connect(self._execute)
        self.cancel_button.clicked.connect(self._cancel)
        for button in (self.preview_button, self.execute_button, self.cancel_button):
            buttons.addWidget(button)
        buttons.addStretch()
        preview_layout.addLayout(buttons)
        self.tabs.addTab(preview, "整理预览")

        history = QWidget()
        history_layout = QVBoxLayout(history)
        self.history_model = HistoryModel(self)
        self.history_table = self._table(self.history_model)
        history_layout.addWidget(self.history_table)
        history_buttons = QHBoxLayout()
        self.history_button = QPushButton("刷新操作记录")
        self.undo_button = QPushButton("撤销移动 / 删除本次副本…")
        self.history_button.clicked.connect(self._history)
        self.undo_button.clicked.connect(self._undo)
        history_buttons.addWidget(self.history_button)
        history_buttons.addWidget(self.undo_button)
        history_buttons.addStretch()
        history_layout.addLayout(history_buttons)
        self.tabs.addTab(history, "操作记录与撤销")
        self.status = QLabel("先生成预览并核对目标路径，确认后才能执行。")
        layout.addWidget(self.status)
        self._controls = (self.scope, self.source_root, self.target_root, self.recursive,
                          self.action, self.conflict, self.template, source_browse, target_browse)
        for field in (self.source_root, self.target_root, self.template):
            field.textChanged.connect(self._invalidate)
        for combo in (self.scope, self.action, self.conflict):
            combo.currentIndexChanged.connect(self._invalidate)
        self.recursive.toggled.connect(self._invalidate)

    def _table(self, model):
        table = QTableView()
        table.setModel(model)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideMiddle)
        widths = (310, 310, 180, 150, 70, 100, 240) if isinstance(model, PreviewModel) else (155, 90, 280, 280, 105, 110, 240)
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
        return table

    def _preview_selection(self, index, _previous):
        plan = self.preview_model.plan
        if index.isValid() and plan is not None:
            item = plan.items[index.row()]
            self.path_details.setText(f"{item.source}\n→ {item.target or '无法生成目标路径'}")
        else:
            self.path_details.setText("选择预览行可查看完整路径。")

    def _choose(self, field):
        selected = QFileDialog.getExistingDirectory(self, "选择目录", field.text())
        if selected:
            field.setText(selected)

    def _invalidate(self, *_args):
        self.plan = None
        self.execute_button.setEnabled(False)

    def _preview(self):
        if not self.target_root.text().strip():
            QMessageBox.warning(self, "请选择目标目录", "目标目录不能为空。")
            return
        self.plan = None
        self.preview_model.set_plan(None)
        self._start("preview", dict(
            scope=self.scope.currentData(), source_root=Path(self.source_root.text()),
            target_root=Path(self.target_root.text()), sources=self.sources,
            recursive=self.recursive.isChecked(), action=self.action.currentData(),
            conflict_policy=self.conflict.currentData(), template=self.template.text(),
        ))

    def _execute(self):
        if self.plan is None:
            return
        count = sum(item.status == "ready" for item in self.plan.items)
        if QMessageBox.question(self, "确认整理", f"将{ACTION[self.plan.action]} {count} 个文件到：\n{self.plan.target_root}\n\n是否按当前预览执行？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._start("execute", {"plan": self.plan})

    def _history(self):
        self._start("history", {})

    def _undo(self):
        index = self.history_table.currentIndex()
        if not index.isValid():
            QMessageBox.information(self, "选择操作", "请先选择一条成功的移动或复制记录。")
            return
        row = self.history_model.entries[index.row()]
        if row["status"] != "success" or row["operation"] not in {"move", "copy"} or row["undone_at"] is not None:
            QMessageBox.warning(self, "无法撤销", "该记录不能撤销或已经撤销。")
            return
        message = (f"恢复文件到原位置：\n{row['source_path']}" if row["operation"] == "move"
                   else f"校验身份后删除本次创建的副本：\n{row['target_path']}")
        if QMessageBox.question(self, "确认撤销", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._start("undo", {"operation_id": row["id"]})

    def _start(self, task: str, parameters: dict):
        if self._task is not None:
            return
        self._busy = True
        self._completion = None
        for field in self._controls:
            field.setEnabled(False)
        for button in (self.preview_button, self.execute_button, self.history_button, self.undo_button):
            button.setEnabled(False)
        self.cancel_button.setEnabled(task in {"preview", "execute"})
        self.status.setText("正在后台处理…")
        self._task = OrganizeTask(task, self.database_path, self.cache_dir, parameters, self)
        self._task.progress.connect(self.status.setText)
        self._task.completed.connect(self._completed)
        self._task.finished.connect(self._finished)
        self._task.start()

    def _completed(self, result, error, cancelled):
        self._completion = (result, error, cancelled)

    def _finished(self):
        task = self._task.task
        result, error, cancelled = self._completion or (None, "任务未返回结果", False)
        self._task.deleteLater()
        self._task = None
        self._busy = False
        for field in self._controls:
            field.setEnabled(True)
        for button in (self.preview_button, self.history_button, self.undo_button):
            button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if error:
            self.status.setText(f"处理失败：{error}")
        elif task == "preview":
            self.preview_model.set_plan(result)
            if result.items:
                self.preview_table.selectRow(0)
            if not cancelled:
                self.plan = result
            ready = sum(item.status == "ready" for item in result.items)
            self.status.setText(f"预览 {len(result.items)} 项，可执行 {ready} 项。" + ("已取消，请重新预览。" if cancelled else "请核对后确认执行。"))
            self.execute_button.setEnabled(not cancelled and ready > 0)
        elif task == "execute":
            self.preview_model.set_plan(self.plan, result)
            self.plan = None
            successful = sum(item.status == "success" for item in result)
            failed = sum(item.status in {"failed", "needs_review"} for item in result)
            self.status.setText(f"成功 {successful}，失败 / 待核对 {failed}，跳过 {len(result)-successful-failed}。" + ("已取消；已完成操作保留。" if cancelled else "可在操作记录中撤销。"))
            self.files_changed.emit()
        elif task == "history":
            self.history_model.set_entries(result)
            self.status.setText(f"共 {len(result)} 条记录。需人工核对的记录不会自动删除文件。")
        elif task == "undo":
            self.status.setText(STATUS.get(result.status, result.status) + (f"：{result.message}" if result.message else "：撤销完成"))
            self.plan = None
            self.files_changed.emit()
            self._history()

    def _cancel(self):
        if self._task:
            self._task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在停止；允许当前文件完成…")

    def closeEvent(self, event):
        if self._busy:
            self._cancel()
            self.status.setText("请等待当前后台任务停止后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self._busy:
            self._cancel()
            return
        super().reject()

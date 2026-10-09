"""Compact optional OCR controls; concrete engines and SQLite stay in workers."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QMessageBox

from .model_cache import model_dir
from .worker import OCRRuntime, OCRTask


class OCRPanel(QWidget):
    busy = Signal(bool)
    details_ready = Signal(object)

    def __init__(self, options_provider, results_provider, database_path=None, parent=None,
                 model_root=None) -> None:
        super().__init__(parent)
        self.options_provider, self.results_provider = options_provider, results_provider
        self.database_path = database_path
        self.runtime = OCRRuntime(model_root)
        self.task = None
        self._stopping = False
        self._result = None
        self._installed = False
        self._dependencies = False
        self._indexing_allowed = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("图片文字"))
        self.text = QLineEdit()
        self.text.setMaxLength(512)
        self.text.setPlaceholderText("图片内的文字，例如 Windows Update、错误代码；使用下方“搜索”")
        self.text.setToolTip("多个词同时匹配；有图片内容条件时先筛 OCR，再按 AI 相关度排序。只查询已有 OCR。")
        row.addWidget(self.text, 1)
        self.device = QComboBox()
        for label, value in (("OCR 自动", "auto"), ("ONNXRuntime CPU", "ort-cpu"),
                             ("OpenVINO CPU", "ov-cpu"), ("OpenVINO GPU", "ov-gpu"), ("OpenVINO AUTO", "ov-auto")):
            self.device.addItem(label, value)
        row.addWidget(self.device)
        self.install_button = QPushButton("安装 / 检查 OCR 模型")
        self.index_button = QPushButton("建立 OCR 索引…")
        self.cancel_button = QPushButton("取消 OCR")
        self.cancel_button.setEnabled(False)
        for button in (self.install_button, self.index_button, self.cancel_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel("OCR：正在检查本地模型；PP-OCRv6 small，图片和文字不会上传。")
        self.status.setWordWrap(True)
        self.status.setToolTip(str(model_dir(model_root)))
        layout.addWidget(self.status)
        self.install_button.clicked.connect(self.install)
        self.index_button.clicked.connect(self.choose_index)
        self.cancel_button.clicked.connect(self.cancel)
        QTimer.singleShot(0, self.check_status)

    def set_advanced_visible(self, visible: bool) -> None:
        for widget in (self.device,self.install_button,self.index_button):widget.setVisible(visible)

    def set_indexing_allowed(self, allowed: bool) -> None:
        self._indexing_allowed = allowed
        for widget in (self.install_button,self.index_button,self.device):
            widget.setEnabled(allowed and self.task is None)

    def _start(self, operation, options=None, **kwargs) -> None:
        if self._stopping or self.task is not None:
            return
        if operation in {"index","install","check"} and not self._indexing_allowed:return
        options = options or self.options_provider()
        if options is None:
            return
        self.task = OCRTask(self.runtime, operation, options, self.database_path,
                            self.device.currentData(), parent=self, **kwargs)
        self._result = None
        self.task.progress.connect(self.status.setText)
        self.task.completed.connect(lambda value, error: setattr(self, "_result", (value, error)))
        self.task.finished.connect(self._finished)
        for widget in (self.install_button, self.index_button, self.device):
            widget.setEnabled(False)
        self.cancel_button.setEnabled(operation in {"index", "install"})
        if operation in {"index", "install"}:
            self.busy.emit(True)
        self.task.start()

    def _finished(self) -> None:
        task = self.task
        operation = task.operation
        value, error = self._result or (None, "OCR 任务未返回")
        self.task = None
        task.deleteLater()
        if operation in {"index", "install"}:
            self.busy.emit(False)
        for widget in (self.install_button, self.index_button, self.device):
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.set_indexing_allowed(self._indexing_allowed)
        if error:
            self.status.setText(f"OCR 失败：{error}。普通搜索和 AI 搜图仍可使用。")
            return
        if operation in {"status", "check"}:
            if operation == "status" and not self._stopping:
                current = self.options_provider()
                if current is not None and current.folder_scope() != task.options.folder_scope():
                    self.check_status()
                    return
            self._installed, self._dependencies = value["installed"], value["dependencies"]
            for index in range(self.device.count()):
                option = self.device.itemData(index)
                enabled = option == "auto" or option in value["backends"]
                self.device.model().item(index).setEnabled(enabled)
                self.device.setItemData(index, "" if enabled else "本机未检测到该 OCR 设备 / 运行库", Qt.ToolTipRole)
            state = "模型已安装" if self._installed else "模型未安装或校验失败"
            if not self._dependencies:
                state += "，需要安装 [ocr] 依赖"
            self.status.setText(f"OCR：{state}；当前目录已识别 {value['indexed']:,} / {value['total']:,}。")
            self.status.setToolTip(value["cache"])
            if operation == "check":
                QMessageBox.information(self, "OCR 模型检查", f"{state}\n缓存：{value['cache']}\n"
                                        "仅安装模型需要联网，索引和搜索完全离线。")
        elif operation == "index":
            self.status.setText(f"OCR {'已取消，可继续' if value.cancelled else '完成'}：总数 {value.total:,}，"
                f"复用 {value.existing:,}，完成 {value.completed:,}，失败 {value.failed:,}；"
                f"{value.backend or '复用缓存，无需推理'}；{value.pictures_s:.2f} pictures/s。")
        elif operation == "detail":
            self.details_ready.emit(value)
        else:
            self._installed = bool(value)
            self.status.setText("OCR 模型已安装，可离线索引。" if value else "OCR 下载已取消，可重试；已完成文件保留。")

    def check_status(self) -> None:
        if self.task is None:
            self._start("status")

    def install(self) -> None:
        if not self._dependencies:
            QMessageBox.information(self, "OCR 依赖", '请在项目目录运行：\n.\\.venv\\Scripts\\python.exe -m pip install -e ".[ocr]"')
            return
        if self._installed:
            self._start("check")
            return
        answer = QMessageBox.question(self, "安装本地 OCR 模型",
            f"将从 RapidAI 官方模型仓库下载 PP-OCRv6 small 检测 / 识别和方向模型。\n"
            f"缓存：{model_dir(self.runtime.root)}\n图片和文字不会上传。是否安装？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._start("install")

    def choose_index(self) -> None:
        if self.task is not None:
            return
        if not self._installed or not self._dependencies:
            self.install()
            return
        options = self.options_provider()
        if options is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("建立 OCR 索引")
        dialog.setText("已有有效 OCR 会复用。请选择范围：")
        current = dialog.addButton("当前目录全部图片", QMessageBox.ActionRole)
        results = dialog.addButton("当前搜索结果", QMessageBox.ActionRole)
        all_images = dialog.addButton("已索引的所有图片", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec()
        if dialog.clickedButton() == current:
            self._start("index", options.folder_scope())
        elif dialog.clickedButton() == results:
            self._start("index", paths=[row.path for row in self.results_provider() if row.is_image])
        elif dialog.clickedButton() == all_images:
            self._start("index", all_indexed=True)

    def show_details(self, file_uid: str | None) -> None:
        if self.task is not None:
            self.status.setText("OCR 任务尚未结束，请稍后查看识别文字。")
            return
        self._start("detail", file_uid=file_uid)

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消 OCR，当前单图允许完成，已完成识别会保存…")

    def stop(self) -> None:
        self._stopping = True
        if self.task is not None:
            self.task.cancel()
            self.task.wait()

"""Small optional AI controls; concrete inference stays behind AIRuntime."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QComboBox, QSpinBox, QMessageBox, QFileDialog)
from ..paths import get_model_cache_dir
from .worker import AIRuntime, AITask


class AIPanel(QWidget):
    results_ready = Signal(object)
    searching = Signal(bool)

    def __init__(self, options_provider, results_provider, database_path=None, parent=None) -> None:
        super().__init__(parent)
        self.options_provider, self.results_provider = options_provider, results_provider
        self.database_path = database_path
        self.runtime = AIRuntime()
        self.task = None
        self._result = None
        self._installed = False
        self._dependencies = False
        self._indexing_allowed = True
        self._counts = (0, 0, "当前条件")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("图片内容"))
        self.text = QLineEdit()
        self.text.setPlaceholderText("中文语义，例如：夕阳下的湖（留空使用普通搜索）")
        row.addWidget(self.text, 1)
        self.device = QComboBox()
        for title, value in (("自动", "auto"), ("PyTorch CPU", "cpu"), ("CUDA", "cuda"),
                             ("OpenVINO CPU", "ov-cpu"), ("OpenVINO GPU", "ov-gpu")):
            self.device.addItem(title, value)
        row.addWidget(self.device)
        row.addWidget(QLabel("批次（高级）"))
        self.batch_size = QSpinBox()
        self.batch_size.setRange(0, 64)
        self.batch_size.setSpecialValueText("自动")
        self.batch_size.setToolTip("0 为自动：使用本机实际测量并缓存的配置；也可手工设置批次大小。")
        row.addWidget(self.batch_size)
        row.addWidget(QLabel("最多结果"))
        self.top_k = QSpinBox()
        self.top_k.setRange(1, 10000)
        self.top_k.setValue(100)
        row.addWidget(self.top_k)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.install_button = QPushButton("安装 / 检查模型")
        self.index_button = QPushButton("建立 AI 图片索引…")
        self.search_button = QPushButton("文本搜图")
        self.similar_button = QPushButton("选择图片找相似…")
        self.cancel_button = QPushButton("取消 AI")
        self.cancel_button.setEnabled(False)
        for button in (self.install_button, self.index_button, self.search_button,
                       self.similar_button, self.cancel_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel("AI：正在检查本地安装状态；Chinese-CLIP ViT-B/16")
        self.info = QLabel("Chinese-CLIP ViT-B/16 · 512 维 · 设备待加载 · AI 索引待检查")
        self.info.setWordWrap(True)
        self.info.setToolTip(f"模型缓存：{get_model_cache_dir()}")
        self.status.setWordWrap(True)
        self.status.setToolTip(f"模型缓存：{get_model_cache_dir()}\n图片不会上传；相关度是余弦相似程度，并非概率。")
        layout.addWidget(self.info)
        layout.addWidget(self.status)
        self.install_button.clicked.connect(self.install)
        self.index_button.clicked.connect(self.choose_index)
        self.search_button.clicked.connect(lambda: self.search())
        self.similar_button.clicked.connect(self.choose_similar)
        self.cancel_button.clicked.connect(self.cancel)
        self.text.returnPressed.connect(lambda: self.search())
        QTimer.singleShot(0, self.check_status)

    def set_advanced_visible(self, visible: bool) -> None:
        row = self.layout().itemAt(0).layout()
        for i in range(2,row.count()):
            widget = row.itemAt(i).widget()
            if widget is not None:widget.setVisible(visible)
        self.install_button.setVisible(visible)
        self.index_button.setVisible(visible)

    def _start(self, operation, options=None, **kwargs) -> None:
        if self.task is not None:
            return
        if operation in {"index", "download"} and not self._indexing_allowed:
            return
        options = options or self.options_provider()
        if options is None:
            return
        self.task = AITask(self.runtime, operation, options, self.database_path,
                           self.device.currentData(), batch_size=self.batch_size.value(), parent=self, **kwargs)
        self._result = None
        self.task.progress.connect(self.status.setText)
        self.task.candidate_counts.connect(lambda indexed, total: setattr(
            self, "_counts", (indexed, total-indexed, "当前条件")))
        self.task.completed.connect(lambda value, error: setattr(self, "_result", (value, error)))
        self.task.finished.connect(self._finished)
        for button in (self.install_button, self.index_button, self.search_button, self.similar_button):
            button.setEnabled(False)
        self.device.setEnabled(False)
        self.batch_size.setEnabled(False)
        self.cancel_button.setEnabled(operation != "status")
        if operation in {"index", "search"}:
            self.searching.emit(True)
        self.task.start()

    def _finished(self) -> None:
        operation = self.task.operation
        cancelled = self.task.cancelled.is_set()
        value, error = self._result or (None, "任务未返回结果")
        self.task.deleteLater()
        self.task = None
        for button in (self.install_button, self.index_button, self.search_button, self.similar_button):
            button.setEnabled(True)
        self.device.setEnabled(True)
        self.batch_size.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.set_indexing_allowed(self._indexing_allowed)
        for index in range(self.device.count()):
            reason = self.runtime.unavailable.get(self.device.itemData(index))
            if reason:
                self.device.model().item(index).setEnabled(False)
                self.device.setItemData(index, reason, Qt.ToolTipRole)
        if operation in {"index", "search"}:
            self.searching.emit(False)
        if error:
            self.status.setText(f"AI 任务失败：{error}。普通搜索仍可使用。")
            return
        if operation == "status":
            availability = {
                "cuda": (value.get('cuda_available', False), "未检测到可用 NVIDIA CUDA 驱动"),
                "ov-cpu": (value.get('openvino', {}).get('available', False), value.get('openvino', {}).get('reason', 'OpenVINO 未安装')),
                "ov-gpu": (any(d.startswith('GPU') for d in value.get('openvino', {}).get('devices', {})), "OpenVINO 未识别 Intel GPU"),
            }
            for index in range(self.device.count()):
                option = self.device.itemData(index)
                if option in availability:
                    enabled, reason = availability[option]
                    if option in value.get('unavailable_backends', {}):
                        enabled, reason = False, value['unavailable_backends'][option]
                    self.device.model().item(index).setEnabled(enabled)
                    self.device.setItemData(index, "实际模型兼容性会在首次加载时检测" if enabled else reason, Qt.ToolTipRole)
            self._installed, self._dependencies = value["installed"], value["dependencies"]
            self._counts = (value['indexed'], value['total']-value['indexed'], "当前条件")
            state = "模型已安装" if self._installed else "模型未安装（约 753 MB）"
            if not self._dependencies:
                state += "；需要安装 [ai] 依赖"
            self.status.setText(f"AI：{state}；Chinese-CLIP ViT-B/16；{value['device']}；"
                                f"当前条件已索引 {value['indexed']:,} / {value['total']:,}，"
                                f"尚未索引 {value['total']-value['indexed']:,}。")
            self.info.setText(f"Chinese-CLIP ViT-B/16 · {state} · {value['device']} · "
                              f"当前条件已索引 {value['indexed']:,}，尚未索引 {value['total']-value['indexed']:,}")
        elif operation == "search":
            indexed, missing, scope = self._counts
            self.info.setText(f"Chinese-CLIP ViT-B/16 · 模型已安装 · {self.runtime.backend.device} · "
                              f"{scope}已索引 {indexed:,}，尚未索引 {missing:,}")
            if not cancelled:
                self.results_ready.emit(value)
            self.status.setText(f"AI 搜图{'已取消' if cancelled else '完成'}：{len(value)} 个结果；"
                                f"{self.runtime.backend.device}；相关度不是概率。未建 AI 索引的图片不会参与。")
        elif operation == "index":
            self._counts = (value.existing+value.completed, value.total-value.existing-value.completed, "本次范围")
            self.info.setText(f"Chinese-CLIP ViT-B/16 · 模型已安装 · {self.runtime.backend.device} · "
                              f"本次范围已索引 {value.existing+value.completed:,}，"
                              f"尚未索引 {value.total-value.existing-value.completed:,}")
            self.status.setText(f"AI 索引{'已取消，可继续' if value.cancelled else '完成'}："
                                f"总数 {value.total}，已有 {value.existing}，本次完成 {value.completed}，"
                                f"失败 {value.failed}，尚未完成 {value.total-value.existing-value.completed}；"
                                f"{self.runtime.backend.device}。")
            if value.completed:
                self.status.setText(self.status.text()+f" batch {value.batch_size}，{value.pictures_s:.1f} pictures/s。")
            elif value.existing == value.total and not value.failed:
                self.status.setText(self.status.text()+" 已有向量已复用，无需推理。")
        else:
            self.status.setText("模型下载已取消；已下载文件保留，可继续。" if cancelled else "模型已安装，可离线使用。")
            self.check_status()

    def check_status(self) -> None:
        if self.task is None:
            self._start("status")

    def set_indexing_allowed(self, allowed: bool) -> None:
        self._indexing_allowed = allowed
        idle = self.task is None
        for button in (self.install_button, self.index_button):
            button.setEnabled(idle and allowed)
            button.setToolTip("" if allowed else "OCR 建索引期间可搜索；建立 AI 索引请等待 OCR 完成。")

    def install(self) -> None:
        if not self._indexing_allowed:
            return
        if not self._dependencies:
            QMessageBox.information(self, "安装 AI 依赖", "请在项目目录运行：\n"
                                    '.\\.venv\\Scripts\\python.exe -m pip install -e ".[ai]"\n'
                                    "CPU / GTX 1060 的具体安装方式见 README。")
            return
        if self._installed:
            QMessageBox.information(self, "本地模型", f"Chinese-CLIP ViT-B/16 已安装。\n"
                                    f"缓存目录：{get_model_cache_dir()}\n无需联网即可推理。")
            self.check_status()
            return
        answer = QMessageBox.question(self, "下载本地模型", "需要从 Hugging Face 下载 Chinese-CLIP ViT-B/16，"
                                      f"约 753 MB。\n缓存目录：{get_model_cache_dir()}\n"
                                      "安装后可离线运行，图片不会上传。是否下载？",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._start("download")

    def _ready(self) -> bool:
        if self.task is not None:
            return False
        if not self._dependencies or not self._installed:
            self.install()
            return False
        return True

    def choose_index(self) -> None:
        if not self._indexing_allowed:
            return
        if not self._ready():
            return
        options = self.options_provider()
        if options is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("建立 AI 图片索引")
        dialog.setText("选择处理范围。只处理图片；已有有效向量会跳过。")
        current = dialog.addButton("当前目录全部图片", QMessageBox.ActionRole)
        results = dialog.addButton("当前搜索结果", QMessageBox.ActionRole)
        specified = dialog.addButton("指定目录…", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec()
        if dialog.clickedButton() == current:
            self._start("index", options.folder_scope())
        elif dialog.clickedButton() == results:
            self._start("index", paths=[item.path for item in self.results_provider() if item.is_image])
        elif dialog.clickedButton() == specified:
            folder = QFileDialog.getExistingDirectory(self, "选择 AI 索引目录", str(options.folder))
            if folder:
                self._start("index", replace(options.folder_scope(), folder=Path(folder)))

    def search(self, image_path: Path | None = None) -> None:
        if not self._ready():
            return
        if image_path is None and not self.text.text().strip():
            self.status.setText("请输入图片内容，例如：一只猫。")
            return
        self._start("search", text=self.text.text().strip(), image_path=image_path, top_k=self.top_k.value())

    def choose_similar(self) -> None:
        if not self._ready():
            return
        name, _ = QFileDialog.getOpenFileName(self, "选择图片，查找相似图片", "",
                                            "图片 (*.jpg *.jpeg *.png *.webp *.nef *.nrw *.bmp *.tif *.tiff);;所有文件 (*)")
        if name:
            self.search(Path(name))

    def cancel(self) -> None:
        if self.task:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消 AI 任务，当前单个推理或下载文件允许完成…")

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task.wait()

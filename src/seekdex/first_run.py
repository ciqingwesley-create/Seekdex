"""Optional first-run steps, with explicit background indexing and downloads."""
from pathlib import Path
from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWizard, QWizardPage, QVBoxLayout, QLabel, QListWidget, QPushButton, QFileDialog
from .product_worker import ProductTask
from .search import SearchOptions


class FirstRunWizard(QWizard):
    directories_ready = Signal(object)

    def __init__(self, settings, main_window):
        super().__init__(main_window)
        self.settings, self.main_window = settings, main_window
        self.roots: list[Path] = []
        self.task = None
        self._result = None
        self._skip_pending = False
        self._close_pending = False
        self.setWindowTitle("欢迎使用 Seekdex · 索星仪")
        self.resize(680,500)
        self.setButtonText(QWizard.NextButton,"下一步")
        self.setButtonText(QWizard.BackButton,"上一步")
        self.setButtonText(QWizard.FinishButton,"进入主界面")
        self.setButtonText(QWizard.CancelButton,"稍后设置，进入主界面")
        page, layout = self.page("欢迎", "本地运行，图片不会上传。\n可以按文件属性搜索，也可选装 AI 图片内容搜索与 OCR 文字识别。\n不会自动移动、重命名或删除你的文件。")
        page, layout = self.page("选择目录", "只会处理你选择的目录；可加入多个目录，不会自动扫描整台电脑。")
        self.directory_list = QListWidget()
        layout.addWidget(self.directory_list)
        add = QPushButton("添加目录…")
        add.clicked.connect(self.add_directory)
        layout.addWidget(add)
        page, layout = self.page("基础文件索引", "扫描只读取路径、大小和修改时间，不批量解码图片。\n允许跳过；稍后搜索时也会渐进建立索引。")
        self.base_status = QLabel("尚未开始")
        layout.addWidget(self.base_status)
        button = QPushButton("现在建立基础索引")
        button.clicked.connect(lambda:self.launch("scan",self.base_status))
        layout.addWidget(button)
        page, layout = self.page("AI 图片搜索（可选）", "Chinese-CLIP ViT-B/16 权重约 753 MB。\n标准版首次安装需要联网；预装版已提供模型。安装后本地运行。未安装时普通搜索仍可用。")
        self.ai_status = QLabel("可直接点击下一步，暂时跳过。")
        layout.addWidget(self.ai_status)
        install = QPushButton("安装 AI 搜图")
        install.clicked.connect(self.install_ai)
        layout.addWidget(install)
        tune = QPushButton("检测后端并自动调优")
        tune.clicked.connect(lambda:self.launch("tune",self.ai_status))
        layout.addWidget(tune)
        page, layout = self.page("OCR（可选）", "OCR 搜索截图或照片中的文字。PP-OCRv6 small 模型约 30.3 MiB。\n标准版首次安装需要联网；预装版已提供模型。图片和识别文字不上传。")
        self.ocr_status = QLabel("可直接点击下一步，暂时跳过。")
        layout.addWidget(self.ocr_status)
        install = QPushButton("安装 OCR")
        install.clicked.connect(self.install_ocr)
        layout.addWidget(install)
        page, layout = self.page("可选索引", "建立 AI / OCR 索引可能耗时，可稍后在索引管理中补全。\n已有有效向量和 OCR 会复用，不会重建。")
        self.index_status = QLabel("尚未开始")
        layout.addWidget(self.index_status)
        for label,kind in (("建立 AI 图片索引","ai"),("建立 OCR 索引","ocr")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False,op=kind:self.launch(op,self.index_status))
            layout.addWidget(button)
        page, layout = self.page("完成", "可以进入主界面，模型或索引失败不会影响普通搜索。")
        self.summary = QLabel("目录和索引状态可在“索引管理”查看。")
        layout.addWidget(self.summary)
        self.setOption(QWizard.HaveCustomButton1,True)
        self.setButtonText(QWizard.CustomButton1,"跳过当前任务")
        self.customButtonClicked.connect(self.skip_task)
        self.currentIdChanged.connect(self.page_changed)

    def page(self,title,text):
        page = QWizardPage()
        page.setTitle(title)
        layout = QVBoxLayout(page)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.addPage(page)
        return page,layout

    def add_directory(self):
        value = QFileDialog.getExistingDirectory(self,"选择图片 / 文件目录")
        if value and Path(value) not in self.roots:
            self.roots.append(Path(value))
            self.directory_list.addItem(value)

    def parameters(self):
        window = self.main_window
        return dict(ai_runtime=window.ai.runtime, ocr_runtime=window.ocr.runtime,
            ai_backend=window.ai.device.currentData(),ai_batch=window.ai.batch_size.value(),
            ocr_backend=window.ocr.device.currentData())

    def launch(self,kind,label):
        if self.task is not None:
            return
        if kind in {"scan","ai","ocr","add","tune"} and not self.roots:
            label.setText("请先添加目录；也可以跳过，稍后在主界面选择。")
            return
        self.task = ProductTask(kind,self.main_window._effective_database_path(),roots=self.roots,
                               parameters=self.parameters(),parent=self)
        self.attach(label)

    def attach(self,label):
        task = self.task
        self._result = None
        self.button(QWizard.NextButton).setEnabled(False)
        self.button(QWizard.BackButton).setEnabled(False)
        self.button(QWizard.FinishButton).setEnabled(False)
        label.setText("正在准备…")
        task.progress.connect(label.setText)
        task.completed.connect(lambda value,error:setattr(self,"_result",(value,error)))
        def finished():
            self.task = None
            task.deleteLater()
            value,error = self._result or (None,"任务未返回")
            label.setText(f"未完成：{error}；可以跳过继续使用普通搜索。" if error else "任务结束；已完成内容会保存。")
            if task.operation == "status" and not error:
                self.summary.setText(f"已索引文件：{value['files']:,}\nAI：{value['embeddings']:,}\nOCR：{value['ocr']:,}")
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.BackButton).setEnabled(True)
            self.button(QWizard.FinishButton).setEnabled(True)
            if self._close_pending:
                self.reject()
            elif self._skip_pending:
                self._skip_pending = False
                self.next()
            elif task.operation == "download" and not error and value:
                self.launch("tune",self.ai_status)
        task.finished.connect(finished)
        task.start()

    def install_ai(self):
        if self.task is not None:return
        from .ai.worker import AITask
        from .ai.model_cache import installed
        if installed(self.main_window.ai.runtime.model_cache_dir):
            self.launch("tune",self.ai_status)
            return
        self.task = AITask(self.main_window.ai.runtime,"download",SearchOptions(self.roots[0] if self.roots else Path.home()),
                           self.main_window._effective_database_path(),parent=self)
        self.attach(self.ai_status)

    def install_ocr(self):
        if self.task is not None:return
        from .ocr.worker import OCRTask
        self.task = OCRTask(self.main_window.ocr.runtime,"install",SearchOptions(self.roots[0] if self.roots else Path.home()),
                            self.main_window._effective_database_path(),parent=self)
        self.attach(self.ocr_status)

    def skip_task(self,*args):
        if self.task is None:
            self.next()
        else:
            self._skip_pending = True
            self.task.cancel()

    def page_changed(self,page_id):
        if page_id == 6 and self.task is None:
            self.task = ProductTask("status",self.main_window._effective_database_path(),parent=self)
            self.attach(self.summary)

    def finish_profile(self):
        self.settings.complete_onboarding()
        self.directories_ready.emit(self.roots)

    def accept(self):
        if self.task is not None:
            return
        self.finish_profile()
        super().accept()

    def reject(self):
        if self.task is not None:
            self._close_pending = True
            self.task.cancel()
            return
        self.finish_profile()
        super().reject()

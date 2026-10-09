"""Product settings; slow inspection and maintenance use ProductTask."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QStackedWidget,
    QWidget, QFormLayout, QCheckBox, QComboBox, QSpinBox, QLabel, QLineEdit,
    QPushButton, QDialogButtonBox, QFileDialog, QMessageBox)
from .product_worker import ProductTask
from .paths import get_thumbnail_cache_dir, get_model_cache_dir, get_ocr_model_root


class SettingsDialog(QDialog):
    def __init__(self, settings, database_path, parent=None):
        super().__init__(parent)
        self.settings, self.database_path = settings, database_path
        self.task = None
        self.controls = {}
        self._closing = False
        self.setWindowTitle("Seekdex · 索星仪 — 设置")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.categories = QListWidget()
        self.categories.setFixedWidth(120)
        self.pages = QStackedWidget()
        row.addWidget(self.categories)
        row.addWidget(self.pages, 1)
        layout.addLayout(row, 1)
        self.status = QLabel("正在读取本机状态…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.general()
        self.search()
        self.thumbnails()
        self.ai()
        self.ocr()
        self.data()
        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.categories.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.start("status")

    def page(self, label):
        self.categories.addItem(label)
        widget = QWidget()
        form = QFormLayout(widget)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.pages.addWidget(widget)
        return form

    def check(self, form, label, key):
        control = QCheckBox(label)
        control.setChecked(self.settings.get(key))
        self.controls[key] = control
        form.addRow(control)
        return control

    def combo(self, form, label, key, choices):
        control = QComboBox()
        for title, value in choices:
            control.addItem(title, value)
        control.setCurrentIndex(max(0, control.findData(self.settings.get(key))))
        self.controls[key] = control
        form.addRow(label, control)
        return control

    def spin(self, form, label, key, maximum=100000):
        control = QSpinBox()
        control.setRange(0, maximum)
        control.setSpecialValueText("自动 / 不限")
        control.setValue(self.settings.get(key))
        self.controls[key] = control
        form.addRow(label, control)
        return control

    def directory(self, form, label, key, default):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(str(self.settings.get(key, str(default))))
        button = QPushButton("选择…")
        def choose():
            path = QFileDialog.getExistingDirectory(self,"选择缓存目录",edit.text())
            if path:
                edit.setText(path)
        button.clicked.connect(choose)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        form.addRow(label, row)
        self.controls[key] = edit
        return edit

    def action(self, form, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        form.addRow(button)
        return button

    def general(self):
        form = self.page("常规")
        for label, key in (("恢复上次窗口大小和位置","general/restore_window"),
            ("恢复上次搜索目录","general/restore_folder"),("记住上次筛选条件","general/remember_filters"),
            ("日志允许记录文件路径","general/log_paths")):
            self.check(form,label,key)
        self.combo(form,"双击结果","general/double_click",[("使用系统默认程序打开","open"),("在资源管理器中显示","reveal"),("显示详情","details")])

    def search(self):
        form = self.page("搜索")
        self.spin(form,"普通搜索最多结果（0 不限）","search/result_limit")
        self.combo(form,"默认排序","search/sort",[("文件名","name"),("修改时间：最新优先","modified"),("拍摄时间：最新优先","capture"),("大小：最大优先","size")])
        self.combo(form,"默认视图","search/view",[("列表","list"),("网格","grid")])
        self.check(form,"默认搜索子目录","search/default_recursive")
        self.combo(form,"默认日期类型","search/time_type",[("修改时间","modified"),("拍摄时间","capture")])
        self.spin(form,"AI 默认最多结果","ai/top_k",10000).setMinimum(1)

    def thumbnails(self):
        form = self.page("缩略图")
        self.combo(form,"默认尺寸","thumbnail/size",[("小",0),("中",1),("大",2)])
        self.directory(form,"缓存目录","thumbnail/directory",get_thumbnail_cache_dir())
        self.spin(form,"缓存上限（MiB，0 不限）","thumbnail/limit_mb",100000)
        self.thumbnail_info = QLabel("读取中…")
        form.addRow("当前缓存",self.thumbnail_info)
        self.action(form,"清理缩略图缓存",lambda:self.confirm_clear("thumbnail"))
        form.addRow(QLabel("清理只删除本程序生成的缓存，不会删除原图。新目录会复制已有缓存，原目录保留。"))

    def ai(self):
        form = self.page("AI")
        self.ai_info = QLabel("正在检查本机模型和设备…")
        self.ai_info.setWordWrap(True)
        form.addRow(self.ai_info)
        self.combo(form,"默认后端","ai/backend",[("自动","auto"),("PyTorch CPU","cpu"),("OpenVINO CPU","ov-cpu"),("OpenVINO GPU","ov-gpu"),("CUDA","cuda")])
        self.spin(form,"batch size（0 自动）","ai/batch",64)
        self.directory(form,"模型缓存根目录","ai/model_dir",get_model_cache_dir())
        self.action(form,"清理 Chinese-CLIP 模型缓存",lambda:self.confirm_clear("ai"))
        form.addRow(QLabel("清理不会删除 embedding；下一次 AI 推理前需要重新安装模型。"))

    def ocr(self):
        form = self.page("OCR")
        self.ocr_info = QLabel("正在检查 OCR…")
        self.ocr_info.setWordWrap(True)
        form.addRow(self.ocr_info)
        self.combo(form,"默认后端","ocr/backend",[("自动","auto"),("ONNXRuntime CPU","ort-cpu"),("OpenVINO CPU","ov-cpu"),("OpenVINO GPU","ov-gpu"),("OpenVINO AUTO","ov-auto")])
        self.directory(form,"OCR 模型目录","ocr/model_dir",get_ocr_model_root())
        self.spin(form,"ONNXRuntime CPU 线程数（高级）","ocr/threads",16).setMinimum(1)
        self.action(form,"清理 OCR 模型缓存",lambda:self.confirm_clear("ocr"))
        form.addRow(QLabel("PP-OCRv6 small；保持长边 2048 / 检测 1536，以复用现有 OCR 索引。"))

    def data(self):
        form = self.page("数据")
        self.data_info = QLabel("读取中…")
        self.data_info.setWordWrap(True)
        form.addRow(self.data_info)
        self.action(form,"打开数据目录",lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.database_path.parent))))
        self.action(form,"数据库完整性检查",lambda:self.start("integrity"))
        def export():
            path,_ = QFileDialog.getSaveFileName(self,"导出整理操作日志","operation-log.csv","CSV (*.csv)")
            if path:
                self.start("export",dict(destination=path))
        self.action(form,"导出操作日志…",export)

    def confirm_clear(self, kind):
        if self.task is not None:
            return
        prompt = ("将清理缩略图缓存；原图不变。" if kind=="thumbnail" else
            "将清理本地模型文件，不会删除 embedding / OCR 索引。\n之后再次推理前需要重新安装模型。")
        if QMessageBox.question(self,"确认清理",prompt,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        defaults = {"thumbnail":get_thumbnail_cache_dir(),"ai":get_model_cache_dir(),"ocr":get_ocr_model_root()}
        if kind=="thumbnail":
            self.start("clear_thumbnails",dict(directory=str(defaults[kind])))
        else:
            self.start("clear_models",dict(kind=kind,directory=str(defaults[kind])))

    def start(self, operation, parameters=None):
        if self.task is not None or self._closing:
            return
        self.task = ProductTask(operation,self.database_path,parameters=parameters,parent=self)
        self.task.progress.connect(self.status.setText)
        self.task.completed.connect(lambda value,error:self.receive(operation,value,error))
        task = self.task
        def finished():
            self.task = None
            task.deleteLater()
            if operation != "status" and not self._closing:
                self.start("status")
        task.finished.connect(finished)
        task.start()

    def receive(self, operation, value, error):
        if error:
            self.status.setText(f"操作失败：{error}")
            return
        if operation == "status":
            self.thumbnail_info.setText(f"{value['thumbnail_bytes']/1048576:.1f} MiB")
            current = self.parent().ai.runtime.backend
            device = current.device if current else "尚未加载"
            self.ai_info.setText(f"Chinese-CLIP ViT-B/16：{'已安装' if value['ai_installed'] else '未安装'}\n"
                f"{value['ai_path']}\n当前设备：{device}；OpenVINO：{'可用' if value['openvino']['available'] else '不可用'}；CUDA：{'可用' if value['cuda'] else '不可用'}")
            current_ocr = self.parent().ocr.runtime.backend
            ocr_device = current_ocr.device if current_ocr else "尚未加载"
            self.ocr_info.setText(f"PP-OCRv6 small：{'已安装' if value['ocr_installed'] else '未安装'}\n{value['ocr_path']}\n当前 backend / 设备：{ocr_device}")
            self.data_info.setText(f"{value['database']}\n数据库（含 WAL）：{value['database_bytes']/1048576:.1f} MiB\n"
                f"文件：{value['files']:,}；AI embedding：{value['embeddings']:,}；OCR：{value['ocr']:,}")
            self.status.setText("设置只保存在当前用户配置目录。")
            choices = self.controls["ai/backend"]
            for i in range(choices.count()):
                key = choices.itemData(i)
                enabled = key in {"auto","cpu"} or (key=="cuda" and value["cuda"]) or (key=="ov-cpu" and value["openvino"]["available"]) or (key=="ov-gpu" and any(d.startswith("GPU") for d in value["openvino"]["devices"]))
                choices.model().item(i).setEnabled(enabled)
                choices.setItemData(i,"" if enabled else "本机未检测到该设备 / 运行库",Qt.ToolTipRole)
        else:
            self.status.setText(f"操作完成：{value}")
            if operation in {"integrity","export"}:
                QMessageBox.information(self,"数据维护",str(value))

    def save(self):
        if self.task is not None or self.parent()._settings_job is not None:
            self.status.setText("请等待当前维护任务完成后保存。")
            return
        values = {}
        for key, control in self.controls.items():
            if isinstance(control,QCheckBox): value = control.isChecked()
            elif isinstance(control,QComboBox): value = control.currentData()
            elif isinstance(control,QSpinBox): value = control.value()
            else:
                value = control.text().strip()
                if not Path(value).is_absolute():
                    self.status.setText("缓存目录必须为完整路径。")
                    return
            values[key] = value
        # Cache relocation runs before switching the profile; failures keep old paths.
        self.parent().apply_product_settings(values, self)

    def accept(self):
        self._closing = True
        super().accept()

    def reject(self):
        if self.parent()._settings_job is not None:
            self.status.setText("正在迁移缓存，请等待完成后关闭。")
            return
        if self.task is not None:
            self.task.cancel()
            self.status.setText("正在结束维护任务，请稍后关闭…")
            self.task.finished.connect(self.reject)
            return
        self._closing = True
        super().reject()

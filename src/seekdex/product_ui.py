"""Product shell layered onto the existing search window."""
from pathlib import Path
import json
from PySide6.QtCore import Qt, QSize, QUrl, QStandardPaths
from PySide6.QtGui import QAction, QIcon, QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QListView, QStackedWidget,
    QToolButton, QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QDialogButtonBox, QMessageBox)
from .app_info import APP_NAME, DISPLAY_NAME, VERSION, HOMEPAGE
from .paths import get_database_path, get_model_cache_dir, get_thumbnail_cache_dir, get_ocr_model_root
from .settings import AppSettings
from .resources import resource_path
from .product_worker import ProductTask
from .details_sidebar import DetailsSidebar


class ProductWindowMixin:
    def _init_product_settings(self, settings_path):
        self.config = AppSettings(settings_path)
        self._indexed_roots = []
        self._selected_roots = []
        self._catalog_task = None
        self._index_manager = None
        self._manager_kind = None
        self._settings_dialog = None
        self._settings_job = None
        self._wizard = None
        self._using_grid = False

    def _effective_database_path(self):
        return self._database_path or get_database_path()

    def _install_product_ui(self):
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowIcon(QIcon(str(resource_path("app.ico"))))
        menu = self.menuBar().addMenu("应用")
        for title,callback in (("设置…",self._show_settings),("索引管理…",self._manage_indices),
            ("首次启动向导…",self.show_first_run),("检查更新",lambda:QDesktopServices.openUrl(QUrl(HOMEPAGE+"/releases"))),
            ("关于…",self._show_about),("退出",self.close)):
            action = QAction(title,self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.scope_mode = QComboBox()
        self.scope_mode.addItem("当前文件夹","single")
        self.scope_mode.addItem("全部索引目录","all")
        self.scope_mode.addItem("选择多个目录…","selected")
        self.centralWidget().layout().itemAt(0).layout().insertWidget(1,self.scope_mode)
        self.scope_mode.currentIndexChanged.connect(self._scope_changed)
        layout = self.centralWidget().layout()
        self.more_options = QToolButton()
        self.more_options.setText("更多 AI / OCR 选项")
        self.more_options.setCheckable(True)
        self.more_options.toggled.connect(self.ai.set_advanced_visible)
        self.more_options.toggled.connect(self.ocr.set_advanced_visible)
        layout.insertWidget(layout.indexOf(self.ai),self.more_options)
        self.ai.set_advanced_visible(False)
        self.ocr.set_advanced_visible(False)
        self.grid = QListView()
        from .result_grid import ResultGridDelegate
        self.grid.setItemDelegate(ResultGridDelegate(self.grid))
        self.grid.setModel(self.model)
        self.grid.setModelColumn(0)
        self.grid.setSelectionModel(self.table.selectionModel())
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.grid.setIconSize(QSize(240,180))
        self.grid.setGridSize(QSize(270,245))
        self.grid.doubleClicked.connect(self._open_result)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.verticalScrollBar().valueChanged.connect(self._schedule_visible)
        self.grid.viewport().installEventFilter(self)
        index = layout.indexOf(self.table)
        layout.removeWidget(self.table)
        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.table)
        self.result_stack.addWidget(self.grid)
        layout.insertWidget(index,self.result_stack,1)
        self.table.setSortingEnabled(True)
        view = self.menuBar().addMenu("视图")
        for label,kind in (("列表","list"),("网格","grid")):
            action = QAction(label,self)
            action.triggered.connect(lambda _checked=False,value=kind:self._set_result_view(value))
            view.addAction(action)
        self.details_sidebar = DetailsSidebar(self._effective_database_path(),self)
        self.details_sidebar.similar.connect(self._search_similar)
        self.addDockWidget(Qt.RightDockWidgetArea,self.details_sidebar)
        view.addAction(self.details_sidebar.toggleViewAction())
        self.details_sidebar.hide()
        self._restore_product_preferences()
        self._load_catalog()

    def _restore_product_preferences(self):
        if not self.config.get("general/restore_folder") or not self._preferences.settings.contains("search/folder"):
            self.folder.setText(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or str(Path.home()))
        if not self._preferences.settings.contains("search/recursive"):
            self.recursive.setChecked(self.config.get("search/default_recursive"))
        self.time_type.setCurrentIndex(max(0,self.time_type.findData(self.config.get("search/time_type"))))
        self.thumbnail_size.setCurrentIndex(self.config.get("thumbnail/size"))
        self.ai.device.setCurrentIndex(max(0,self.ai.device.findData(self.config.get("ai/backend"))))
        self.ai.batch_size.setValue(self.config.get("ai/batch"))
        self.ai.top_k.setValue(self.config.get("ai/top_k"))
        self.ocr.device.setCurrentIndex(max(0,self.ocr.device.findData(self.config.get("ocr/backend"))))
        self.ai.runtime.model_cache_dir = Path(self.config.get("ai/model_dir",str(get_model_cache_dir())))
        self.ocr.runtime.root = Path(self.config.get("ocr/model_dir",str(get_ocr_model_root())))
        self.ocr.runtime.threads = self.config.get("ocr/threads")
        self._set_result_view(self.config.get("search/view"))
        if self.config.get("general/restore_window"):
            geometry = self.config.get("window/geometry")
            if geometry is not None:self.restoreGeometry(geometry)
        if self.config.get("general/remember_filters"):
            try:
                values = json.loads(self.config.get("search/filters","{}"))
                self.filename.setText(values.get("filename",""))
                self.extensions.setText(values.get("extensions",""))
                self.ai.text.setText(values.get("ai",""))
                self.ocr.text.setText(values.get("ocr",""))
                self.from_enabled.setChecked(values.get("from_enabled",False))
                self.to_enabled.setChecked(values.get("to_enabled",False))
                from PySide6.QtCore import QDate
                for key,widget in (("from_date",self.from_date),("to_date",self.to_date)):
                    date = QDate.fromString(values.get(key,""),Qt.ISODate)
                    if date.isValid():widget.setDate(date)
                self.image_filters.group.setChecked(values.get("image_filters",False))
                self.image_filters.camera.setCurrentText(values.get("camera",""))
                for name,widget in self.image_filters.bounds.items():widget.setValue(values.get(name,0))
                for name in ("resolution","orientation"):
                    widget = getattr(self.image_filters,name)
                    widget.setCurrentIndex(max(0,widget.findData(values.get(name,""))))
            except (ValueError,TypeError):pass

    def _save_product_preferences(self):
        self.config.set("search/scope_mode",self.scope_mode.currentData())
        self.config.set("search/selected_roots",json.dumps([str(path) for path in self._selected_roots]))
        self.config.set("window/geometry",self.saveGeometry())
        self.config.set("search/view","grid" if self._using_grid else "list")
        self.config.set("thumbnail/size",self.thumbnail_size.currentIndex())
        self.config.set("ai/backend",self.ai.device.currentData())
        self.config.set("ai/batch",self.ai.batch_size.value())
        self.config.set("ai/top_k",self.ai.top_k.value())
        self.config.set("ocr/backend",self.ocr.device.currentData())
        if self.config.get("general/remember_filters"):
            values = dict(filename=self.filename.text(),extensions=self.extensions.text(),ai=self.ai.text.text(),ocr=self.ocr.text.text(),
                from_enabled=self.from_enabled.isChecked(),to_enabled=self.to_enabled.isChecked(),
                from_date=self.from_date.date().toString(Qt.ISODate),to_date=self.to_date.date().toString(Qt.ISODate),
                image_filters=self.image_filters.group.isChecked(),camera=self.image_filters.camera.currentText(),
                resolution=self.image_filters.resolution.currentData(),orientation=self.image_filters.orientation.currentData(),
                **{name:widget.value() for name,widget in self.image_filters.bounds.items()})
            self.config.set("search/filters",json.dumps(values,ensure_ascii=False))
        self.config.save()

    def _set_result_view(self,kind):
        self._using_grid = kind == "grid"
        self.model.grid_mode = self._using_grid
        self.result_stack.setCurrentIndex(1 if self._using_grid else 0)
        self.model.dataChanged.emit(self.model.index(0,0),self.model.index(max(0,len(self.model.results)-1),0))
        self._resize_product_grid()
        self._schedule_visible()

    def _resize_product_grid(self):
        if not hasattr(self,"grid"):return
        size = self.thumbnail_size.currentData()
        # Keep filenames visible even when filters leave a short result viewport.
        height = max(64,min(size.height(),self.grid.viewport().height()-80))
        size = QSize(max(80,round(size.width()*height/size.height())),height)
        self.grid.setIconSize(size)
        self.grid.setGridSize(QSize(size.width()+30,size.height()+70))
        if self._using_grid:self.model.set_preview_size(size)

    def _scope_changed(self):
        if self.scope_mode.currentData() != "selected":return
        dialog = QDialog(self)
        dialog.setWindowTitle("选择搜索目录")
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        for path in self._indexed_roots:
            item = QListWidgetItem(str(path),items)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if path in self._selected_roots else Qt.Unchecked)
        layout.addWidget(items)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self._selected_roots = [Path(items.item(i).text()) for i in range(items.count()) if items.item(i).checkState()==Qt.Checked]
        else:self.scope_mode.setCurrentIndex(0)

    def _search_scope_roots(self):
        mode = self.scope_mode.currentData() if hasattr(self,"scope_mode") else "single"
        return tuple(self._indexed_roots if mode == "all" else self._selected_roots if mode == "selected" else ())

    def _load_catalog(self):
        if self._catalog_task is not None or self._closing:return
        self._catalog_task = ProductTask("catalog",self._effective_database_path(),parent=self)
        task = self._catalog_task
        task.completed.connect(lambda value,error:self._catalog_received(value) if not error else None)
        def finished():
            self._catalog_task = None
            task.deleteLater()
        task.finished.connect(finished)
        task.start()

    def _catalog_received(self,items):
        self._indexed_roots = [Path(item["root_path"]) for item in items]
        if not self._closing and not getattr(self,"_restored_scope",False):
            self._restored_scope = True
            mode = self.config.get("search/scope_mode","single")
            try:self._selected_roots = [Path(value) for value in json.loads(self.config.get("search/selected_roots","[]"))]
            except (ValueError,TypeError):self._selected_roots = []
            if mode == "all":self.scope_mode.setCurrentIndex(1)
            elif mode == "selected":
                self.scope_mode.blockSignals(True)
                self.scope_mode.setCurrentIndex(2)
                self.scope_mode.blockSignals(False)

    def _manage_indices(self):
        if self._index_manager is None:
            from .index_manager import IndexManager
            self._index_manager = IndexManager(self._effective_database_path(),self._runtime_parameters(),self)
            self._index_manager.roots_changed.connect(lambda paths:setattr(self,"_indexed_roots",paths))
            self._index_manager.job_running.connect(self._managed_busy)
        elif self._index_manager.task is None:self._index_manager.start("catalog")
        self._index_manager.show()
        self._index_manager.raise_()

    def _runtime_parameters(self):
        return dict(ai_runtime=self.ai.runtime,ocr_runtime=self.ocr.runtime,ai_backend=self.ai.device.currentData(),
                    ai_batch=self.ai.batch_size.value(),ocr_backend=self.ocr.device.currentData())

    def _managed_busy(self,kind,busy):
        self._manager_kind = kind if busy else None
        self._update_task_controls()

    def _show_about(self):
        from .about import AboutDialog
        AboutDialog(self).exec()

    def _show_settings(self):
        if self._worker or self._metadata_task or self.ai.task or self.ocr.task or self._manager_kind:
            self.status.setText("请等待当前任务结束后再修改运行设置。")
            return
        from .settings_dialog import SettingsDialog
        self._settings_dialog = SettingsDialog(self.config,self._effective_database_path(),self)
        self._settings_dialog.exec()
        self.ai.check_status()
        self.ocr.check_status()

    def apply_product_settings(self,values,dialog):
        old_paths = {"ai/model_dir":self.ai.runtime.model_cache_dir or get_model_cache_dir(),
                     "ocr/model_dir":self.ocr.runtime.root or get_ocr_model_root(),
                     "thumbnail/directory":self._cache_dir or get_thumbnail_cache_dir()}
        pending = [(old_paths[key],Path(values[key])) for key in old_paths if Path(values[key])!=old_paths[key]]
        def finish():
            if self._closing:return
            for key,value in values.items():self.config.set(key,value)
            self.config.save()
            self.ai.runtime.backend = None
            self.ai.runtime.vectors = None
            self.ai.runtime.model_cache_dir = Path(values["ai/model_dir"])
            self.ocr.runtime.backend = None
            self.ocr.runtime.root = Path(values["ocr/model_dir"])
            self.ocr.runtime.threads = values["ocr/threads"]
            self.ai.device.setCurrentIndex(max(0,self.ai.device.findData(values["ai/backend"])))
            self.ai.batch_size.setValue(values["ai/batch"])
            self.ai.top_k.setValue(values["ai/top_k"])
            self.ocr.device.setCurrentIndex(max(0,self.ocr.device.findData(values["ocr/backend"])))
            self.thumbnail_size.setCurrentIndex(values["thumbnail/size"])
            self.time_type.setCurrentIndex(max(0,self.time_type.findData(values["search/time_type"])))
            self.recursive.setChecked(values["search/default_recursive"])
            self._set_result_view(values["search/view"])
            self._cache_dir = Path(values["thumbnail/directory"])
            loader = self._image_loader
            self._generation += 1
            loader.stop()
            def replace_loader():
                from .image_loader import ImageLoader
                loader.deleteLater()
                if self._closing:return
                self._image_loader = ImageLoader(self._database_path,self._cache_dir,parent=self)
                self._image_loader.image_ready.connect(self._image_ready)
                self._image_loader.start()
                self._schedule_visible()
            if loader.isRunning():loader.finished.connect(replace_loader)
            else:replace_loader()
            dialog.accept()
        def next_path():
            if self._closing:return
            if not pending:
                finish()
                return
            source,target = pending.pop(0)
            task = ProductTask("relocate_cache",self._effective_database_path(),parameters=dict(source=str(source),target=str(target)),parent=self)
            self._settings_job = task
            outcome = []
            task.progress.connect(dialog.status.setText)
            task.completed.connect(lambda value,error:outcome.append(error))
            def done():
                self._settings_job = None
                task.deleteLater()
                if outcome and outcome[0]:dialog.status.setText("缓存迁移失败，设置未切换："+outcome[0])
                else:next_path()
            task.finished.connect(done)
            task.start()
        next_path()

    def show_first_run(self):
        if self._wizard is not None and self._wizard.isVisible():return
        from .first_run import FirstRunWizard
        self._wizard = FirstRunWizard(self.config,self)
        self._wizard.directories_ready.connect(self._wizard_directories)
        self._wizard.finished.connect(lambda *_:(self.ai.check_status(),self.ocr.check_status()))
        self._wizard.open()

    def _wizard_directories(self,roots):
        if not roots:return
        self.folder.setText(str(roots[0]))
        self._save_search_scope()
        self._selected_roots = roots
        if self._catalog_task is not None:
            self._catalog_task.finished.connect(lambda:self._wizard_directories(roots))
            return
        task = ProductTask("add",self._effective_database_path(),roots=roots,parent=self)
        self._catalog_task = task
        task.completed.connect(lambda value,error:self._catalog_received(value) if not error else None)
        task.finished.connect(lambda:(setattr(self,"_catalog_task",None),task.deleteLater()))
        task.start()
        if len(roots)>1:self.scope_mode.setCurrentIndex(1)

    def _search_similar(self,path):
        if not self._worker and not self._metadata_task and not self._manager_kind:self.ai.search(path)

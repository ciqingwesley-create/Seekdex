"""PySide6 desktop interface."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QDate, QEvent, QModelIndex, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .image_loader import ImageLoader
from .capture_time import source_label
from .organize.dialog import OrganizeDialog
from .paths import path_key
from .search import SearchOptions, SearchResult, parse_extensions
from .worker import SearchThread
from .ai.panel import AIPanel
from .image_filter_widget import ImageFilterWidget
from .image_metadata import camera_display_name
from .metadata_worker import MetadataThread, MetadataStatusThread
from .worker import result_from_record
from .preferences import SearchPreferences
from .result_table import ResultTable
from .ocr.panel import OCRPanel
from .ocr.details import OCRDetailsDialog
from .ocr.text import query_terms
from .product_ui import ProductWindowMixin


class ResultModel(QAbstractTableModel):
    HEADERS = ("缩略图", "文件名", "文件路径", "大小", "修改时间", "分辨率", "拍摄时间", "时间来源", "相关度", "拍摄设备", "像素", "类型")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.results: list[SearchResult] = []
        self._rows: dict[str, int] = {}
        self._pixmaps: dict[int, QPixmap] = {}
        self.preview_size = QSize(240, 180)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.results)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.results):
            return None
        item = self.results[index.row()]
        col = index.column()
        if role == Qt.DecorationRole and col == 0 and item.thumbnail:
            if index.row() not in self._pixmaps:
                pixmap = QPixmap()
                pixmap.loadFromData(item.thumbnail, "JPEG")
                self._pixmaps[index.row()] = pixmap.scaled(
                    self.preview_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                if len(self._pixmaps) > 128:
                    self._pixmaps.pop(next(iter(self._pixmaps)))
            return self._pixmaps[index.row()]
        if role == Qt.DisplayRole:
            if col == 0 and getattr(self,"grid_mode",False):
                score = f"\n相关度 {item.similarity:.3f}" if item.similarity is not None else ""
                ocr = "\nOCR 命中" if getattr(self,"ocr_query","") else ""
                return item.path.name+score+ocr
            if col == 11:
                return item.path.suffix.lstrip(".").upper() or "文件"
            if col == 1:
                return item.path.name
            if col == 2:
                return str(item.path)
            if col == 3:
                return _format_size(item.size)
            if col == 4:
                return datetime.fromtimestamp(item.modified).strftime("%Y-%m-%d %H:%M")
            if col == 5 and item.width is not None:
                return f"{item.width} × {item.height}"
            if col == 6:
                return item.capture_time_text or (datetime.fromtimestamp(item.modified).isoformat(timespec="seconds") if not item.is_image else "待读取")
            if col == 7:
                return source_label(item.capture_time_source or ("filesystem_mtime" if not item.is_image else None))
            if col == 8 and item.similarity is not None:
                return f"{item.similarity:.3f}"
            if col == 9 and item.is_image:
                return camera_display_name(item.camera_make, item.camera_model) or ("未知 / 无 EXIF" if item.metadata_version else "待读取")
            if col == 10 and item.width is not None and item.height is not None:
                return f"{item.width*item.height/1_000_000:.1f} MP"
        if role == Qt.ToolTipRole:
            if col == 8:
                return "图文 / 图像向量的余弦相似程度，不代表概率或确定性。"
            value = self.data(index, Qt.DisplayRole)
            return f"{value}\n{item.path}" if value and col not in (1, 2) else str(item.path)
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self.results.clear()
        self._rows.clear()
        self._pixmaps.clear()
        self.endResetModel()

    def append(self, batch: list[SearchResult]) -> None:
        if not batch:
            return
        additions: list[SearchResult] = []
        for item in batch:
            key = path_key(item.path)
            row = self._rows.get(key)
            if row is None:
                self._rows[key] = len(self.results) + len(additions)
                additions.append(item)
                continue
            if row >= len(self.results):
                additions[row - len(self.results)] = item
                continue
            previous = self.results[row]
            if (previous.mtime_ns, previous.size) == (item.mtime_ns, item.size):
                item = replace(
                    item,
                    width=item.width if item.width is not None else previous.width,
                    height=item.height if item.height is not None else previous.height,
                    thumbnail=item.thumbnail or previous.thumbnail,
                    capture_time=item.capture_time if item.capture_time is not None else previous.capture_time,
                    capture_time_source=item.capture_time_source or previous.capture_time_source,
                    capture_time_text=item.capture_time_text or previous.capture_time_text,
                    file_uid=item.file_uid or previous.file_uid,
                    similarity=item.similarity if item.similarity is not None else previous.similarity,
                    camera_make=item.camera_make if item.metadata_version else previous.camera_make,
                    camera_model=item.camera_model if item.metadata_version else previous.camera_model,
                    metadata_version=max(item.metadata_version, previous.metadata_version),
                )
            self.results[row] = item
            self._pixmaps.pop(row, None)
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.HEADERS) - 1))
        if additions:
            first = len(self.results)
            self.beginInsertRows(QModelIndex(), first, first + len(additions) - 1)
            self.results.extend(additions)
            self.endInsertRows()

    def remove_keys(self, keys: list[str]) -> None:
        rows = sorted({self._rows[key] for key in keys if key in self._rows}, reverse=True)
        for row in rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            self.results.pop(row)
            self.endRemoveRows()
        if rows:
            self._rows = {path_key(item.path): row for row, item in enumerate(self.results)}
            self._pixmaps.clear()

    def set_preview_size(self, size: QSize) -> None:
        self.preview_size = size
        self._pixmaps.clear()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        persistent = self.persistentIndexList()
        previous = [path_key(self.results[index.row()].path) for index in persistent]
        def value(item):
            return {0:item.path.name.casefold(),1:item.path.name.casefold(),2:str(item.path).casefold(),
                3:item.size,4:item.modified,5:(item.width or 0)*(item.height or 0),6:item.capture_time or item.modified,
                7:item.capture_time_source or "",8:item.similarity if item.similarity is not None else -1,
                9:camera_display_name(item.camera_make,item.camera_model),10:(item.width or 0)*(item.height or 0),
                11:item.path.suffix.casefold()}.get(column,item.path.name.casefold())
        self.layoutAboutToBeChanged.emit()
        self.results.sort(key=lambda item:(value(item),path_key(item.path)),reverse=order==Qt.DescendingOrder)
        self._rows = {path_key(item.path):row for row,item in enumerate(self.results)}
        self._pixmaps.clear()
        self.changePersistentIndexList(persistent,[self.index(self._rows[key],index.column()) for key,index in zip(previous,persistent)])
        self.layoutChanged.emit()
        if self.results:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.results) - 1, 0), [Qt.DecorationRole]
            )


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def _start_of_day(date: QDate) -> float:
    return datetime.combine(date.toPython(), time.min).timestamp()


class MainWindow(ProductWindowMixin, QMainWindow):
    def __init__(self, database_path: Path | None = None, cache_dir: Path | None = None,
                 *, settings_path: Path | None = None) -> None:
        super().__init__()
        self._database_path = database_path
        self._cache_dir = cache_dir
        # Explicit database overrides keep test / standalone profiles isolated.
        if settings_path is None and database_path is not None:
            settings_path = database_path.parent / "settings.ini"
        self._preferences = SearchPreferences(settings_path)
        self._init_product_settings(settings_path)
        self.setWindowTitle("本地图片 / 文件搜索")
        self.resize(1200, 720)
        self._worker: SearchThread | None = None
        self._completion: tuple[int, bool, str] | None = None
        self._generation = 0
        self._loading_enabled = False
        self._scan_status = ""
        self._metadata_task: MetadataThread | None = None
        self._metadata_status_task: MetadataStatusThread | None = None
        self._closing = False

        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)

        folder_row = QHBoxLayout()
        self.folder = QLineEdit(self._preferences.folder)
        self.folder.setPlaceholderText("选择要搜索的文件夹")
        self.browse_button = QPushButton("选择文件夹…")
        self.browse_button.clicked.connect(self._browse)
        folder_row.addWidget(QLabel("文件夹"))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.browse_button)
        layout.addLayout(folder_row)

        form = QFormLayout()
        self.filename = QLineEdit()
        self.filename.setPlaceholderText("文件名包含的文字，留空不限")
        self.extensions = QLineEdit()
        self.extensions.setPlaceholderText("例如 jpg, png, webp, nef；留空不限")
        form.addRow("文件名", self.filename)
        form.addRow("扩展名", self.extensions)
        layout.addLayout(form)

        options_row = QHBoxLayout()
        self.recursive = QCheckBox("包含子文件夹")
        self.recursive.setChecked(self._preferences.recursive)
        self.time_type = QComboBox()
        self.time_type.addItem("修改时间", "modified")
        self.time_type.addItem("拍摄时间", "capture")
        self.from_enabled = QCheckBox("日期从")
        self.from_date = QDateEdit(QDate.currentDate().addMonths(-1))
        self.from_date.setCalendarPopup(True)
        self.from_date.setEnabled(False)
        self.from_enabled.toggled.connect(self.from_date.setEnabled)
        self.to_enabled = QCheckBox("至")
        self.to_date = QDateEdit(QDate.currentDate())
        self.to_date.setCalendarPopup(True)
        self.to_date.setEnabled(False)
        self.to_enabled.toggled.connect(self.to_date.setEnabled)
        for widget in (self.recursive, self.time_type, self.from_enabled, self.from_date, self.to_enabled, self.to_date):
            options_row.addWidget(widget)
        options_row.addStretch()
        layout.addLayout(options_row)
        self.image_filters = ImageFilterWidget(self)
        layout.addWidget(self.image_filters)

        self.ai = AIPanel(self._search_options, lambda: self.model.results,
                          database_path, self)
        self.ai.results_ready.connect(self._ai_results)
        self.ai.searching.connect(self._ai_busy)
        layout.addWidget(self.ai)
        self.ocr = OCRPanel(self._search_options, lambda: self.model.results, database_path, self)
        self.ocr.busy.connect(self._ocr_busy)
        self.ocr.details_ready.connect(self._ocr_details_ready)
        layout.addWidget(self.ocr)

        action_row = QHBoxLayout()
        self.search_button = QPushButton("搜索")
        self.refresh_button = QPushButton("刷新索引")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.organize_button = QPushButton("按时间整理…")
        self.organize_button.clicked.connect(self._organize)
        self.metadata_button = QPushButton("补全图片元数据")
        self.metadata_button.clicked.connect(self._start_metadata)
        self.search_button.clicked.connect(lambda: self._start_search())
        self.refresh_button.clicked.connect(lambda: self._start_search(refresh=True))
        self.cancel_button.clicked.connect(self._cancel_search)
        action_row.addWidget(self.search_button)
        action_row.addWidget(self.refresh_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.organize_button)
        action_row.addWidget(self.metadata_button)
        action_row.addStretch()
        action_row.addWidget(QLabel("缩略图大小"))
        self.thumbnail_size = QComboBox()
        self.thumbnail_size.addItem("小", QSize(160, 120))
        self.thumbnail_size.addItem("中", QSize(240, 180))
        self.thumbnail_size.addItem("大", QSize(320, 240))
        self.thumbnail_size.setCurrentIndex(1)
        action_row.addWidget(self.thumbnail_size)
        layout.addLayout(action_row)

        self.model = ResultModel(self)
        self.table = ResultTable()
        self.table.setModel(self.model)
        self.table.preview_size_changed.connect(self.model.set_preview_size)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setIconSize(QSize(240, 180))
        self.table.verticalHeader().setDefaultSectionSize(192)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._open_result)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.verticalScrollBar().valueChanged.connect(self._schedule_visible)
        self.table.viewport().installEventFilter(self)
        self.thumbnail_size.currentIndexChanged.connect(self._resize_thumbnails)
        layout.addWidget(self.table, 1)

        self.image_details = QLabel("选择图片可查看设备、分辨率和实际像素数量。")
        self.image_details.setWordWrap(True)
        details_row = QHBoxLayout()
        details_row.addWidget(self.image_details, 1)
        self.ocr_details_button = QPushButton("查看识别文字…")
        self.ocr_details_button.setEnabled(False)
        self.ocr_details_button.clicked.connect(self._show_ocr_details)
        details_row.addWidget(self.ocr_details_button)
        layout.addLayout(details_row)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._show_image_details())
        self.model.dataChanged.connect(lambda *_: self._show_image_details())
        self.model.modelReset.connect(self._show_image_details)

        self.status = QLabel("请选择文件夹并搜索。双击结果用系统默认程序打开。")
        layout.addWidget(self.status)
        self._image_loader = ImageLoader(database_path, cache_dir, parent=self)
        self._image_loader.image_ready.connect(self._image_ready)
        self._image_loader.start()
        self.folder.editingFinished.connect(self.ai.check_status)
        self.folder.editingFinished.connect(self.ocr.check_status)
        self.folder.editingFinished.connect(self._check_metadata_status)
        self.folder.editingFinished.connect(self._save_search_scope)
        self.recursive.toggled.connect(self._check_metadata_status)
        self.recursive.toggled.connect(lambda *_: self.ocr.check_status())
        self.recursive.toggled.connect(self._save_search_scope)
        self._install_product_ui()

    def _save_search_scope(self, *_args) -> None:
        self._preferences.save(self.folder.text(), self.recursive.isChecked())

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择搜索文件夹", self.folder.text())
        if folder:
            self.folder.setText(folder)
            self._save_search_scope()
            self._check_metadata_status()
            self.ocr.check_status()

    def _resize_thumbnails(self) -> None:
        self._resize_product_grid()
        size = self.thumbnail_size.currentData()
        self.table.set_preview_size(size)
        self._schedule_visible()

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.table.viewport() or hasattr(self,"grid") and obj is self.grid.viewport()) and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._queue_visible)
            if hasattr(self,"grid") and obj is self.grid.viewport():
                QTimer.singleShot(0,self._resize_product_grid)
        return super().eventFilter(obj, event)

    def _schedule_visible(self, *_args) -> None:
        QTimer.singleShot(0, self._queue_visible)

    def _queue_visible(self) -> None:
        if not self._loading_enabled or not self.model.results:
            return
        if self._using_grid:
            size = self.grid.gridSize()
            columns = max(1,self.grid.viewport().width()//max(1,size.width()))
            first = (self.grid.verticalScrollBar().value()//max(1,size.height()))*columns
            last = first+(self.grid.viewport().height()//max(1,size.height())+2)*columns
            self._image_loader.set_visible(self.model.results[first:min(len(self.model.results),last)],self._generation)
            return
        viewport = self.table.viewport()
        first = self.table.rowAt(0)
        if first < 0:
            return
        last = self.table.rowAt(max(0, viewport.height() - 1))
        if last < 0:
            last = min(len(self.model.results) - 1, first + max(1, viewport.height() // self.table.verticalHeader().defaultSectionSize()))
        self._image_loader.set_visible(
            self.model.results[first:min(len(self.model.results), last + 5)],
            self._generation,
        )

    def _image_ready(self, generation: int, result: SearchResult) -> None:
        if generation == self._generation:
            self.model.append([result])

    def _search_options(self) -> SearchOptions | None:
        folder = Path(self.folder.text().strip()).expanduser()
        if self.from_enabled.isChecked() and self.to_enabled.isChecked() and self.from_date.date() > self.to_date.date():
            QMessageBox.warning(self, "日期范围无效", "开始日期不能晚于结束日期。")
            return
        options = SearchOptions(
            folder=self._search_scope_roots()[0] if self._search_scope_roots() else folder,
            recursive=self.recursive.isChecked(),
            filename=self.filename.text(),
            extensions=parse_extensions(self.extensions.text()),
            modified_from=_start_of_day(self.from_date.date()) if self.from_enabled.isChecked() else None,
            modified_before=_start_of_day(self.to_date.date().addDays(1)) if self.to_enabled.isChecked() else None,
            time_type=self.time_type.currentData(),
            ocr_text=self.ocr.text.text() if hasattr(self, "ocr") else "",
            folders=self._search_scope_roots(), max_results=self.config.get("search/result_limit"),
        )
        try:
            if options.ocr_text.strip() and not query_terms(options.ocr_text):
                raise ValueError("图片文字请至少输入一个汉字、英文词或数字")
            return self.image_filters.apply(options)
        except ValueError as exc:
            QMessageBox.warning(self, "图片筛选无效", str(exc))
            return None

    def _start_search(self, refresh: bool = False) -> None:
        if self._closing or getattr(self, "_search_active", False) or getattr(self, "_ai_active", False) or self._manager_kind in {"scan","ai","remove"}:
            return
        self._save_search_scope()
        if not refresh and self.ai.text.text().strip():
            self.ai.search()
            return
        options = self._search_options()
        if options is None:
            return
        if self.scope_mode.currentData() != "single" and not options.folders:
            self.status.setText("请先在索引管理中添加目录，或选择要搜索的目录。")
            return
        self.model.ocr_query = options.ocr_text
        header = self.table.horizontalHeader()
        header.moveSection(header.visualIndex(8), header.count() - 1)
        self.table.fit_columns()
        self.model.clear()
        self._generation += 1
        self._loading_enabled = True
        self._scan_status = ""
        self._image_loader.set_visible([], self._generation)
        self._completion = None
        self._worker = SearchThread(
            options, refresh=refresh, database_path=self._database_path,
            cache_dir=self._cache_dir, parent=self,
        )
        self._worker.progress.connect(self._set_progress)
        self._worker.batch_ready.connect(self._add_results)
        self._worker.paths_removed.connect(self._remove_results)
        self._worker.search_done.connect(self._search_done)
        self._worker.finished.connect(self._worker_finished)
        self._set_searching(True)
        self.status.setText("正在准备搜索…")
        self._worker.start()

    def _set_searching(self, searching: bool) -> None:
        self._search_active = searching
        if searching:
            self._search_cancel_pending = False
        self._update_task_controls()

    def _update_task_controls(self) -> None:
        searching = getattr(self, "_search_active", False)
        ai_busy = getattr(self, "_ai_active", False)
        ocr_busy = getattr(self, "_ocr_active", False)
        manager = self._manager_kind
        ocr_busy = ocr_busy or manager == "ocr"
        foreground_busy = searching or ai_busy or manager in {"scan","ai","remove"}
        self.ai.setEnabled(not searching and manager not in {"scan","ai","remove"})
        self.ai.set_indexing_allowed(not ocr_busy and manager is None)
        # Keep the OCR cancel button accessible during either kind of search.
        self.ocr.setEnabled(ocr_busy or not foreground_busy)
        self.ocr.text.setEnabled(not foreground_busy)
        self.ocr.set_indexing_allowed(manager is None)
        if hasattr(self,"scope_mode"):self.scope_mode.setEnabled(not foreground_busy)
        for widget in (self.folder, self.browse_button, self.filename, self.extensions, self.recursive,
                       self.from_enabled, self.to_enabled, self.search_button, self.refresh_button,
                       self.time_type):
            widget.setEnabled(not foreground_busy)
        self.image_filters.setEnabled(not foreground_busy)
        self.organize_button.setEnabled(not foreground_busy and not ocr_busy and manager is None)
        self.metadata_button.setEnabled(not foreground_busy and not ocr_busy and manager is None)
        self.from_date.setEnabled(not foreground_busy and self.from_enabled.isChecked())
        self.to_date.setEnabled(not foreground_busy and self.to_enabled.isChecked())
        self.cancel_button.setEnabled(searching and not getattr(self, "_search_cancel_pending", False))

    def _cancel_search(self) -> None:
        self._search_cancel_pending = True
        if self._metadata_task is not None:
            self._metadata_task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消元数据补全，已完成记录会保留…")
            return
        if self._worker:
            self._worker.cancel()
            self._loading_enabled = False
            self._generation += 1
            self._image_loader.set_visible([], self._generation)
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消…")

    def _add_results(self, batch: list[SearchResult]) -> None:
        self.model.append(batch)
        self.status.setText(f"{self._scan_status} 已找到 {len(self.model.results)} 个文件。")
        self._schedule_visible()

    def _set_progress(self, message: str) -> None:
        self._scan_status = message
        self.status.setText(message)

    def _remove_results(self, keys: list[str]) -> None:
        self.model.remove_keys(keys)
        self.status.setText(f"{self._scan_status} 已找到 {len(self.model.results)} 个文件。")

    def _search_done(self, count: int, cancelled: bool, error: str) -> None:
        self._completion = (count, cancelled, error)

    def _worker_finished(self) -> None:
        column = {"name":1,"modified":4,"capture":6,"size":3}.get(self.config.get("search/sort"),1)
        self.model.sort(column,Qt.AscendingOrder if column==1 else Qt.DescendingOrder)
        if self._completion is not None:
            count, cancelled, error = self._completion
            if error:
                self.status.setText(f"搜索失败：{error}")
            else:
                suffix = "（已取消）" if cancelled else ""
                self.status.setText(f"找到 {count} 个文件{suffix}。双击结果可打开。")
        self._set_searching(False)
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if self._closing:
            return
        self.ai.check_status()
        self.ocr.check_status()
        self._check_metadata_status()

    def _show_image_details(self) -> None:
        index = self.table.currentIndex()
        if not index.isValid() or index.row()>=len(self.model.results):
            self.image_details.setText("选择图片可查看设备、分辨率和实际像素数量。")
            self.ocr_details_button.setEnabled(False)
            if hasattr(self,"details_sidebar"):self.details_sidebar.request(None)
            return
        item = self.model.results[index.row()]
        if hasattr(self,"details_sidebar"):self.details_sidebar.request(item)
        self.ocr_details_button.setEnabled(item.is_image and bool(item.file_uid))
        if not item.is_image:
            self.image_details.setText("普通文件：没有拍摄设备或图片分辨率。")
            return
        camera = camera_display_name(item.camera_make, item.camera_model) or ("未知 / 无 EXIF" if item.metadata_version else "待读取")
        size = f"{item.width} × {item.height}" if item.width is not None and item.height is not None else "未知 / 待读取"
        pixels = f"{item.width*item.height/1_000_000:.1f} MP" if item.width is not None and item.height is not None else "未知 / 待读取"
        self.image_details.setText(f"拍摄设备：{camera}    分辨率：{size}    像素：{pixels}")

    def _check_metadata_status(self, *_args) -> None:
        if self._closing or self._metadata_status_task is not None:
            return
        options = SearchOptions(Path(self.folder.text()).expanduser(), recursive=self.recursive.isChecked())
        task = MetadataStatusThread(options, self._database_path, self)
        self._metadata_status_task = task
        def ready(devices, pending, total) -> None:
            if task.options.folder == Path(self.folder.text()).expanduser() and task.options.recursive==self.recursive.isChecked():
                self.image_filters.set_devices(devices, pending, total)
        def finished() -> None:
            self._metadata_status_task = None
            task.deleteLater()
            if task.options.folder != Path(self.folder.text()).expanduser() or task.options.recursive!=self.recursive.isChecked():
                self._check_metadata_status()
        task.ready.connect(ready)
        task.finished.connect(finished)
        task.start()

    def _start_metadata(self) -> None:
        ai_busy = self.ai.task is not None and self.ai.task.operation != "status"
        if self._metadata_task is not None or self._worker is not None or ai_busy or getattr(self, "_ocr_active", False) or self._manager_kind:
            return
        # This explicitly completes the chosen folder's indexed images, ignoring filters.
        options = SearchOptions(Path(self.folder.text()).expanduser(), recursive=self.recursive.isChecked())
        task = MetadataThread(options, self._database_path, self)
        self._metadata_task = task
        self._loading_enabled = False
        self._generation += 1
        self._image_loader.set_visible([], self._generation)
        self._set_searching(True)
        def progress(stats) -> None:
            self.status.setText(f"正在补全图片元数据：{stats.processed:,} / {stats.total:,}，读取失败 {stats.failed:,}。")
        def batch(records) -> None:
            self.model.append([result_from_record(r) for r in records if path_key(r.path) in self.model._rows])
        def completed(stats, error) -> None:
            if error:
                self.status.setText(f"元数据补全失败：{error}")
            else:
                suffix = "已取消，下次可继续" if stats.cancelled else "完成"
                self.status.setText(f"元数据补全{suffix}：{stats.processed:,} / {stats.total:,}，失败 {stats.failed:,}；可点击搜索应用筛选。")
        def finished() -> None:
            self._metadata_task = None
            task.deleteLater()
            if self._closing:
                return
            self._set_searching(False)
            self._loading_enabled = True
            self._schedule_visible()
            self._check_metadata_status()
            self.ai.check_status()
        task.progress.connect(progress)
        task.batch_ready.connect(batch)
        task.completed.connect(completed)
        task.finished.connect(finished)
        task.start()

    def _ai_busy(self, busy: bool) -> None:
        self._ai_active = busy
        if self._closing:
            return
        self._update_task_controls()
        if busy:
            self._loading_enabled = False
            self._generation += 1
            self._image_loader.set_visible([], self._generation)
        else:
            self._loading_enabled = True
            self._schedule_visible()

    def _ocr_busy(self, busy: bool) -> None:
        self._ocr_active = busy
        if self._closing:
            return
        self._update_task_controls()

    def _show_ocr_details(self) -> None:
        index = self.table.currentIndex()
        if index.isValid() and index.row() < len(self.model.results):
            self.ocr.show_details(self.model.results[index.row()].file_uid)

    def _ocr_details_ready(self, data) -> None:
        dialog = OCRDetailsDialog(data, self.ocr.text.text(), self)
        dialog.exec()
        dialog.deleteLater()

    def _ai_results(self, results: list[SearchResult]) -> None:
        self.model.ocr_query = self.ocr.text.text()
        self.model.clear()
        self._generation += 1
        self._loading_enabled = True
        self._image_loader.set_visible([], self._generation)
        for offset in range(0, len(results), 100):
            self.model.append(results[offset:offset+100])
        self.model.sort(8,Qt.DescendingOrder)
        # Keep AI scores visible without scrolling past long metadata columns.
        header = self.table.horizontalHeader()
        header.moveSection(header.visualIndex(8), 1)
        self.table.fit_columns()
        self.status.setText(f"AI 搜索：{len(results)} 个结果，按相关度排序；相关度不是概率。")
        self._schedule_visible()

    def _context_menu(self, point) -> None:
        from .file_actions import open_file,reveal_file
        from PySide6.QtWidgets import QApplication
        view = self.grid if self._using_grid else self.table
        index = view.indexAt(point)
        if not index.isValid():
            return
        item = self.model.results[index.row()]
        menu = QMenu(self)
        for label,callback in (("打开",lambda:open_file(item.path)),("使用系统默认程序打开",lambda:open_file(item.path)),
            ("在资源管理器中显示",lambda:reveal_file(item.path)),("复制完整路径",lambda:QApplication.clipboard().setText(str(item.path))),
            ("复制文件名",lambda:QApplication.clipboard().setText(item.path.name)),
            ("查看详细信息",lambda:(self.details_sidebar.show(),self.details_sidebar.request(item)))):
            option = menu.addAction(label)
            option.triggered.connect(callback)
        action = menu.addAction("查找相似图片")
        action.setEnabled(item.is_image and self._worker is None and self.ai.task is None and self._metadata_task is None and self._manager_kind is None)
        ocr = menu.addAction("查看 OCR 文字")
        ocr.setEnabled(item.is_image)
        ocr.triggered.connect(lambda:(self.details_sidebar.show(),self.details_sidebar.request(item)))
        if menu.exec(view.viewport().mapToGlobal(point)) == action:
            self.ai.search(item.path)

    def _open_result(self, index: QModelIndex) -> None:
        path = self.model.results[index.row()].path
        action = self.config.get("general/double_click")
        if action == "reveal":
            from .file_actions import reveal_file
            reveal_file(path)
            return
        if action == "details":
            self.details_sidebar.show()
            self.details_sidebar.request(self.model.results[index.row()])
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "打开失败", f"无法打开：{path}")

    def _organize(self) -> None:
        if getattr(self, "_ocr_active", False) or self._manager_kind:
            return
        self._loading_enabled = False
        self._generation += 1
        self._image_loader.set_visible([], self._generation)
        changed = []
        dialog = OrganizeDialog(
            Path(self.folder.text()), [item.path for item in self.model.results],
            self.recursive.isChecked(), self._database_path, self._cache_dir, self,
        )
        dialog.files_changed.connect(lambda: changed.append(True))
        dialog.exec()
        dialog.deleteLater()
        if changed:
            self._start_search()
        else:
            self._loading_enabled = True
            self._schedule_visible()

    def closeEvent(self, event) -> None:
        self._save_search_scope()
        self._save_product_preferences()
        self._closing = True
        self._loading_enabled = False
        self._image_loader.stop()
        if self.ai.task is not None:self.ai.cancel()
        if self.ocr.task is not None:self.ocr.cancel()
        if self._worker is not None:self._worker.cancel()
        if self._metadata_task is not None:self._metadata_task.cancel()
        if self._wizard is not None and self._wizard.task is not None and self._wizard.task.isRunning():
            self._wizard.task.cancel()
            self._wizard.task.finished.connect(self.close)
            event.ignore()
            return
        for task in (self._catalog_task,self._settings_job):
            if task is not None:task.cancel()
        self.details_sidebar.stop()
        if self.details_sidebar.task is not None and self.details_sidebar.task.isRunning() and not self.details_sidebar.task.full:
            self.details_sidebar.task.wait()
        if self.details_sidebar.task is not None and self.details_sidebar.task.isRunning():
            self.details_sidebar.task.finished.connect(self.close)
            event.ignore()
            return
        if self._index_manager is not None and self._index_manager.task is not None and self._index_manager.task.isRunning():
            self._index_manager._stopping = True
            self._index_manager.cancel()
            self._index_manager.task.finished.connect(self.close)
            event.ignore()
            return
        for task in (self._catalog_task,self._settings_job):
            if task is not None and task.operation in {"catalog","add"}:
                task.wait()
            if task is not None and task.isRunning():
                task.finished.connect(self.close)
                event.ignore()
                return
        # Request cancellation of every concurrent job before waiting for one.
        if self.ai.task is not None:
            self.ai.cancel()
        if self._worker is not None:
            self._worker.cancel()
        if self._metadata_task is not None:
            self._metadata_task.cancel()
        if self.ocr.task is not None and self.ocr.task.isRunning():
            if self.ocr.task.operation in {"status", "check", "detail"}:
                self.ocr.task.cancel()
                self.ocr.task.wait()
            else:
                if not getattr(self, "_closing_ocr", False):
                    self._closing_ocr = True
                    self.ocr.task.finished.connect(self.close)
                self.ocr.cancel()
                event.ignore()
                return
        self.ocr.stop()
        if self._metadata_task is not None and self._metadata_task.isRunning():
            self._metadata_task.cancel()
            self._metadata_task.wait()
        if self._metadata_status_task is not None:
            self._metadata_status_task.wait()
        if self.ai.task is not None and self.ai.task.isRunning():
            if not getattr(self, "_closing_ai", False):
                self._closing_ai = True
                self.ai.task.finished.connect(self.close)
            self.ai.cancel()
            event.ignore()
            return
        self.ai.stop()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        self._image_loader.stop()
        self._image_loader.wait()
        super().closeEvent(event)

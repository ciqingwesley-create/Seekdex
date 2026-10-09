"""Small expandable image filter panel; it never accesses files or SQLite."""
from __future__ import annotations

from dataclasses import replace
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (QWidget, QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout,
    QComboBox, QSpinBox, QDoubleSpinBox, QLabel, QCompleter)
from .search import SearchOptions


class ImageFilterWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.group = QGroupBox("拍摄设备 / 分辨率筛选")
        self.group.setCheckable(True)
        self.group.setChecked(False)
        group_layout = QVBoxLayout(self.group)
        self.body = QWidget()
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.camera = QComboBox()
        self.camera.setEditable(True)
        self.camera.setInsertPolicy(QComboBox.NoInsert)
        self.camera.addItem("")
        self.camera.lineEdit().setPlaceholderText("全部设备；例如 D800、Nikon、iPhone")
        completer = self.camera.completer()
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        self.orientation = QComboBox()
        for label, value in (("全部方向", "all"), ("横向", "landscape"), ("竖向", "portrait"), ("正方形", "square")):
            self.orientation.addItem(label, value)
        self.resolution = QComboBox()
        for label, value in (("自定义 / 不限", ""), ("≥ Full HD", "fhd"), ("≥ 4K", "4k"), ("≥ 8K", "8k")):
            self.resolution.addItem(label, value)
        for label, widget in (("拍摄设备", self.camera), ("分辨率", self.resolution), ("方向", self.orientation)):
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1 if widget is self.camera else 0)
        layout.addLayout(row)
        grid = QGridLayout()
        self.bounds = {}
        for column, (label, kind) in enumerate((("宽度 (px)", "width"), ("高度 (px)", "height"), ("像素 (MP)", "megapixels"))):
            grid.addWidget(QLabel(label), 0, column*3)
            for j, (caption, prefix) in enumerate((("最小", "min"), ("最大", "max"))):
                control = QDoubleSpinBox() if kind=="megapixels" else QSpinBox()
                control.setRange(0, 1_000_000)
                control.setSpecialValueText("不限")
                if kind=="megapixels":
                    control.setDecimals(3)
                self.bounds[f'{prefix}_{kind}'] = control
                grid.addWidget(QLabel(caption), 1+j, column*3)
                grid.addWidget(control, 1+j, column*3+1, 1, 2)
        layout.addLayout(grid)
        group_layout.addWidget(self.body)
        self.body.hide()
        self.group.toggled.connect(self.body.setVisible)
        outer.addWidget(self.group)
        self.info = QLabel("设备和分辨率使用缓存信息；旧图片可点击“补全图片元数据”。")
        self.info.setWordWrap(True)
        outer.addWidget(self.info)

    def apply(self, options: SearchOptions) -> SearchOptions:
        if not self.group.isChecked():
            return options
        return replace(options, camera=self.camera.currentText(), resolution=self.resolution.currentData(),
            orientation=self.orientation.currentData(), **{name:control.value() or None for name,control in self.bounds.items()})

    def set_devices(self, devices: list[str], pending: int, total: int) -> None:
        value = self.camera.currentText()
        blocker = QSignalBlocker(self.camera)
        self.camera.clear()
        self.camera.addItems([""]+devices)
        self.camera.setCurrentText(value)
        del blocker
        self.info.setText(f"当前目录索引中 {total:,} 张图片，{pending:,} 张待补全设备 / 方向信息。"
                          "筛选使用已补全信息；未补全项需先点击“补全图片元数据”。")

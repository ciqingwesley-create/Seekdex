"""Responsive result columns without scanning rows or reading files."""

from PySide6.QtCore import QEvent, QSize, Signal, Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QTableView


class ResultTable(QTableView):
    preview_size_changed = Signal(QSize)

    def __init__(self) -> None:
        super().__init__()
        self._requested_preview = QSize(240, 180)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideMiddle)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setMinimumSectionSize(40)
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.viewport() and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self.fit_columns)
        return super().eventFilter(obj, event)

    def set_preview_size(self, size: QSize) -> None:
        self._requested_preview = size
        self.verticalHeader().setDefaultSectionSize(size.height() + 12)
        self.fit_columns()

    def fit_columns(self) -> None:
        header = self.horizontalHeader()
        if header.count() != 12:
            return
        scale = max(1.0, self.fontMetrics().height() / 16)
        minimum = [96, 75, 110, 50, 80, 65, 85, 60, 50, 70, 50, 40]
        preferred = [0, 170, 270, 75, 135, 110, 155, 115, 65, 140, 75, 60]
        minimum = [round(value * scale) for value in minimum]
        preferred = [round(value * scale) for value in preferred]
        preferred[0] = max(minimum[0], self._requested_preview.width() + 16)
        columns = [col for col in range(12) if not self.isColumnHidden(col)]
        available = self.viewport().width()
        lower = sum(minimum[col] for col in columns)
        upper = sum(preferred[col] for col in columns)
        if available < lower:
            widths = minimum[:]
        elif available < upper:
            fraction = (available - lower) / (upper - lower)
            widths = [int(low + fraction * (high - low))
                      for low, high in zip(minimum, preferred)]
        else:
            widths = preferred[:]
            widths[1] += int((available - upper) * 0.35)
        if available >= lower:
            # Give remaining space (including integer rounding) to the path.
            widths[2] += available - sum(widths[col] for col in columns)
        for col in columns:
            if self.columnWidth(col) != widths[col]:
                self.setColumnWidth(col, widths[col])
        width = min(self._requested_preview.width(), max(1, widths[0] - 16))
        size = QSize(width, round(width * self._requested_preview.height()
                                  / self._requested_preview.width()))
        if size != self.iconSize():
            self.setIconSize(size)
            self.preview_size_changed.emit(size)

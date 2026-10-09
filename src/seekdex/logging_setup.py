"""Rotating local logs and friendly uncaught exception reporting."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import traceback
import re
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox
from .app_info import APP_NAME, VERSION
from .paths import get_storage_root


class PrivacyFormatter(logging.Formatter):
    def format(self,record):
        message = super().format(record)
        from .paths import get_config_path
        from PySide6.QtCore import QSettings
        # Logging runs before migration: reading must not create a new profile
        # that would prevent the legacy settings file from being imported.
        settings = QSettings(str(get_config_path()),QSettings.IniFormat)
        if not settings.value("general/log_paths",True,type=bool):
            message = re.sub(r'"[^"\n]*(?:[A-Za-z]:[\\/]|/)[^"\n]*"','"[路径已隐藏]"',message)
            message = re.sub(r'[A-Za-z]:[\\/][^\n;,]+','[路径已隐藏]',message)
        return message


def setup_logging(root: Path | None = None) -> Path:
    directory = (root or get_storage_root()) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "app.log"
    logger = logging.getLogger("seekdex")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == target for h in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=2*1024*1024, backupCount=5, encoding="utf8")
        handler.setFormatter(PrivacyFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.info("Application started: %s %s, frozen=%s", APP_NAME, VERSION, getattr(sys, "frozen", False))
    return target


class ExceptionReporter(QObject):
    raised = Signal(str)

    def __init__(self, log_path: Path, parent=None):
        super().__init__(parent)
        self.log_path = log_path
        self._showing = False
        self.raised.connect(self.show_error)

    def install(self):
        sys.excepthook = self.handle
        threading.excepthook = lambda args: self.handle(args.exc_type, args.exc_value, args.exc_traceback)

    def handle(self, kind, value, tb):
        details = "".join(traceback.format_exception(kind, value, tb))
        logging.getLogger("seekdex").error("Unhandled exception", exc_info=(kind, value, tb))
        self.raised.emit(details)

    def show_error(self, details):
        if self._showing:
            return
        self._showing = True
        dialog = QMessageBox(QMessageBox.Critical, f"{APP_NAME} 遇到问题",
            f"操作未能完成。可以继续使用其他功能。\n日志位置：{self.log_path}", parent=QApplication.activeWindow())
        dialog.setDetailedText(details)
        copy = dialog.addButton("复制错误信息", QMessageBox.ActionRole)
        exit_button = dialog.addButton("退出", QMessageBox.DestructiveRole)
        dialog.addButton("继续运行", QMessageBox.AcceptRole)
        dialog.exec()
        if dialog.clickedButton() == copy:
            QApplication.clipboard().setText(details)
        elif dialog.clickedButton() == exit_button:
            QApplication.closeAllWindows()
        self._showing = False

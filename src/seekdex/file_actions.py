"""Desktop integration without shell interpolation."""
from pathlib import Path
import sys
from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices


def open_file(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_file(path: Path) -> bool:
    if sys.platform == "win32":
        ok, _pid = QProcess.startDetached("explorer.exe", ["/select,",str(path)])
        return ok
    return open_file(path.parent)

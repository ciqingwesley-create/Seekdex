"""Application entry point."""

from __future__ import annotations

import sys
import argparse
from pathlib import Path
from threading import Event

from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QMessageBox
from PySide6.QtCore import QThread, Signal, QTimer, QLockFile
from PySide6.QtGui import QIcon

from .ui import MainWindow
from .app_info import APP_NAME, DISPLAY_NAME, VERSION
from .paths import get_storage_root, get_database_path, get_app_data_dir, get_model_cache_dir
from .resources import resource_path
from .settings import AppSettings


class StartupTask(QThread):
    progress = Signal(str)
    completed = Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent)
        self.cancelled = Event()

    def report(self,message):
        if self.cancelled.is_set():raise InterruptedError("启动已取消")
        self.progress.emit(message)

    def run(self):
        error = ""
        try:
            from .brand_migration import migrate_brand
            migrate_brand(progress=self.report)
            from .storage import migrate_legacy
            from .index.database import FileIndex
            migrate_legacy(progress=self.report)
            from .bundled_models import install_preinstalled
            try:
                install_preinstalled(cancelled=self.cancelled.is_set, progress=self.report)
            except InterruptedError:
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Preinstalled model import failed")
                self.report("预装模型导入未完成，普通搜索仍可使用；详情见应用日志。")
            self.report("正在打开本地数据库…")
            with FileIndex(get_database_path()) as database:
                database.connection.execute("""UPDATE indexed_directories SET
                    scan_status=CASE WHEN scan_status='running' THEN 'partial' ELSE scan_status END,
                    ai_status=CASE WHEN ai_status='running' THEN 'partial' ELSE ai_status END,
                    ocr_status=CASE WHEN ocr_status='running' THEN 'partial' ELSE ocr_status END,
                    last_error=CASE WHEN scan_status='running' OR ai_status='running' OR ocr_status='running'
                        THEN '上次任务未结束，已完成的数据保留，可以继续。' ELSE last_error END""")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Database initialization / migration failed: %s",type(exc).__name__)
            error = f"{type(exc).__name__}: {exc}"
        self.completed.emit(error)


class StartupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.task = StartupTask(self)
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowIcon(QIcon(str(resource_path("app.ico"))))
        self.resize(500,130)
        self.label = QLabel("正在准备用户数据…")
        self.label.setWordWrap(True)
        QVBoxLayout(self).addWidget(self.label)
        self.task.progress.connect(self.label.setText)

    def closeEvent(self,event):
        if self.task.isRunning():
            self.task.cancelled.set()
            self.label.setText("正在结束启动任务…")
            self.task.finished.connect(self.close)
            event.ignore()
        else:super().closeEvent(event)


def main() -> int:
    from .runtime_output import prepare_windowed_output
    prepare_windowed_output()
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--verify-runtime",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--verify-full",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--verify-migration",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--verify-downloads",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--model-root",type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--verify-identity",type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.verify_identity:
        import json
        from .release_identity import verify_frozen_identity
        try:
            result = verify_frozen_identity()
        except Exception as exc:
            result = dict(error=f"{type(exc).__name__}: {exc}")
        args.verify_identity.parent.mkdir(parents=True, exist_ok=True)
        args.verify_identity.write_text(json.dumps(result, indent=2), encoding="utf8")
        return 1 if result.get("error") else 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(QIcon(str(resource_path("app.ico"))))
    app.setQuitOnLastWindowClosed(False)
    from .logging_setup import setup_logging, ExceptionReporter
    log_path = setup_logging()
    reporter = ExceptionReporter(log_path,app)
    reporter.install()
    root = get_storage_root()
    root.mkdir(parents=True,exist_ok=True)
    lock = QLockFile(str(root / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None,APP_NAME,"此用户数据目录已有应用正在运行。请使用已打开的窗口。")
        return 0
    if args.model_root:
        config = AppSettings()
        config.set("ai/model_dir",str(args.model_root.resolve()))
        config.set("ocr/model_dir",str(args.model_root.resolve()/"ocr"))
        config.save()
    startup = StartupDialog()
    window = None
    verification = None
    failure = ""
    def ready(error):
        nonlocal window,verification,failure
        if startup.task.cancelled.is_set():
            app.quit()
            return
        if error:
            failure = error
            QMessageBox.warning(startup,"Seekdex 数据初始化失败",f"原数据未删除。\n{error}\n请查看日志并解决迁移冲突后重试。")
            app.exit(1)
            return
        old_db = get_app_data_dir()/"index.db"
        if error and old_db.exists():
            old_models = get_app_data_dir()/"models"
            if old_models.is_dir():
                config = AppSettings()
                config.set("ai/model_dir",str(old_models))
                config.set("ocr/model_dir",str(old_models/"ocr"))
                config.save()
        window = MainWindow(database_path=old_db if error and old_db.exists() else None)
        window.show()
        startup.hide()
        app.setQuitOnLastWindowClosed(True)
        if window.config.first_launch:window.show_first_run()
        if args.verify_runtime or args.verify_full or args.verify_migration:
            from .release_verification import ReleaseVerification, MigrationVerification
            verifier = MigrationVerification if args.verify_migration else ReleaseVerification
            verification = verifier(window,args.verify_migration or args.verify_full or args.verify_runtime,
                                               full=bool(args.verify_full),download=args.verify_downloads)
            verification.start()
    startup.task.completed.connect(lambda error:setattr(startup,"result",error))
    startup.task.finished.connect(lambda:ready(getattr(startup,"result","启动任务未返回")))
    startup.show()
    startup.task.start()
    try:
        result = app.exec()
        return 1 if verification and verification.error else result
    finally:
        lock.unlock()

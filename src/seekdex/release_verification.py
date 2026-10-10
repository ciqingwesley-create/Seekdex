"""Opt-in native GUI verification for a frozen EXE, always in an isolated profile."""
from pathlib import Path
from datetime import datetime
import json
import os
import socket
import sys
from time import monotonic
from PySide6.QtCore import QTimer, QThread, Signal, QDate, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWizard
from .app_info import VERSION, LICENSE
from .paths import get_config_path, get_database_path
from .product_worker import ProductTask


class RuntimeProbe(QThread):
    ready = Signal(object)
    def run(self):
        result = {}
        try:
            import torch
            from transformers import ChineseCLIPModel,ChineseCLIPProcessor
            import onnxruntime
            import rapidocr
            import openvino as ov
            import rawpy
            from PySide6.QtGui import QImageReader
            result = dict(torch=torch.__version__,openvino=ov.__version__,
                devices=list(ov.Core().available_devices),onnxruntime=onnxruntime.__version__,
                rapidocr=True,rawpy=rawpy.__version__,formats=[bytes(v).decode() for v in QImageReader.supportedImageFormats()])
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        self.ready.emit(result)


class ReleaseVerification:
    def __init__(self,window,directory: Path,*,full=False,download=False):
        self.window,self.directory,self.full,self.download = window,directory.resolve(),full,download
        self.error = ""
        self.report = dict(version=VERSION,frozen=bool(getattr(sys,"frozen",False)),
            platform=QApplication.platformName(),executable=sys.executable,full=full,download=download,checks=[])
        self.report["output_streams_available"] = sys.stdout is not None and sys.stderr is not None
        self.report["xet_disabled"] = os.environ.get("HF_HUB_DISABLE_XET") == "1"
        self.started = monotonic()
        self._pending = None
        self._stepping = False
        self._jobs = []
        self._result = None
        self.ticks = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.heartbeat = QTimer()
        self.heartbeat.timeout.connect(lambda:self.ticks.append(monotonic()))
        self._original_socket = socket.socket.connect

    def start(self):
        profile = os.environ.get("SEEKDEX_HOME") or os.environ.get("LOCAL_IMAGE_SEARCH_HOME")
        if not profile or not self.directory.is_relative_to(Path(profile).resolve().parent):
            self.finish("Verification requires an explicitly isolated profile")
            return
        self.directory.mkdir(parents=True,exist_ok=True)
        QApplication.instance().setQuitOnLastWindowClosed(False)
        if not self.download:
            def no_network(*args,**kwargs):raise AssertionError("Offline verification attempted network")
            socket.socket.connect = no_network
        self.steps = self.verify()
        self.heartbeat.start(20)
        self.timer.start(40)

    def record(self,name):
        self.report["checks"].append(name)
        (self.directory/"progress.json").write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding="utf8")

    def tick(self):
        if self._stepping:return
        self._stepping = True
        try:
            if monotonic()-self.started>(1200 if self.full else 180):raise TimeoutError(str(self.report["checks"]))
            if self._pending is not None and not self._pending():return
            self._pending = next(self.steps)
        except StopIteration:self.finish()
        except Exception as exc:self.finish(f"{type(exc).__name__}: {exc}")
        finally:self._stepping = False

    def idle(self):
        window = self.window
        return window._worker is None and window.ai.task is None and window.ocr.task is None and window._catalog_task is None

    def job(self,operation,parameters=None,roots=None):
        self._result = None
        task = ProductTask(operation,get_database_path(),roots=roots,parameters=parameters,parent=self.window)
        task.completed.connect(lambda value,error:setattr(self,"_result",(value,error)))
        self._jobs.append(task)
        task.start()
        return task

    def query(self,filename="",ocr="",ai=""):
        window = self.window
        window.filename.setText(filename)
        window.ocr.text.setText(ocr)
        window.ai.text.setText(ai)
        window.search_button.click()

    def create_images(self):
        from PIL import Image,ImageDraw,ImageFont
        self.photos = self.directory/"photos"
        self.other = self.directory/"other"
        sub = self.photos/"sub"
        sub.mkdir(parents=True,exist_ok=True)
        self.other.mkdir(exist_ok=True)
        font_path = Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/"msyh.ttc"
        font = ImageFont.truetype(str(font_path),44) if font_path.exists() else ImageFont.load_default()
        for path,text in ((self.photos/"screen.png","Windows Update failed\n错误代码 0x80070005"),
                          (sub/"chat.png","ChatGPT\n本地图片文字搜索")):
            image = Image.new("RGB",(1080,1920),"white")
            ImageDraw.Draw(image).multiline_text((70,130),text,font=font,fill="black",spacing=30)
            image.save(path)
        exif = Image.Exif()
        exif[271],exif[272],exif[36867] = "NIKON CORPORATION","NIKON D800","2026:05:06 14:23:10"
        Image.new("RGB",(800,600),(60,140,190)).save(self.photos/"camera.jpg",exif=exif,quality=95)
        (self.photos/"ordinary.txt").write_text("ordinary local file",encoding="utf8")
        (self.other/"archive.txt").write_text("second scope",encoding="utf8")

    def verify(self):
        from .resources import resource_path
        from .index.database import FileIndex
        from .organize.worker import OrganizeTask
        from .settings_dialog import SettingsDialog
        self.create_images()
        yield lambda:self.window._wizard is not None and self.window._wizard.isVisible() and self.idle()
        assert self.window.isVisible()
        assert resource_path("app.ico").is_file()
        assert LICENSE == "GPL-3.0-only"
        license_text = resource_path("LICENSE").read_text(encoding="utf8")
        assert "GNU GENERAL PUBLIC LICENSE" in license_text and "END OF TERMS AND CONDITIONS" in license_text
        assert len(license_text) > 30000
        self.report["license_expression"] = LICENSE
        self.record("complete_gplv3_license_and_metadata")
        if getattr(sys, "frozen", False):
            from .release_identity import verify_frozen_identity
            self.report["release_identity"] = verify_frozen_identity()
            self.record("actual_EXE_version_license_homepage_source_commit")
        from .about import AboutDialog
        from .app_info import HOMEPAGE
        from PySide6.QtWidgets import QLabel
        about = AboutDialog(self.window)
        about.show()
        yield lambda: about.isVisible()
        labels = "\n".join(label.text() for label in about.findChildren(QLabel))
        assert VERSION in about.version_label.text() and LICENSE in labels and HOMEPAGE in labels
        about.grab().save(str(self.directory / "about.png"))
        about.close()
        self.record("packaged_About_version_GPL_homepage")
        wizard = self.window._wizard
        paths = iter([self.photos,self.photos/"sub",self.other])
        QFileDialog.getExistingDirectory = lambda *args,**kwargs:str(next(paths))
        self.report["wizard_steps"] = []
        for page in range(7):
            assert wizard.currentId()==page,(page,wizard.currentId())
            self.report["wizard_steps"].append(wizard.currentPage().title())
            if page==1:
                for _ in range(3):wizard.add_directory()
            if page==2:
                wizard.launch("scan",wizard.base_status)
                yield lambda:wizard.task is None
            if page==3 and self.full:
                wizard.install_ai()
                yield lambda:wizard.task is None
                assert "未完成" not in wizard.ai_status.text(),wizard.ai_status.text()
            if page==4 and self.full:
                wizard.install_ocr()
                yield lambda:wizard.task is None
                assert "未完成" not in wizard.ocr_status.text(),wizard.ocr_status.text()
            if page==5 and self.full:
                for kind in ("ai","ocr"):
                    wizard.launch(kind,wizard.index_status)
                    yield lambda:wizard.task is None
                    assert "未完成" not in wizard.index_status.text(),wizard.index_status.text()
            if page<6:wizard.next()
            yield lambda:wizard.task is None
        wizard.accept()
        yield self.idle
        assert not self.window.config.first_launch
        self.record("first_launch_wizard_7_steps_and_optional_skips")
        self.window.recursive.setChecked(True)
        self.window.scope_mode.setCurrentIndex(1)
        self.query()
        yield self.idle
        assert len(self.window.model.results)==5,self.window.status.text()
        self.record("multi_directory_search_overlap_dedup")
        yield lambda:all(r.thumbnail and r.width for r in self.window.model.results if r.is_image)
        self.record("visible_thumbnails_capture_time_and_camera_metadata")
        camera = next(r for r in self.window.model.results if r.path.name=="camera.jpg")
        assert camera.capture_time_text.startswith("2026-05-06"),camera
        assert camera.camera_model=="NIKON D800"
        self.window.image_filters.group.setChecked(True)
        self.window.image_filters.camera.setCurrentText("D800")
        self.query()
        yield self.idle
        assert [r.path.name for r in self.window.model.results]==["camera.jpg"]
        self.record("camera_filter")
        self.window.image_filters.bounds["min_width"].setValue(800)
        self.window.image_filters.bounds["min_height"].setValue(600)
        self.query()
        yield self.idle
        assert len(self.window.model.results)==1
        self.record("resolution_filter")
        self.window.image_filters.group.setChecked(False)
        self.window.time_type.setCurrentIndex(self.window.time_type.findData("capture"))
        self.window.from_date.setDate(QDate(2026,5,6))
        self.window.to_date.setDate(QDate(2026,5,6))
        self.window.from_enabled.setChecked(True)
        self.window.to_enabled.setChecked(True)
        self.query()
        yield self.idle
        assert [r.path.name for r in self.window.model.results]==["camera.jpg"]
        self.record("capture_date_filter")
        self.window.from_enabled.setChecked(False)
        self.window.to_enabled.setChecked(False)
        self.query(filename="camera")
        yield self.idle
        assert [r.path.name for r in self.window.model.results] == ["camera.jpg"]
        self.record("filename_filter")
        self.window.extensions.setText("png")
        self.query()
        yield self.idle
        assert {r.path.name for r in self.window.model.results} == {"screen.png", "chat.png"}
        self.record("extension_filter")
        self.window.extensions.clear()
        self.query()
        yield self.idle
        self.window.model.sort(3,Qt.DescendingOrder)
        assert [r.size for r in self.window.model.results]==sorted((r.size for r in self.window.model.results),reverse=True)
        self.window._set_result_view("grid")
        self.record("list_sort_and_grid_view")
        row = next(i for i,r in enumerate(self.window.model.results) if r.path.name=="camera.jpg")
        self.window.table.setCurrentIndex(self.window.model.index(row,0))
        self.window.details_sidebar.show()
        self.window.details_sidebar.request(self.window.model.results[row])
        yield lambda:self.window.details_sidebar.task is None
        assert "D800" in self.window.details_sidebar.text.toPlainText()
        self.window.grab().save(str(self.directory/"main-window.png"))
        self.record("details_sidebar")
        from .file_actions import open_file
        assert open_file(self.photos/"ordinary.txt")
        self.record("system_default_file_open_requested")
        self.window._manage_indices()
        yield lambda:self.window._index_manager.task is None
        assert len(self.window._index_manager.items)==3
        self.window._index_manager.grab().save(str(self.directory/"index-manager.png"))
        self.window._index_manager.hide()
        self.record("index_management_counts")
        probe = RuntimeProbe(self.window)
        self._jobs.append(probe)
        self._result = None
        probe.ready.connect(lambda value:setattr(self,"_result",value))
        probe.start()
        yield lambda:not probe.isRunning() and self._result is not None
        assert not self._result.get("error"),self._result
        self.report["runtimes"] = self._result
        self.record("Qt_plugins_torch_openvino_ocr_rawpy_DLLs")
        if self.full:
            self.query(ocr="Windows Update")
            yield self.idle
            assert [r.path.name for r in self.window.model.results]==["screen.png"],self.window.status.text()
            self.record("offline_ocr_query")
            self.query(ai="电脑屏幕")
            yield self.idle
            assert len(self.window.model.results)==3,self.window.ai.status.text()
            self.report["ai_backend"] = self.window.ai.runtime.backend.device
            self.report["ai_scores"] = [r.similarity for r in self.window.model.results]
            self.record("offline_AI_query")
            self.window.ai.search(self.photos/"screen.png")
            yield self.idle
            assert len(self.window.model.results)==2
            self.record("similar_image_search")
            self.query(ocr="Windows Update",ai="电脑屏幕")
            yield self.idle
            assert [r.path.name for r in self.window.model.results]==["screen.png"],self.window.ai.status.text()
            self.record("AI_plus_OCR")
            with FileIndex(get_database_path()) as database:
                assert database.connection.execute("SELECT count(*) FROM image_embeddings").fetchone()[0]==3
                assert database.connection.execute("SELECT count(*) FROM ocr_results").fetchone()[0]==3
            self.record("existing_embedding_and_OCR_identity")
        else:
            self.record("ordinary_search_without_models_offline")
        self.query()
        yield self.idle
        # Exercise the actual organization workers, on our fixture only.
        parameters = dict(scope="results",sources=[self.photos/"camera.jpg"],target_root=self.directory/"organized",
            source_root=self.photos,recursive=True,action="move",template="{year}y/{month}m/{day}d/{filename}",conflict_policy="skip")
        self._result = None
        preview = OrganizeTask("preview",get_database_path(),self.window._image_loader.cache_dir,parameters,self.window)
        preview.completed.connect(lambda value,error,cancelled:setattr(self,"_result",(value,error)))
        self._jobs.append(preview)
        preview.start()
        yield lambda:not preview.isRunning() and self._result is not None
        plan,error = self._result
        assert not error,error
        assert (self.photos/"camera.jpg").exists()
        self.record("organization_preview_does_not_modify_files")
        self._result = None
        execute = OrganizeTask("execute",get_database_path(),self.window._image_loader.cache_dir,dict(plan=plan),self.window)
        execute.completed.connect(lambda value,error,cancelled:setattr(self,"_result",(value,error)))
        self._jobs.append(execute)
        execute.start()
        yield lambda:not execute.isRunning() and self._result is not None
        assert not self._result[1],self._result
        assert not (self.photos/"camera.jpg").exists()
        with FileIndex(get_database_path()) as database:
            operation = database.connection.execute("SELECT id FROM file_operations WHERE operation='move' AND success=1 ORDER BY id DESC LIMIT 1").fetchone()[0]
        self._result = None
        undo = OrganizeTask("undo",get_database_path(),self.window._image_loader.cache_dir,dict(operation_id=operation),self.window)
        undo.completed.connect(lambda value,error,cancelled:setattr(self,"_result",(value,error)))
        self._jobs.append(undo)
        undo.start()
        yield lambda:not undo.isRunning() and self._result is not None
        assert not self._result[1],self._result
        assert (self.photos/"camera.jpg").exists()
        self.record("move_and_undo")
        yield self.idle
        dialog = SettingsDialog(self.window.config,get_database_path(),self.window)
        dialog.show()
        yield lambda:dialog.task is None
        dialog.controls["general/remember_filters"].setChecked(True)
        dialog.controls["search/view"].setCurrentIndex(dialog.controls["search/view"].findData("grid"))
        dialog.save()
        yield lambda:not dialog.isVisible()
        self.window.filename.setText("camera")
        self.window.resize(1300,800)
        self.window._save_product_preferences()
        self.record("settings_saved")
        self.window.close()
        yield lambda:not self.window.isVisible()
        from .ui import MainWindow
        self.window = MainWindow()
        self.window.show()
        yield self.idle
        assert self.window.folder.text()==str(self.photos)
        assert self.window.filename.text()=="camera"
        assert self.window._using_grid
        assert not self.window.config.first_launch
        self.record("window_restart_profile_restored_no_repeat_wizard")
        check = self.job("integrity")
        yield lambda:not check.isRunning() and self._result is not None
        assert self._result==(["ok"],""),self._result
        self.record("database_integrity")
        self.report["elapsed_s"] = monotonic()-self.started

    def finish(self,error=""):
        self.timer.stop()
        self.heartbeat.stop()
        self.error = error
        self.report["error"] = error
        self.report["max_heartbeat_gap_s"] = max((b-a for a,b in zip(self.ticks,self.ticks[1:])),default=0)
        self.report["heartbeat_ticks"] = len(self.ticks)
        self.directory.mkdir(parents=True,exist_ok=True)
        (self.directory/"report.json").write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding="utf8")
        socket.socket.connect = self._original_socket
        self.window.close()
        cleanup = QTimer()
        self.cleanup = cleanup
        cleanup.timeout.connect(lambda:QApplication.instance().quit() if not self.window.isVisible() else None)
        cleanup.start(50)


class MigrationProbe(QThread):
    ready = Signal(object)

    def __init__(self, old: Path, new: Path, parent=None):
        super().__init__(parent)
        self.old, self.new = old, new

    def run(self):
        import hashlib
        import sqlite3
        from contextlib import closing
        from .brand_migration import digest
        try:
            identities = []
            for profile in (self.old, self.new):
                result = {}
                with closing(sqlite3.connect((profile / "data/database.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
                    for table, order in (("files", "file_uid"), ("image_embeddings", "file_uid,model_id"), ("ocr_results", "file_uid")):
                        rows = db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
                        result[table] = dict(count=len(rows), sha256=hashlib.sha256(repr(rows).encode()).hexdigest())
                identities.append(result)
            assert identities[0] == identities[1], "Database identities changed during migration"
            models = list((self.old / "models").rglob("*"))
            verified = 0
            for old in models:
                if not old.is_file() or old.suffix in {".lock", ".tmp", ".part"}:
                    continue
                new = self.new / "models" / old.relative_to(self.old / "models")
                assert new.is_file() and digest(old) == digest(new), "Model/cache bytes changed"
                verified += 1
            self.ready.emit(dict(database=identities[0], model_files_verified=verified, error=""))
        except Exception as exc:
            self.ready.emit(dict(error=f"{type(exc).__name__}: {exc}"))


class MigrationVerification(ReleaseVerification):
    """Frozen-EXE validation of an explicitly supplied synthetic legacy profile."""
    def verify(self):
        from .paths import get_storage_root
        from .app_info import DISPLAY_NAME
        from .settings import AppSettings
        old = Path(os.environ["SEEKDEX_LEGACY_HOME"]).resolve()
        root = get_storage_root()
        assert self.window.isVisible() and self.window.windowTitle() == DISPLAY_NAME
        assert (root / "config/brand-migration-v1.done").is_file()
        assert not self.window.config.first_launch
        original = AppSettings(old / "config/settings.ini")
        assert self.window.folder.text() == original.get("search/folder")
        assert self.window.config.get("ai/batch") == original.get("ai/batch")
        self.record("brand_title_settings_restored_no_repeat_wizard")
        self._result = None
        probe = MigrationProbe(old, root, self.window)
        self._jobs.append(probe)
        probe.ready.connect(lambda value: setattr(self, "_result", value))
        probe.start()
        yield lambda: not probe.isRunning() and self._result is not None
        assert not self._result.get("error"), self._result
        self.report["migration"] = self._result
        self.record("existing_UID_embedding_OCR_and_model_bytes_preserved")
        self.window.image_filters.group.setChecked(False)
        self.window.from_enabled.setChecked(False)
        self.window.to_enabled.setChecked(False)
        self.window.extensions.clear()
        self.window.scope_mode.setCurrentIndex(1)
        self.query()
        yield self.idle
        assert len(self.window.model.results) == 5
        yield lambda: all(r.thumbnail and r.width for r in self.window.model.results if r.is_image)
        camera = next(r for r in self.window.model.results if r.path.name == "camera.jpg")
        assert camera.camera_model == "NIKON D800" and camera.capture_time_text.startswith("2026-05-06")
        self.record("migrated_search_thumbnails_EXIF")
        self.window.image_filters.group.setChecked(True)
        self.window.image_filters.camera.setCurrentText("D800")
        self.window.image_filters.bounds["min_width"].setValue(800)
        self.query()
        yield self.idle
        assert [r.path.name for r in self.window.model.results] == ["camera.jpg"]
        self.record("migrated_camera_resolution_filter")
        self.window.image_filters.group.setChecked(False)
        self.query(ai="电脑屏幕")
        yield self.idle
        assert len(self.window.model.results) == 3, self.window.ai.status.text()
        self.report["ai_backend"] = self.window.ai.runtime.backend.device
        self.record("offline_AI_query_existing_embeddings")
        self.query(ocr="Windows Update")
        yield self.idle
        assert [r.path.name for r in self.window.model.results] == ["screen.png"]
        self.record("offline_OCR_query_existing_text")
        self.window._save_product_preferences()
        self.window.close()
        yield lambda: not self.window.isVisible()
        from .ui import MainWindow
        self.window = MainWindow()
        self.window.show()
        yield self.idle
        assert not self.window.config.first_launch
        self.record("migrated_restart_and_settings")
        task = self.job("integrity")
        yield lambda: not task.isRunning() and self._result is not None
        assert self._result == (["ok"], "")
        self.record("migrated_database_integrity")
        self.report["elapsed_s"] = monotonic() - self.started

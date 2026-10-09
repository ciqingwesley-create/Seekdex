"""Selection details query saved data independently of ongoing OCR indexing."""
from pathlib import Path
from threading import Event
from html import escape
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QTextBrowser, QPushButton
from .index.database import FileIndex
from .ai.model_cache import MODEL_ID as AI_MODEL
from .ocr.model_cache import MODEL_ID as OCR_MODEL
from .capture_time import source_label
from .file_actions import open_file, reveal_file


class DetailsTask(QThread):
    ready = Signal(object)
    def __init__(self, database_path: Path, uid: str, full: bool, parent=None):
        super().__init__(parent)
        self.database_path,self.uid,self.full = database_path,uid,full
        self.cancelled = Event()

    def cancel(self): self.cancelled.set()

    def run(self):
        try:
            with FileIndex(self.database_path) as database:
                row = database.connection.execute("SELECT * FROM files WHERE file_uid=?",(self.uid,)).fetchone()
                if row is None or self.cancelled.is_set():return
                result = dict(row)
                result["ai"] = bool(database.connection.execute("SELECT 1 FROM image_embeddings WHERE file_uid=? AND model_id=? AND size=? AND mtime_ns=?",
                    (self.uid,AI_MODEL,row["size"],row["mtime_ns"])).fetchone())
                ocr = database.connection.execute("SELECT ocr_text,confidence FROM ocr_results WHERE file_uid=? AND ocr_model_id=? AND size=? AND mtime_ns=?",
                    (self.uid,OCR_MODEL,row["size"],row["mtime_ns"])).fetchone()
                result["ocr"] = dict(ocr) if ocr else None
                if self.full and row["is_image"]:
                    from .exif_metadata import read_exif_tags
                    result["exif"] = {k:str(v)[:1000] for k,v in read_exif_tags(Path(row["path"])).items() if "MakerNote" not in k}
            if not self.cancelled.is_set():self.ready.emit(result)
        except Exception as exc:
            if not self.cancelled.is_set():self.ready.emit(dict(error=f"读取详情失败：{type(exc).__name__}"))


class DetailsSidebar(QDockWidget):
    similar = Signal(object)
    def __init__(self,database_path: Path,parent=None):
        super().__init__("文件详情",parent)
        self.setObjectName("file-details")
        self.database_path = database_path
        self.task = None
        self.item = None
        self._pending = None
        self._last = None
        self._stopping = False
        body = QWidget()
        layout = QVBoxLayout(body)
        self.text = QTextBrowser()
        self.text.setMinimumWidth(270)
        layout.addWidget(self.text,1)
        for label, action in (("打开",lambda:open_file(self.item.path) if self.item else None),
            ("在资源管理器中显示",lambda:reveal_file(self.item.path) if self.item else None),
            ("查找相似图片",lambda:self.similar.emit(self.item.path) if self.item and self.item.is_image else None),
            ("查看全部元数据",lambda:self.request(self.item,True) if self.item else None)):
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        self.setWidget(body)

    def request(self,item,full=False):
        self.item = item
        if item is None:
            self._last = None
            self.text.setText("请选择一个结果。")
            if self.task is not None:self.task.cancel()
            self._pending = None
            return
        token = (item.file_uid,item.mtime_ns,item.metadata_version,full,item.similarity)
        if token == self._last or self._stopping:return
        self._last = token
        self.text.setText(f"{item.path.name}\n{item.path}\n正在读取已保存的详情…")
        self._pending = (item,full,token)
        if self.task is not None:
            self.task.cancel()
        else:self.start_pending()

    def start_pending(self):
        if self._pending is None or self._stopping:return
        item,full,token = self._pending
        self._pending = None
        if not item.file_uid:return
        task = DetailsTask(self.database_path,item.file_uid,full,self)
        self.task = task
        task.ready.connect(lambda data:self.render(data,item,token))
        def finished():
            self.task = None
            task.deleteLater()
            self.start_pending()
        task.finished.connect(finished)
        task.start()

    def render(self,data,item,token):
        if token != self._last:return
        if "error" in data:
            self.text.setText(data["error"])
            return
        from datetime import datetime
        width,height = data["width"],data["height"]
        resolution = f"{width} × {height} ({width*height/1e6:.2f} MP)" if width and height else "未知 / 待读取"
        orientation = "横向" if width and height and width>height else "竖向" if width and height and width<height else "正方形" if width and height else "未知"
        values = [("文件名",data["name"]),("完整路径",data["path"]),("类型",data["extension"]),
            ("大小",f"{data['size']:,} 字节"),("修改时间",datetime.fromtimestamp(data["mtime"]).strftime("%Y-%m-%d %H:%M:%S")),
            ("拍摄时间",data["capture_time_text"] or "待读取"),("时间来源",source_label(data["capture_time_source"])),
            ("设备"," ".join(filter(None,(data["camera_make"],data["camera_model"]))) or "未知 / 无 EXIF"),
            ("分辨率",resolution),("方向",orientation),("AI","已建立 · Chinese-CLIP ViT-B/16" if data["ai"] else "尚未建立"),
            ("相关度",f"{item.similarity:.6f}" if item.similarity is not None else "无 AI 条件"),
            ("OCR","已建立 · PP-OCRv6 small" if data["ocr"] else "尚未建立")]
        html = "".join(f"<p><b>{escape(key)}</b><br>{escape(str(value))}</p>" for key,value in values)
        if data["ocr"]:
            html += "<h3>识别文字</h3><pre>"+escape(data["ocr"]["ocr_text"][:20000])+"</pre>"
        if "exif" in data:
            html += "<h3>全部 EXIF（不展开大型 MakerNote）</h3>"+"".join(f"<p>{escape(k)}：{escape(v)}</p>" for k,v in data["exif"].items())
        self.text.setHtml(html)

    def stop(self):
        self._stopping = True
        self._pending = None
        if self.task is not None:self.task.cancel()

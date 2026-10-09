"""Render real widgets with synthetic media and fictional paths, without models."""
from pathlib import Path
from io import BytesIO
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from seekdex.ai.panel import AIPanel
from seekdex.ocr.panel import OCRPanel
from seekdex.index_manager import IndexManager
from seekdex.search import SearchResult
from seekdex.ui import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def thumbnail(color: str, text: str) -> bytes:
    image = Image.new("RGB", (480, 360), color)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 30, 445, 330), radius=30, fill="white")
    draw.ellipse((160, 90, 320, 250), fill=color)
    draw.text((50, 45), text, fill="black")
    stream = BytesIO()
    image.save(stream, "JPEG")
    return stream.getvalue()


def main() -> None:
    AIPanel.check_status = lambda self: None
    OCRPanel.check_status = lambda self: None
    IndexManager.start = lambda self, operation, parameters=None: None
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    with tempfile.TemporaryDirectory(prefix="seekdex-screenshots-") as temporary:
        window = MainWindow(Path(temporary) / "index.db", Path(temporary) / "cache")
        window.resize(1440, 900)
        window.folder.setText("D:/Photos")
        results = [SearchResult(Path("D:/Photos") / name, 250000, 1778064000,
                    1920, 1080, thumbnail(color, name), is_image=True,
                    capture_time_text="2026-05-06T14:23:10", capture_time_source="exif_datetime_original",
                    camera_make="Synthetic", camera_model="Demo Camera", metadata_version=1)
                   for name, color in (("red-shape.jpg", "#ef6b5a"), ("blue-shape.png", "#468dc5"),
                                       ("green-shape.jpg", "#62ae72"))]
        window.model.append(results)
        window.status.setText("Seekdex · 索星仪 — 合成演示图库，找到 3 个文件。")
        window.ai.info.setText("Chinese-CLIP ViT-B/16 · 本地模型按需安装") if hasattr(window.ai, "info") else None
        window.ai.status.setText("AI 图片内容搜索 / 以图搜图 · 演示未加载模型")
        window.ocr.status.setText("OCR 图片文字搜索 · 演示未加载模型")
        window._set_result_view("grid")
        window.details_sidebar.show()
        window.details_sidebar.text.setPlainText("合成演示图片\n\n文件名\nred-shape.jpg\n\n路径\nD:/Photos/red-shape.jpg\n\n拍摄时间\n2026-05-06 14:23:10\n\n设备\nSynthetic Demo Camera\n\n分辨率\n1920 × 1080")
        window.show()
        manager = IndexManager(Path(temporary) / "index.db", lambda: {}, window)
        manager.receive("catalog", [dict(root_path="D:/Photos", total=3, images=3, ai_count=0,
                    ocr_count=0, last_scan=1778064000, last_error="", coverage="complete",
                    ai_status="not_indexed", ocr_status="not_indexed", recursive=True)], "")
        def save():
            directory = ROOT / "docs/screenshots"
            directory.mkdir(exist_ok=True)
            assert window.grab().save(str(directory / "windows-main.png"))
            manager.show()
            app.processEvents()
            assert manager.grab().save(str(directory / "windows-index-manager.png"))
            manager.close()
            window.close()
            app.quit()
        QTimer.singleShot(800, save)
        app.exec()


if __name__ == "__main__":
    main()

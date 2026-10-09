"""Display original OCR output, safely highlighting literal query terms."""
from __future__ import annotations

from html import escape
import re

from PySide6.QtWidgets import QDialog, QLabel, QTextBrowser, QVBoxLayout, QDialogButtonBox

from .text import query_terms


def highlighted_text(text: str, query: str) -> str:
    terms = query_terms(query)
    if not terms:
        return escape(text)
    pattern = re.compile("|".join(re.escape(term) for term in sorted(set(terms), key=len, reverse=True)), re.IGNORECASE)
    parts = []
    offset = 0
    for match in pattern.finditer(text):
        parts.extend([escape(text[offset:match.start()]), '<span style="background-color:#ffe691; color:#222">',
                      escape(match.group()), '</span>'])
        offset = match.end()
    return "".join(parts) + escape(text[offset:])


class OCRDetailsDialog(QDialog):
    def __init__(self, data: dict | None, query: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("识别文字（本地 OCR）")
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        if data is None:
            browser.setPlainText("这张图片尚无当前版本的有效 OCR。请点击“建立 OCR 索引”。")
        else:
            text = data["ocr_text"]
            browser.setHtml('<pre style="white-space:pre-wrap">' + highlighted_text(text, query) + '</pre>')
            if not text:
                browser.setPlainText("已完成 OCR，未检测到文字。此结果会缓存，不会重复识别。")
            confidence = data["confidence"]
            note = QLabel(f"模型：{data['ocr_model_id']}\n"
                          f"平均置信度：{confidence:.3f}" if confidence is not None else f"模型：{data['ocr_model_id']}")
            note.setWordWrap(True)
            layout.addWidget(note)
            if data["limitations"]:
                limitations = QLabel(data["limitations"])
                limitations.setWordWrap(True)
                layout.addWidget(limitations)
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

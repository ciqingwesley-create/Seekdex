from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTextBrowser
from .app_info import DISPLAY_NAME, VERSION, AUTHOR, HOMEPAGE, LICENSE, DESCRIPTION, RELEASE_STATUS
from .resources import resource_path


class AboutDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 "+DISPLAY_NAME)
        self.resize(650,500)
        layout = QVBoxLayout(self)
        self.version_label = QLabel(f"{DISPLAY_NAME} {VERSION} · {RELEASE_STATUS}")
        layout.addWidget(self.version_label)
        layout.addWidget(QLabel(f"{DESCRIPTION}\nSeekdex is an open-source, local-first intelligent search application.\nCopyright (c) 2026 {AUTHOR} · {LICENSE}（仅 GPL 第 3 版）\n本程序不提供担保；第三方与模型保留各自许可。\n图片、embedding 和 OCR 默认仅在本地处理。"))
        home = QLabel(f'<a href="{HOMEPAGE}">项目主页 / GitHub</a>')
        home.setOpenExternalLinks(True)
        layout.addWidget(home)
        browser = QTextBrowser()
        path = resource_path("THIRD_PARTY_LICENSES.md")
        browser.setMarkdown(path.read_text(encoding="utf8") if path.exists() else "第三方代码与模型分别遵循上游许可，完整许可证随安装包附带。")
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser,1)
        project_license = QPushButton("查看 GNU GPLv3 完整正文")
        project_license.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("LICENSE")))))
        layout.addWidget(project_license)
        button = QPushButton("查看完整许可证目录")
        button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("licenses")))))
        layout.addWidget(button)

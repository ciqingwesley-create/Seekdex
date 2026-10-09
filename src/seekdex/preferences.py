"""Persist the user's search scope independently of the file index."""

from pathlib import Path

from PySide6.QtCore import QSettings

from .paths import get_config_path


class SearchPreferences:
    def __init__(self, path: Path | None = None) -> None:
        self.settings = QSettings(
            str(path or get_config_path()), QSettings.IniFormat
        )

    @property
    def folder(self) -> str:
        saved = str(self.settings.value("search/folder", "")).strip()
        return saved or str(Path.cwd())

    @property
    def recursive(self) -> bool:
        return self.settings.value("search/recursive", True, type=bool)

    def save(self, folder: str, recursive: bool) -> None:
        # An empty edit must not replace the last selected directory.
        if folder.strip():
            self.settings.setValue("search/folder", folder.strip())
        self.settings.setValue("search/recursive", recursive)
        self.settings.sync()

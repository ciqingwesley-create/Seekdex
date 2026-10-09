"""Versioned QSettings profile shared by the UI and background services."""
from pathlib import Path
from PySide6.QtCore import QSettings
from .paths import get_config_path

SCHEMA_VERSION = 1
DEFAULTS = {
    "general/restore_window": True, "general/restore_folder": True,
    "general/remember_filters": False, "general/double_click": "open",
    "general/log_paths": True, "search/default_recursive": True,
    "search/result_limit": 1000, "search/sort": "name", "search/view": "list",
    "search/time_type": "modified", "thumbnail/size": 1, "thumbnail/limit_mb": 2048,
    "ai/backend": "auto", "ai/batch": 0, "ai/top_k": 100,
    "ocr/backend": "auto", "ocr/threads": 4, "onboarding/completed": False,
}


class AppSettings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_config_path()
        self.settings = QSettings(str(self.path), QSettings.IniFormat)
        self.migrate()

    def migrate(self) -> None:
        # Legacy folder/recursive keys are intentionally retained.
        version = int(self.settings.value("config/schema_version", 0))
        if version < SCHEMA_VERSION:
            self.settings.setValue("config/schema_version", SCHEMA_VERSION)
            self.settings.sync()

    def get(self, key: str, default=None):
        default = DEFAULTS.get(key, default)
        if isinstance(default, bool):
            return self.settings.value(key, default, type=bool)
        if isinstance(default, int):
            return self.settings.value(key, default, type=int)
        return self.settings.value(key, default)

    def set(self, key: str, value) -> None:
        self.settings.setValue(key, value)

    def save(self) -> None:
        self.settings.sync()

    @property
    def first_launch(self) -> bool:
        return not self.get("onboarding/completed")

    def complete_onboarding(self) -> None:
        self.set("onboarding/completed", True)
        self.save()

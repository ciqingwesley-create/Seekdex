"""Application-owned storage locations and normalized database path keys."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


APP_DIR_NAME = "Seekdex"


def get_model_cache_dir() -> Path:
    return configured_path("ai/model_dir", get_storage_root() / "models")


def get_storage_root(*, platform: str | None = None,
                     environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """A user-owned product profile; the optional override supports isolated profiles."""
    from .app_info import APP_NAME
    env = os.environ if environ is None else environ
    # The old override is accepted only for existing launch scripts / isolated profiles.
    override = _absolute_env_path(env, "SEEKDEX_HOME") or _absolute_env_path(env, "LOCAL_IMAGE_SEARCH_HOME")
    if override is not None:
        return override
    return get_app_data_dir(platform=platform, environ=env, home=home).parent / APP_NAME


def legacy_profiles(*, platform: str | None = None,
                    environ: Mapping[str, str] | None = None, home: Path | None = None) -> tuple[Path, ...]:
    """Previous product and early development layouts, solely for migration."""
    base = get_app_data_dir(platform=platform, environ=environ, home=home).parent
    return base / "LocalImageSearch", base / "local-image-search"


def isolated_profile() -> bool:
    return any(_absolute_env_path(os.environ, key) is not None
               for key in ("SEEKDEX_HOME", "LOCAL_IMAGE_SEARCH_HOME"))


def get_database_path() -> Path:
    return get_storage_root() / "data" / "database.sqlite3"


def get_config_path() -> Path:
    return get_storage_root() / "config" / "settings.ini"


def configured_path(key: str, default: Path) -> Path:
    from PySide6.QtCore import QSettings
    value = str(QSettings(str(get_config_path()), QSettings.IniFormat).value(key, ""))
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else default


def get_thumbnail_cache_dir() -> Path:
    return configured_path("thumbnail/directory", get_storage_root() / "cache" / "thumbnails")


def get_ocr_model_root() -> Path:
    return configured_path("ocr/model_dir", get_model_cache_dir() / "ocr")


def _absolute_env_path(environ: Mapping[str, str], name: str) -> Path | None:
    value = environ.get(name)
    candidate = Path(value) if value else None
    return candidate if candidate is not None and candidate.is_absolute() else None


def get_app_data_dir(
    *, platform: str | None = None, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if platform == "win32":
        base = _absolute_env_path(environ, "LOCALAPPDATA")
        return (base or home / "AppData" / "Local") / APP_DIR_NAME
    base = _absolute_env_path(environ, "XDG_DATA_HOME")
    return (base or home / ".local" / "share") / APP_DIR_NAME


def get_cache_dir(
    *, platform: str | None = None, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if platform == "win32":
        return get_app_data_dir(platform=platform, environ=environ, home=home) / "thumbnails"
    base = _absolute_env_path(environ, "XDG_CACHE_HOME")
    return (base or home / ".cache") / APP_DIR_NAME / "thumbnails"


def path_key(path: Path) -> str:
    """Comparable path key; preserve the original path separately for display."""
    return os.path.normcase(str(path)).replace("\\", "/")


def descendant_bounds(key: str) -> tuple[str, str]:
    """Range for descendants with a separator boundary, excluding sibling names."""
    prefix = key if key.endswith("/") else key + "/"
    return prefix, prefix[:-1] + "0"


def unique_roots(paths: tuple[Path, ...] | list[Path], recursive: bool = True) -> tuple[Path, ...]:
    """Deduplicate paths and recursive overlaps without accessing the filesystem."""
    from pathlib import PurePosixPath
    ordered = sorted({path_key(p.expanduser().absolute()): p.expanduser().absolute() for p in paths}.items(),
                     key=lambda pair: len(PurePosixPath(pair[0]).parts))
    result: list[tuple[str, Path]] = []
    for key, path in ordered:
        if recursive and any(PurePosixPath(key).is_relative_to(PurePosixPath(parent)) for parent, _ in result):
            continue
        result.append((key, path))
    return tuple(path for _, path in result)

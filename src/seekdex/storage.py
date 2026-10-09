"""Non-destructive migration of the previous development profile."""
from collections.abc import Callable
from pathlib import Path
import logging
import shutil
import sqlite3
import os
from contextlib import closing
from uuid import uuid4
from PySide6.QtCore import QLockFile
from .paths import get_storage_root, get_cache_dir, legacy_profiles, isolated_profile

log = logging.getLogger(__name__)


def copy_missing_tree(source: Path, target: Path, progress: Callable[[str], None] = lambda _: None) -> None:
    if not source.is_dir() or source.resolve() == target.resolve():
        return
    if target.resolve().is_relative_to(source.resolve()):
        raise ValueError("新目录不能位于旧缓存目录内")
    for path in source.rglob("*"):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != source.parent) or not path.is_file() or path.suffix in {".lock", ".tmp", ".part"}:
            continue
        destination = target / path.relative_to(source)
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError("迁移目标含目录链接，已停止以保护其他目录")
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        progress(f"正在迁移缓存：{path.name}")
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
        try:
            shutil.copy2(path, temporary)
            # OpenVINO compiled blobs can be read-only. Copies are application cache;
            # make our temporary copy writable so Windows can remove its extra link.
            temporary.chmod(temporary.stat().st_mode | 0o200)
            # Exclusive creation prevents a second writer from being overwritten.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            except OSError:
                # Never use POSIX rename here: it may overwrite a racing writer.
                raise OSError("缓存文件无法原子迁移，请选择支持硬链接的本地数据目录")
        finally:
            temporary.unlink(missing_ok=True)


def migrate_legacy(root: Path | None = None, legacy: Path | None = None,
                   legacy_cache: Path | None = None,
                   progress: Callable[[str], None] = lambda _: None) -> list[str]:
    root = root or get_storage_root()
    if isolated_profile() and legacy is None:
        # An explicitly isolated profile must never import the user's real database.
        root.mkdir(parents=True,exist_ok=True)
        return []
    legacy = legacy or legacy_profiles()[1]
    legacy_cache = legacy_cache or legacy / "thumbnails"
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / "migration.lock"))
    if not lock.tryLock(10000):
        raise RuntimeError("另一个应用正在迁移数据，请稍后重试")
    notes = []
    try:
        if (root / "config" / "migration-v1.done").is_file():
            return []
        target = root / "data" / "database.sqlite3"
        old = legacy / "index.db"
        if old.is_file() and not target.exists():
            progress("正在安全备份旧数据库，保留索引、AI、OCR 和操作记录…")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name("migration-" + uuid4().hex + ".sqlite3")
            try:
                with closing(sqlite3.connect(old.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(temporary)) as output:
                    source.backup(output, pages=256)
                    if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("旧数据库完整性检查失败，原数据库未修改")
                if not target.exists():
                    temporary.replace(target)
                    notes.append("旧数据库已迁移；原数据库保留")
            finally:
                temporary.unlink(missing_ok=True)
        elif old.is_file() and target.exists():
            notes.append("目标已有数据库，未覆盖；旧数据库保留")
        old_config = legacy / "settings.ini"
        new_config = root / "config" / "settings.ini"
        if old_config.is_file():
            new_config.parent.mkdir(parents=True, exist_ok=True)
            if not new_config.exists():
                shutil.copy2(old_config, new_config)
            else:
                from PySide6.QtCore import QSettings
                source_settings = QSettings(str(old_config),QSettings.IniFormat)
                target_settings = QSettings(str(new_config),QSettings.IniFormat)
                for key in source_settings.allKeys():
                    if not target_settings.contains(key):
                        target_settings.setValue(key,source_settings.value(key))
                target_settings.sync()
        if old.is_file():
            from .settings import AppSettings
            AppSettings(new_config).complete_onboarding()
        # Copy only missing files. Re-running after interruption is safe.
        copy_missing_tree(legacy_cache.parent / "models", root / "models", progress)
        copy_missing_tree(legacy_cache, root / "cache" / "thumbnails", progress)
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "migration-v1.done").write_text("\n".join(notes), encoding="utf8")
        log.info("User data migration complete; %s", "; ".join(notes))
        return notes
    finally:
        lock.unlock()

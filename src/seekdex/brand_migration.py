"""One-time, resumable brand migration. Originals remain available for recovery."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from PySide6.QtCore import QLockFile, QSettings

from .paths import get_storage_root, isolated_profile, legacy_profiles
from .storage import copy_missing_tree

log = logging.getLogger(__name__)


class MigrationConflict(RuntimeError):
    """Both profiles contain independent data; user must choose rather than lose it."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(path)


def merge_settings(source: QSettings, target: QSettings,
                   old: Path | None = None, new: Path | None = None) -> None:
    for key in source.allKeys():
        if target.contains(key):
            continue
        value = source.value(key)
        if old and new and key in {"ai/model_dir", "ocr/model_dir", "thumbnail/directory"}:
            candidate = Path(str(value))
            if candidate.is_absolute() and candidate.is_relative_to(old):
                value = str(new / candidate.relative_to(old))
        target.setValue(key, value)
    target.sync()
    if target.status() != QSettings.NoError:
        raise OSError("无法保存迁移后的设置，旧设置保留")


def migrate_qsettings(root: Path, sources: Sequence[QSettings] | None = None) -> None:
    """INI is current storage; import historical registry namespaces only when missing."""
    target = QSettings(str(root / "config/settings.ini"), QSettings.IniFormat)
    if target.value("migration/qsettings_seekdex", False, type=bool):
        return
    if sources is None:
        sources = (QSettings("LocalImageSearch", "LocalImageSearch"),
                   QSettings("local-image-search", "local-image-search"))
    for source in sources:
        merge_settings(source, target)
    target.setValue("migration/qsettings_seekdex", True)
    target.sync()


def _database(old: Path, new: Path, journal: Path, state: dict,
              progress: Callable[[str], None]) -> None:
    if not old.is_file():
        return
    if old.is_symlink():
        raise MigrationConflict("旧数据库是符号链接，请手工确认数据位置")
    new.parent.mkdir(parents=True, exist_ok=True)
    if new.exists():
        # A durable receipt is written BEFORE publishing the backup. It also handles
        # interruption immediately after the exclusive link is created.
        if state.get("database_sha256") == digest(new):
            return
        raise MigrationConflict("新旧目录都有数据库，未覆盖任何数据。请先备份并选择要使用的数据库；旧目录保留。")
    progress("正在迁移数据库：保留文件 UID、AI embedding、OCR 和操作记录…")
    temporary = new.with_name("brand-migration-" + uuid4().hex + ".sqlite3")
    try:
        with closing(sqlite3.connect(old.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as output:
                source.backup(output, pages=256)
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("旧数据库完整性检查失败，原数据未修改")
        state["database_sha256"] = digest(temporary)
        _write_json(journal, state)
        try:
            os.link(temporary, new)
        except FileExistsError as exc:
            raise MigrationConflict("迁移时目标数据库出现，未覆盖，请重试") from exc
    finally:
        temporary.unlink(missing_ok=True)


def migrate_brand(root: Path | None = None, old: Path | None = None,
                  progress: Callable[[str], None] = lambda _: None) -> list[str]:
    root = root or get_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    # Explicit diagnostic migration source, never accidentally import real profiles
    # into a test profile. The legacy HOME override remains accepted by paths.py.
    override = os.environ.get("SEEKDEX_LEGACY_HOME")
    if old is None and override:
        old = Path(override)
        if not old.is_absolute():
            raise ValueError("SEEKDEX_LEGACY_HOME 必须为绝对路径")
    if old is None and isolated_profile():
        return []
    old = old or legacy_profiles()[0]
    if not old.is_dir() or old.resolve() == root.resolve():
        if not isolated_profile():
            migrate_qsettings(root)
        return []
    if old.is_symlink() or root.resolve().is_relative_to(old.resolve()):
        raise ValueError("迁移源不能是链接，新目录不能位于旧目录内")
    marker = root / "config/brand-migration-v1.done"
    if marker.exists():
        return []
    lock = QLockFile(str(root / "brand-migration.lock"))
    old_lock = QLockFile(str(old / "application.lock"))
    if not lock.tryLock(0):
        raise RuntimeError("另一个 Seekdex 正在迁移，请稍后重试")
    try:
        old_lock.setStaleLockTime(0)
        if not old_lock.tryLock(0):
            raise RuntimeError("请先关闭旧 LocalImageSearch 再迁移，原数据保留")
        journal = root / "config/brand-migration-progress.json"
        state = json.loads(journal.read_text(encoding="utf8")) if journal.exists() else {"schema": 1}
        if state.get("source") not in (None, str(old.resolve())):
            raise MigrationConflict("迁移源发生变化，请先确认旧数据位置")
        state["source"] = str(old.resolve())
        _database(old / "data/database.sqlite3", root / "data/database.sqlite3", journal, state, progress)
        for folder in ("models", "cache"):
            source, target = old / folder, root / folder
            # Models may include tuned/compiled models. Never silently replace a
            # different existing model. Nonessential thumbnail conflicts keep new.
            if folder == "models" and source.exists():
                for path in source.rglob("*"):
                    destination = target / path.relative_to(source)
                    if path.is_file() and not path.is_symlink() and destination.is_file():
                        if path.suffix not in {".lock", ".tmp", ".part"} and digest(path) != digest(destination):
                            raise MigrationConflict("新旧模型缓存存在不同内容，未覆盖，请先确认缓存目录")
            copy_missing_tree(source, target, progress)
        old_settings = old / "config/settings.ini"
        if old_settings.is_file():
            merge_settings(QSettings(str(old_settings), QSettings.IniFormat),
                           QSettings(str(root / "config/settings.ini"), QSettings.IniFormat), old, root)
        copy_missing_tree(old / "logs", root / "logs/legacy", progress)
        migrate_qsettings(root, sources=() if isolated_profile() else None)
        if (old / "data/database.sqlite3").exists():
            from .settings import AppSettings
            AppSettings(root / "config/settings.ini").complete_onboarding()
        _write_json(marker, {"schema": 1, "complete": True, "original_retained": True})
        notes = ["LocalImageSearch → Seekdex 迁移完成；原目录保留；未重新建立 AI / OCR 索引"]
        log.info(notes[0])
        return notes
    except Exception:
        log.exception("Brand migration interrupted; originals retained")
        raise
    finally:
        old_lock.unlock()
        lock.unlock()

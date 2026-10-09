"""Exclusive file operations with a durable SQLite journal and verified undo."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
from time import time
from PySide6.QtCore import QLockFile

from ..index.database import FileIndex
from ..paths import path_key
from ..thumbnail_cache import ThumbnailCache
from .models import OrganizePlan, OperationResult, PlanItem
from .templates import safe_target


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _same_source(item: PlanItem) -> bool:
    if item.source.is_symlink() or item.source.resolve() != item.source:
        return False
    stat = item.source.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino) == (
        item.size, item.mtime_ns, item.device, item.inode,
    )


def _materialize(source: Path, target: Path, move: bool) -> str:
    """Never replace a destination. Cross-volume moves copy fully before unlinking."""
    original = source.stat()
    linked = False
    if move:
        try:
            os.link(source, target)
            linked = True
        except FileExistsError:
            raise
        except OSError:
            pass
    if not linked:
        created = False
        created_identity = None
        try:
            with target.open("xb") as output:
                created = True
                created_stat = os.fstat(output.fileno())
                created_identity = (created_stat.st_dev, created_stat.st_ino)
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            shutil.copystat(source, target, follow_symlinks=False)
            digest = file_hash(target)
            if file_hash(source) != digest:
                raise OSError("复制校验失败")
        except Exception:
            if created:
                try:
                    current = target.lstat()
                    if (current.st_dev, current.st_ino) == created_identity:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise
    owned_stat = target.stat()
    try:
        if linked:
            digest = file_hash(target)
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        return digest
    except Exception:
        current = target.lstat()
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
            owned_stat.st_dev, owned_stat.st_ino, owned_stat.st_size, owned_stat.st_mtime_ns
        ):
            target.unlink()
        raise


def _owned(path: Path, row: dict) -> bool:
    try:
        if path.is_symlink() or path.resolve() != path:
            return False
        stat = path.stat()
        return (
            stat.st_size == row["size"] and stat.st_mtime_ns == row["mtime_ns"]
            and stat.st_dev == int(str(row["target_dev"]), 0) and str(stat.st_ino) == row["target_ino"]
            and file_hash(path) == row["sha256"]
        )
    except OSError:
        return False


class Organizer:
    def __init__(self, database: FileIndex, cache: ThumbnailCache) -> None:
        self.database = database
        self.cache = cache

    @contextmanager
    def _operation_lock(self):
        lock_path = self.database.database_path.with_name(self.database.database_path.name + ".organize.lock")
        lock = QLockFile(str(lock_path))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise RuntimeError("另一个整理或撤销任务正在运行，请稍后重试")
        try:
            yield
        finally:
            lock.unlock()

    def execute(
        self, plan: OrganizePlan, *, dry_run: bool = True,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OperationResult]:
        if dry_run:
            return [OperationResult(item.source, item.target, "preview", item.detail) for item in plan.items]
        with self._operation_lock():
            return self._execute_plan(plan, cancelled, progress)

    def _execute_plan(self, plan: OrganizePlan, cancelled: Callable[[], bool],
                      progress: Callable[[int, int], None] | None) -> list[OperationResult]:
        if plan.action not in {"move", "copy"}:
            raise ValueError("无效的整理操作")
        if self.database.connection.execute("SELECT 1 FROM organize_batches WHERE id=?", (plan.batch_id,)).fetchone():
            raise ValueError("这份预览已经执行过，请重新生成预览")
        self.database.connection.execute(
            "INSERT INTO organize_batches VALUES (?, ?, ?, ?, ?, ?)",
            (plan.batch_id, plan.created_at, plan.action, str(plan.target_root), plan.template, plan.conflict_policy),
        )
        self.database.commit()
        results = []
        for item in plan.items:
            if cancelled():
                break
            results.append(self._execute_item(plan, item))
            if progress:
                progress(len(results), len(plan.items))
        return results

    def _log(self, plan: OrganizePlan, item: PlanItem, operation: str, related: int | None = None) -> int:
        cursor = self.database.connection.execute(
            """INSERT INTO file_operations
               (batch_id, file_id, source_path, target_path, operation, operated_at, status, related_operation_id)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (plan.batch_id, item.file_id, str(item.source), str(item.target or ""), operation, time(), related),
        )
        self.database.commit()
        return int(cursor.lastrowid)

    def _status(self, operation_id: int, status: str, error: str = "", file_id: int | None = None) -> None:
        self.database.connection.execute(
            """UPDATE file_operations SET status=?, success=?, error=?,
               file_id=COALESCE(?, file_id) WHERE id=?""",
            (status, int(status == "success"), error, file_id, operation_id),
        )

    def _sync_index(self, item: PlanItem, target: Path, root: Path, move: bool) -> int:
        record = self.database.get_by_id(item.file_id or -1)
        if record is None or path_key(record.path) != path_key(item.source):
            raise ValueError("源索引记录已变化，请重新生成预览")
        stale = self.database.get_by_key(path_key(target))
        if stale is not None and stale.id != record.id:
            self.database.delete_path(path_key(target))
        if move:
            self.database.relocate(record.id, target, root)
            return record.id
        stat = target.stat()
        self.database.upsert(replace(
            record, path=target, name=target.name, extension=target.suffix.lower().lstrip('.'),
            size=stat.st_size, mtime=stat.st_mtime, mtime_ns=stat.st_mtime_ns,
            root_path=root, indexed_at=time(), id=None, file_uid=None,
        ))
        added = self.database.get_by_key(path_key(target))
        if added is None:
            raise RuntimeError("无法写入副本索引")
        return added.id

    def _execute_item(self, plan: OrganizePlan, item: PlanItem,
                      operation: str | None = None, related: int | None = None) -> OperationResult:
        operation = operation or plan.action
        operation_id = self._log(plan, item, operation, related)
        target = item.target
        fingerprint: dict | None = None
        source_removed = False
        try:
            if item.status != "ready" or target is None:
                self._status(operation_id, "skipped", item.detail)
                self.database.commit()
                return OperationResult(item.source, target, "skipped", item.detail, operation_id)
            if not _same_source(item):
                raise ValueError("源文件自预览后已变化，请重新预览")
            target = safe_target(plan.target_root, target.relative_to(plan.target_root))
            if target.exists() or target.is_symlink():
                raise FileExistsError("目标自预览后已出现文件，请重新预览")
            target.parent.mkdir(parents=True, exist_ok=True)
            safe_target(plan.target_root, target.relative_to(plan.target_root))
            digest = _materialize(item.source, target, plan.action == "move")
            stat = target.stat()
            fingerprint = dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                               sha256=digest, target_dev=hex(stat.st_dev), target_ino=str(stat.st_ino))
            self.database.connection.execute(
                """UPDATE file_operations SET status='materialized', size=?, mtime_ns=?,
                   sha256=?, target_dev=?, target_ino=? WHERE id=?""",
                (*fingerprint.values(), operation_id),
            )
            self.database.commit()
            if not _same_source(item):
                raise ValueError("处理过程中源文件发生变化")
            if plan.action == "move":
                item.source.unlink()
                source_removed = True
            with self.database.transaction():
                file_id = self._sync_index(item, target, plan.target_root, plan.action == "move")
                self._status(operation_id, "success", file_id=file_id)
                if operation == "undo_move" and related is not None:
                    self.database.connection.execute(
                        "UPDATE file_operations SET undone_at=?, undo_status='success', undo_error='' WHERE id=?",
                        (time(), related),
                    )
            try:
                self.cache.reuse_for_path(item.source, target, stat.st_mtime_ns, stat.st_size)
            except Exception:
                pass
            return OperationResult(item.source, target, "success", "", operation_id)
        except Exception as exc:
            self.database.connection.rollback()
            status = "failed"
            message = str(exc)
            try:
                if fingerprint is not None and target is not None:
                    if not _owned(target, fingerprint):
                        raise OSError("目标文件已变化，保留文件等待核对")
                    if source_removed:
                        if item.source.exists() or item.source.is_symlink():
                            raise FileExistsError("原位置出现文件，无法自动恢复")
                        _materialize(target, item.source, True)
                    target.unlink()
            except Exception as recovery_error:
                status = "needs_review"
                message += f"；恢复未完成：{recovery_error}"
            self._status(operation_id, status, message)
            self.database.commit()
            return OperationResult(item.source, target, status, message, operation_id)

    def history(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM file_operations ORDER BY id DESC"
        )]

    def undo(self, operation_id: int) -> OperationResult:
        with self._operation_lock():
            return self._undo(operation_id)

    def _undo(self, operation_id: int) -> OperationResult:
        row = self.database.connection.execute("SELECT * FROM file_operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise ValueError("操作日志不存在")
        entry = dict(row)
        source, target = Path(entry["source_path"]), Path(entry["target_path"])
        try:
            if entry["status"] != "success" or entry["undone_at"] is not None or entry["operation"] not in {"move", "copy"}:
                raise ValueError("该操作不能撤销或已经撤销")
            if not _owned(target, entry):
                raise ValueError("目标文件已修改、被替换或无法确认身份，不能撤销")
            if entry["operation"] == "move":
                if source.exists() or source.is_symlink():
                    raise FileExistsError("原位置已存在同名文件，撤销不会覆盖它")
                stat = target.stat()
                item = PlanItem(target, source, entry["file_id"], stat.st_size, stat.st_mtime_ns,
                                stat.st_dev, stat.st_ino, None, "", "")
                plan = OrganizePlan.create(source.parent, "move", "撤销移动", "skip", [item])
                self.database.connection.execute("INSERT INTO organize_batches VALUES (?, ?, ?, ?, ?, ?)",
                    (plan.batch_id, plan.created_at, plan.action, str(plan.target_root), plan.template, plan.conflict_policy))
                self.database.commit()
                result = self._execute_item(plan, item, "undo_move", operation_id)
                if result.status != "success":
                    raise OSError(result.message)
            else:
                plan = OrganizePlan.create(target.parent, "copy", "删除本次副本", "skip", [])
                item = PlanItem(target, target, entry["file_id"], entry["size"], entry["mtime_ns"],
                                int(str(entry["target_dev"]), 0), int(entry["target_ino"]), None, "", "")
                self.database.connection.execute("INSERT INTO organize_batches VALUES (?, ?, ?, ?, ?, ?)",
                    (plan.batch_id, plan.created_at, plan.action, str(plan.target_root), plan.template, plan.conflict_policy))
                deletion_id = self._log(plan, item, "delete_copy", operation_id)
                deleted = False
                try:
                    if not _owned(target, entry):
                        raise ValueError("副本身份发生变化")
                    target.unlink()
                    deleted = True
                    with self.database.transaction():
                        record = self.database.get_by_id(entry["file_id"])
                        if record is not None and path_key(record.path) == path_key(target):
                            self.database.delete_path(path_key(target))
                        self._status(deletion_id, "success")
                        self.database.connection.execute(
                            "UPDATE file_operations SET undone_at=?, undo_status='success', undo_error='' WHERE id=?",
                            (time(), operation_id),
                        )
                    result = OperationResult(target, None, "success", "已删除本次副本", deletion_id)
                except Exception as exc:
                    self._status(deletion_id, "pending" if deleted else "failed", str(exc))
                    self.database.commit()
                    raise
            return result
        except Exception as exc:
            self.database.connection.execute("UPDATE file_operations SET undo_status='failed', undo_error=? WHERE id=?",
                                             (str(exc), operation_id))
            self.database.commit()
            return OperationResult(target, source, "failed", str(exc), operation_id)

    def recover_interrupted(self) -> None:
        """Reconcile a verified move completed before a crash; never guess or delete files."""
        with self._operation_lock():
            self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        rows = self.database.connection.execute(
            "SELECT * FROM file_operations WHERE status IN ('pending', 'materialized')"
        ).fetchall()
        for row in rows:
            entry = dict(row)
            source, target = Path(entry["source_path"]), Path(entry["target_path"])
            try:
                if entry["operation"] == "delete_copy" and not target.exists() and not target.is_symlink():
                    with self.database.transaction():
                        self.database.delete_path(path_key(target))
                        self._status(entry["id"], "success", "中断后已核对副本删除")
                        self.database.connection.execute("UPDATE file_operations SET undone_at=?, undo_status='success', undo_error='' WHERE id=?",
                                                         (time(), entry["related_operation_id"]))
                elif entry["status"] == "materialized" and _owned(target, entry) and not source.exists() and entry["operation"] in {"move", "undo_move"}:
                    if self.database.get_by_id(entry["file_id"]) is None:
                        raise ValueError("源索引记录缺失，需要人工核对")
                    with self.database.transaction():
                        self.database.relocate(entry["file_id"], target, target.parent)
                        self._status(entry["id"], "success", "中断后已核对并恢复索引")
                        if entry["operation"] == "undo_move":
                            self.database.connection.execute("UPDATE file_operations SET undone_at=?, undo_status='success' WHERE id=?",
                                                             (time(), entry["related_operation_id"]))
                elif entry["status"] == "materialized" and _owned(target, entry) and entry["operation"] == "copy":
                    stat = target.stat()
                    item = PlanItem(source, target, entry["file_id"], stat.st_size, stat.st_mtime_ns,
                                    stat.st_dev, stat.st_ino, None, "", "")
                    with self.database.transaction():
                        file_id = self._sync_index(item, target, target.parent, False)
                        self._status(entry["id"], "success", "中断后已核对并恢复副本索引", file_id)
                elif not target.exists() and source.is_file():
                    self._status(entry["id"], "failed", "操作中断，源文件保留")
                else:
                    self._status(entry["id"], "needs_review", "操作中断，文件已保留；请核对原路径与目标路径")
                self.database.commit()
            except Exception as exc:
                self.database.connection.rollback()
                self._status(entry["id"], "needs_review", str(exc))
                self.database.commit()

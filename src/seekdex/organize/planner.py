"""Build an immutable preview without changing any source or target file."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import time

from ..capture_time import ensure_capture_time
from ..index.database import FileIndex
from ..index.models import FileRecord
from ..paths import path_key
from ..search import is_image_path
from .models import DEFAULT_TEMPLATE, OrganizePlan, PlanItem
from .templates import render_relative, safe_target, validate_template


def current_record(path: Path, database: FileIndex) -> FileRecord:
    stat = path.stat()
    old = database.get_by_key(path_key(path))
    if old is not None and (old.size, old.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
        return old
    record = FileRecord(
        path=path, name=path.name, extension=path.suffix.lower().lstrip('.'),
        size=stat.st_size, mtime=stat.st_mtime, mtime_ns=stat.st_mtime_ns,
        is_image=is_image_path(path), width=None, height=None,
        root_path=old.root_path if old else path.parent, indexed_at=time(),
    )
    database.upsert(record)
    return database.get_by_key(path_key(path)) or record


def prepare_plan(
    sources: Iterable[Path], target_root: Path, database: FileIndex,
    *, action: str = "copy", template: str = DEFAULT_TEMPLATE,
    conflict_policy: str = "skip", cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int], None] | None = None,
) -> OrganizePlan:
    if action not in {"move", "copy"} or conflict_policy not in {"skip", "rename"}:
        raise ValueError("无效的操作类型或冲突策略")
    validate_template(template)
    root = target_root.expanduser().resolve()
    items: list[PlanItem] = []
    reserved: set[str] = set()
    seen: set[str] = set()
    for candidate in sources:
        if cancelled():
            break
        source = candidate.expanduser().absolute()
        if path_key(source) in seen:
            continue
        seen.add(path_key(source))
        item = PlanItem(source, None, None, 0, 0, 0, 0, None, "", "", "error")
        try:
            if source.is_symlink() or not source.is_file():
                raise ValueError("源文件不存在、不是普通文件或为符号链接")
            source = source.resolve()
            record = ensure_capture_time(current_record(source, database), database)
            stat = source.stat()
            if (record.size, record.mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                raise ValueError("读取时间信息期间源文件发生变化，请重新预览")
            date = datetime.fromisoformat(record.capture_time_text or "")
            target = safe_target(root, render_relative(template, date, source))
            detail = ""
            conflict = target.exists() or target.is_symlink() or path_key(target) in reserved
            status = "ready"
            if path_key(source) == path_key(target):
                status, detail, conflict = "skip", "源路径与目标路径相同", True
            elif conflict:
                if conflict_policy == "skip":
                    status, detail = "skip", "目标已存在或本批次重名"
                else:
                    number = 1
                    initial = target
                    while target.exists() or target.is_symlink() or path_key(target) in reserved:
                        target = safe_target(root, initial.with_name(f"{initial.stem}_{number}{initial.suffix}").relative_to(root))
                        number += 1
                    detail = "冲突已自动编号"
            if status == "ready":
                reserved.add(path_key(target))
            item = PlanItem(source, target, record.id, stat.st_size, stat.st_mtime_ns,
                            stat.st_dev, stat.st_ino, record.capture_time,
                            record.capture_time_text or "", record.capture_time_source or "",
                            status, detail, conflict)
        except Exception as exc:
            item = replace(item, detail=str(exc))
        items.append(item)
        if progress and len(items) % 50 == 0:
            progress(len(items))
    database.commit()
    return OrganizePlan.create(root, action, template, conflict_policy, items)

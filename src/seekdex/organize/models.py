from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import time
from uuid import uuid4

DEFAULT_TEMPLATE = "{year}y/{month}m/{day}d/{filename}"


@dataclass(frozen=True)
class PlanItem:
    source: Path
    target: Path | None
    file_id: int | None
    size: int
    mtime_ns: int
    device: int
    inode: int
    capture_time: float | None
    capture_time_text: str
    capture_time_source: str
    status: str = "ready"
    detail: str = ""
    conflict: bool = False


@dataclass(frozen=True)
class OrganizePlan:
    target_root: Path
    action: str
    template: str
    conflict_policy: str
    items: tuple[PlanItem, ...]
    batch_id: str
    created_at: float

    @classmethod
    def create(cls, target_root: Path, action: str, template: str,
               conflict_policy: str, items: list[PlanItem]) -> OrganizePlan:
        return cls(target_root, action, template, conflict_policy, tuple(items), uuid4().hex, time())


@dataclass(frozen=True)
class OperationResult:
    source: Path
    target: Path | None
    status: str
    message: str = ""
    operation_id: int | None = None

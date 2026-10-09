"""Strict portable templates and containment checks."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PureWindowsPath
from string import Formatter

NUMBERS = {"year", "month", "day", "hour", "minute", "second"}
NAMES = {"filename", "stem", "ext"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _component(value: str) -> None:
    if (not value or value in {".", ".."} or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"\\|?*' for c in value)
            or value.split(".")[0].upper() in RESERVED):
        raise ValueError(f"不安全或不兼容的路径名称：{value}")


def validate_template(template: str) -> None:
    if not template.strip() or len(template) > 2048:
        raise ValueError("路径模板不能为空或过长")
    try:
        for _literal, field, spec, conversion in Formatter().parse(template):
            if field is None:
                continue
            if field not in NUMBERS | NAMES or conversion:
                raise ValueError(f"不支持的模板变量：{field}")
            if spec and (field not in NUMBERS or not re.fullmatch(r"0?[1-9][0-9]?d?", spec)):
                raise ValueError(f"不支持的格式：{field}:{spec}")
            if spec and int(spec.rstrip('d')) > 20:
                raise ValueError("补零宽度不能超过 20")
        render_relative(template, datetime(2026, 5, 6, 14, 23, 10), Path("example.jpg"), validated=True)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"路径模板无效：{exc}") from exc


def render_relative(template: str, date: datetime, source: Path, *, validated: bool = False) -> Path:
    if not validated:
        validate_template(template)
    _component(source.name)
    values = {name: getattr(date, name) for name in NUMBERS}
    values.update(filename=source.name, stem=source.stem, ext=source.suffix)
    rendered = template.format(**values).replace("\\", "/")
    windows = PureWindowsPath(rendered)
    path = Path(rendered)
    if path.is_absolute() or windows.drive or windows.root:
        raise ValueError("模板必须生成相对路径")
    parts = rendered.split("/")
    for part in parts:
        _component(part)
    return Path(*parts)


def safe_target(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or any(part in {"..", "."} for part in relative.parts):
        raise ValueError("目标路径不能逃出目标目录")
    for component in relative.parts:
        _component(component)
    root = root.absolute()
    if root.resolve() != root:
        raise ValueError("目标根目录发生变化或包含符号链接")
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ValueError("目标路径逃出目标目录")
    for part in (target, *target.parents):
        if part == root.parent:
            break
        if part.is_symlink() or part.resolve() != part:
            raise ValueError("目标路径不能经过符号链接")
        if part != target and part.exists() and not part.is_dir():
            raise ValueError("目标路径的父级不是文件夹")
    return target

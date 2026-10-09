"""Only application-generated thumbnail files are eligible for removal."""
from pathlib import Path
import re

NAME = re.compile(r"[0-9a-f]{64}\.(jpg|missing)\Z")


def cache_entries(directory: Path) -> list[tuple[Path, int, float]]:
    entries = []
    if not directory.is_dir():
        return entries
    for path in directory.iterdir():
        try:
            if NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                stat = path.stat()
                entries.append((path, stat.st_size, stat.st_mtime))
        except OSError:
            continue
    return entries


def prune_thumbnails(directory: Path, max_bytes: int, *, clear: bool = False) -> int:
    entries = cache_entries(directory)
    total = sum(size for _, size, _ in entries)
    removed = 0
    for path, size, _ in sorted(entries, key=lambda entry: entry[2]):
        if not clear and (max_bytes <= 0 or total <= max_bytes):
            break
        try:
            path.unlink()
            removed += 1
            total -= size
        except OSError:
            continue
    return removed

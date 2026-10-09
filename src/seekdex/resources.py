"""Resources stay next to the package in both source and PyInstaller builds."""
from pathlib import Path


def resource_path(name: str) -> Path:
    root = Path(__file__).resolve().parent / "resources"
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Invalid resource path")
    return target

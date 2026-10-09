"""Make third-party libraries safe in pythonw / PyInstaller windowed processes."""
import os
import sys


def prepare_windowed_output() -> None:
    """Keep real console streams; supply bounded sinks only when streams are absent."""
    # Use the normal HTTP streaming path. A native Xet download can hold the
    # Python interpreter long enough to starve Qt's Python signal handlers.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    windowed = sys.stdout is None or sys.stderr is None
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            stream = open(os.devnull, "w", encoding="utf8")
            setattr(sys, name, stream)
            if getattr(sys, "__" + name + "__") is None:
                setattr(sys, "__" + name + "__", stream)
    if windowed:
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

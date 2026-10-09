"""Windowed PyInstaller entry point."""
import os
import sys
import traceback
from pathlib import Path

if __name__ == "__main__":
    try:
        from seekdex.runtime_output import prepare_windowed_output
        prepare_windowed_output()
        from seekdex.app import main
        result = main()
    except Exception:
        # Capture even import-time DLL errors, before the Qt exception reporter exists.
        directory = Path(os.environ.get("SEEKDEX_HOME") or
            (Path(os.environ.get("LOCALAPPDATA",Path.home()))/"Seekdex"))/"logs"
        directory.mkdir(parents=True,exist_ok=True)
        (directory/"startup-error.log").write_text(traceback.format_exc(),encoding="utf8")
        raise
    raise SystemExit(result)

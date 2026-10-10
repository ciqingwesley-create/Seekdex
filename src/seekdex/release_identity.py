"""Fail-closed identity checks shared by frozen executables and release tooling."""
from __future__ import annotations

import ast
import ctypes
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import zipfile

from . import app_info


def identity() -> dict:
    return dict(version=app_info.VERSION, package_version=app_info.PACKAGE_VERSION,
                windows_version=list(app_info.WINDOWS_VERSION),
                license_expression=app_info.LICENSE, homepage=app_info.HOMEPAGE)


def validate_identity(actual: dict, *, commit: str | None = None) -> None:
    for key, expected in identity().items():
        if actual.get(key) != expected:
            raise ValueError(f"Release identity mismatch: {key}")
    source_commit = actual.get("commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Invalid source commit")
    if commit is not None and source_commit != commit:
        raise ValueError("Release identity mismatch: commit")
    if actual.get("source_url") != f"{app_info.HOMEPAGE}/tree/{source_commit}":
        raise ValueError("Release identity mismatch: source_url")


def sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_pe_identity(path: Path) -> dict:
    """Read the actual Windows version resource without executing the installer."""
    if sys.platform != "win32":
        raise RuntimeError("PE verification requires Windows")
    from ctypes import wintypes
    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                 ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    ignored = wintypes.DWORD()
    size = api.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size:
        raise ValueError("Missing EXE version resource")
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise ctypes.WinError(ctypes.get_last_error())

    def query(key: str) -> tuple[int, int]:
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not api.VerQueryValueW(buffer, key, ctypes.byref(pointer), ctypes.byref(length)):
            raise ValueError(f"Missing EXE resource: {key}")
        return pointer.value, length.value

    pointer, _ = query("\\")
    fixed = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    split = lambda high, low: [high >> 16, high & 65535, low >> 16, low & 65535]
    result = dict(windows_version=split(fixed[2], fixed[3]),
                  product_windows_version=split(fixed[4], fixed[5]))
    pointer, _ = query("\\VarFileInfo\\Translation")
    translation = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
    table = f"{translation[0]:04x}{translation[1]:04x}"
    for key in ("FileVersion", "ProductVersion", "FileDescription", "Comments", "SourceCommit", "Homepage"):
        try:
            pointer, _ = query(f"\\StringFileInfo\\{table}\\{key}")
            result[key] = ctypes.wstring_at(pointer)
        except ValueError:
            result[key] = ""
    return result


def validate_pe(actual: dict, provenance: dict, *, installer: bool = False) -> None:
    for key in ("windows_version", "product_windows_version"):
        if actual.get(key) != provenance["windows_version"]:
            raise ValueError(f"EXE numeric version mismatch: {key}")
    for key in ("FileVersion", "ProductVersion"):
        if actual.get(key) != provenance["version"]:
            raise ValueError(f"EXE text version mismatch: {key}")
    if installer:
        expected = installer_description(provenance)
        if actual.get("FileDescription") != expected:
            raise ValueError("Installer license/homepage/commit mismatch")
    elif (actual.get("SourceCommit") != provenance["commit"]
          or actual.get("Homepage") != provenance["homepage"]
          or actual.get("Comments") != f"Seekdex source license: {provenance['license_expression']}; third-party licenses retained."):
        raise ValueError("EXE license/homepage/commit mismatch")


def installer_description(provenance: dict) -> str:
    return f"Seekdex; {provenance['license_expression']}; {provenance['homepage']}; source {provenance['commit']}"


def verify_frozen_identity() -> dict:
    if not getattr(sys, "frozen", False):
        raise ValueError("Identity verification must execute the packaged EXE")
    from importlib.metadata import metadata
    from .resources import resource_path
    provenance = json.loads(resource_path("source-provenance.json").read_text(encoding="utf8"))
    validate_identity(provenance)
    distribution = metadata("seekdex")
    if distribution["Version"] != app_info.PACKAGE_VERSION or distribution["License-Expression"] != app_info.LICENSE:
        raise ValueError("Packaged Python metadata mismatch")
    source = resource_path("application-source.zip").read_bytes()
    if hashlib.sha256(source).hexdigest() != provenance["source_sha256"]:
        raise ValueError("Packaged source archive hash mismatch")
    with zipfile.ZipFile(BytesIO(source)) as archive:
        values = {node.targets[0].id: ast.literal_eval(node.value)
                  for node in ast.parse(archive.read("src/seekdex/app_info.py")).body
                  if isinstance(node, ast.Assign)}
        for key in ("VERSION", "PACKAGE_VERSION", "WINDOWS_VERSION", "LICENSE", "HOMEPAGE"):
            if values[key] != getattr(app_info, key):
                raise ValueError(f"Compiled code/source mismatch: {key}")
        for name in ("LICENSE", "README.md", "COPYRIGHT.md", "THIRD_PARTY_LICENSES.md", "THIRD_PARTY_NOTICES.md"):
            if archive.read(name) != resource_path(name).read_bytes():
                raise ValueError(f"Packaged notice/source mismatch: {name}")
    license_text = resource_path("LICENSE").read_text(encoding="utf8")
    if len(license_text) < 30000 or "END OF TERMS AND CONDITIONS" not in license_text:
        raise ValueError("Incomplete GPLv3 license")
    pe = read_pe_identity(Path(sys.executable))
    validate_pe(pe, provenance)
    return dict(**provenance, frozen=True, exe_sha256=sha256(Path(sys.executable)), pe=pe)

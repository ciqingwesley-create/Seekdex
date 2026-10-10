"""Mandatory release gates; never installs software, downloads models or publishes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from seekdex import app_info
from seekdex.release_identity import identity, read_pe_identity, sha256, validate_identity, validate_pe


def preflight() -> str:
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT)
    if status.strip():
        raise ValueError("Release builds require a clean, committed source tree")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf8"))["project"]
    if project["license"] != app_info.LICENSE or project["urls"]["Homepage"] != app_info.HOMEPAGE:
        raise ValueError("pyproject license/homepage mismatch")
    from importlib.metadata import metadata
    installed = metadata("seekdex")
    if installed["Version"] != app_info.PACKAGE_VERSION or installed["License-Expression"] != app_info.LICENSE:
        raise ValueError("Stale build environment: reinstall the current seekdex package")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def standard_payload(names) -> None:
    for name in names:
        path = Path(name)
        if (path.suffix.lower() in {".onnx", ".safetensors", ".pt", ".pth", ".ckpt"}
                or path.parts[0].lower() == "models"
                or any(part.lower() in {"preinstalled-models", "model-cache", "hf-cache"} for part in path.parts)
                or path.name.lower() in {"pytorch_model.bin", "image.bin", "text.bin"}):
            raise ValueError(f"Model weights/cache forbidden in standard release: {name}")


def payload_hashes(directory: Path) -> dict:
    return {path.relative_to(directory).as_posix(): sha256(path)
            for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "build-verification.json"}


def verify_exe(directory: Path) -> dict:
    commit = preflight()
    from release_licenses import verify_directory
    verify_directory(directory)
    report_path = ROOT / "build/generated/exe-identity.json"
    report_path.unlink(missing_ok=True)
    environment = {key.upper(): value for key, value in os.environ.items()}
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_QPA_PLATFORM"):
        environment.pop(key, None)
    environment["PATH"] = environment["SYSTEMROOT"] + "/System32;" + environment["SYSTEMROOT"]
    environment["SEEKDEX_HOME"] = str(ROOT / "build/identity-profile")
    completed = subprocess.run([str(directory / "Seekdex.exe"), "--verify-identity", str(report_path)],
                               cwd=directory, env=environment, timeout=45, check=False)
    if completed.returncode or not report_path.is_file():
        raise ValueError("Packaged EXE identity probe failed")
    report = json.loads(report_path.read_text(encoding="utf8"))
    validate_identity(report, commit=commit)
    if report.get("error") or not report.get("frozen") or report.get("exe_sha256") != sha256(directory / "Seekdex.exe"):
        raise ValueError("Packaged EXE verification/hash mismatch")
    files = payload_hashes(directory)
    standard_payload(files)
    receipt = dict(runtime=report, files=files)
    (directory / "build-verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return report


def verify_receipt(directory: Path) -> dict:
    commit = preflight()
    from release_licenses import verify_directory
    verify_directory(directory)
    receipt = json.loads((directory / "build-verification.json").read_text(encoding="utf8"))
    validate_identity(receipt["runtime"], commit=commit)
    if receipt["files"] != payload_hashes(directory):
        raise ValueError("Distribution changed after actual EXE verification")
    if receipt["runtime"].get("exe_sha256") != receipt["files"].get("Seekdex.exe"):
        raise ValueError("Unverified executable")
    standard_payload(receipt["files"])
    return receipt


def planned_files(version: str, edition: str = "standard") -> list[str]:
    if version != app_info.VERSION:
        raise ValueError("Requested release version differs from source version")
    suffix = "-Preinstalled" if edition == "preinstalled" else ""
    return [f"Seekdex-{version}-Windows-x64-Portable{suffix}.zip",
            f"Seekdex-{version}-Windows-x64-Setup{suffix}.exe"]


def verify_tag(tag: str, commit: str) -> None:
    if tag != "v" + app_info.VERSION:
        raise ValueError("Release tag version mismatch")
    target = subprocess.check_output(["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
                                     cwd=ROOT).decode().strip()
    if target != commit:
        raise ValueError("Release tag source commit mismatch")


def record_installer(path: Path, directory: Path) -> dict:
    receipt = verify_receipt(directory)
    provenance = receipt["runtime"]
    pe = read_pe_identity(path)
    validate_pe(pe, provenance, installer=True)
    generated = (ROOT / "build/generated/version.iss").read_text(encoding="utf8")
    for value in (provenance["version"], provenance["license_expression"], provenance["homepage"], provenance["commit"]):
        if f'"{value}"' not in generated:
            raise ValueError("Installer compiler metadata mismatch")
    result = dict(commit=provenance["commit"], sha256=sha256(path), pe=pe,
                  payload_receipt_sha256=sha256(directory / "build-verification.json"))
    (ROOT / "build/generated" / (path.name + ".json")).write_text(json.dumps(result, indent=2), encoding="utf8")
    return result


def verify_artifacts(release: Path, directory: Path, *, portable_only: bool = False) -> dict:
    receipt = verify_receipt(directory)
    names = planned_files(app_info.VERSION)
    expected = dict(receipt["files"])
    expected["build-verification.json"] = sha256(directory / "build-verification.json")
    with zipfile.ZipFile(release / names[0]) as archive:
        if len(archive.namelist()) != len(expected) or set(archive.namelist()) != {"Seekdex/" + name for name in expected}:
            raise ValueError("Portable payload file list mismatch")
        for name, digest in expected.items():
            checksum = hashlib.sha256()
            with archive.open("Seekdex/" + name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
            if checksum.hexdigest() != digest:
                raise ValueError(f"Portable payload hash mismatch: {name}")
    if not portable_only:
        path = release / names[1]
        installer = json.loads((ROOT / "build/generated" / (path.name + ".json")).read_text(encoding="utf8"))
        validate_pe(read_pe_identity(path), receipt["runtime"], installer=True)
        if (installer["commit"] != receipt["runtime"]["commit"] or installer["sha256"] != sha256(path)
                or installer["payload_receipt_sha256"] != expected["build-verification.json"]):
            raise ValueError("Installer hash/source/payload mismatch")
    return dict(**identity(), commit=receipt["runtime"]["commit"],
                source_sha256=receipt["runtime"]["source_sha256"],
                planned_tag="v" + app_info.VERSION, files=names[:1] if portable_only else names,
                installer_payload_evidence="validated compiler inputs and output PE/hash; installation not tested")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--verify-exe", action="store_true")
    parser.add_argument("--record-installer", type=Path)
    parser.add_argument("--portable-only", action="store_true")
    parser.add_argument("--tag", help="Verify an existing local tag; never create or change it")
    args = parser.parse_args()
    directory = ROOT / "dist/Seekdex"
    if args.preflight:
        result = dict(commit=preflight(), **identity())
    elif args.verify_exe:
        result = verify_exe(directory)
    elif args.record_installer:
        result = record_installer(args.record_installer, directory)
    else:
        result = verify_artifacts(ROOT / "release", directory, portable_only=args.portable_only)
        if args.tag:
            verify_tag(args.tag, result["commit"])
            result["verified_tag"] = args.tag
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

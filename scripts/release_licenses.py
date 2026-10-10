"""Stage and verify GPL notices and exact application source in distributions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from seekdex.app_info import LICENSE as LICENSE_EXPRESSION, VERSION
from seekdex.release_identity import validate_identity, validate_pe, read_pe_identity

NOTICES = ("LICENSE", "README.md", "COPYRIGHT.md", "THIRD_PARTY_LICENSES.md", "THIRD_PARTY_NOTICES.md")


def verify_source(data: bytes, provenance: dict) -> int:
    from io import BytesIO
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    validate_identity(provenance, commit=head)
    if provenance.get("commit") != head or provenance.get("license_expression") != "GPL-3.0-only":
        raise ValueError("Source commit/license does not match this checkout")
    if hashlib.sha256(data).hexdigest() != provenance.get("source_sha256"):
        raise ValueError("Application source archive hash mismatch")
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-z", "HEAD"], cwd=ROOT)
    entries = {}
    for item in tree.rstrip(b"\0").split(b"\0"):
        attributes, name = item.split(b"\t", 1)
        mode, kind, oid = attributes.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unexpected source tree entry")
        entries[name.decode("utf8")] = oid
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if set(archive.namelist()) != set(entries) or archive.testzip() is not None:
            raise ValueError("Source archive is not the complete tracked source")
        for name, oid in entries.items():
            content = archive.read(name)
            actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if actual != oid:
                raise ValueError(f"Source file differs from committed source: {name}")
        if archive.read("LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("Archived GPL text mismatch")
    return len(entries)


def verify_directory(directory: Path) -> dict:
    directory = directory.resolve()
    resources = directory / "_internal/seekdex/resources"
    if LICENSE_EXPRESSION != "GPL-3.0-only":
        raise ValueError("Project metadata does not use GPL-3.0-only")
    official = (ROOT / "LICENSE").read_bytes()
    if len(official) < 30000 or b"END OF TERMS AND CONDITIONS" not in official:
        raise ValueError("Incomplete GNU GPLv3 text")
    for name in NOTICES:
        expected = (ROOT / name).read_bytes()
        if (directory / name).read_bytes() != expected or (resources / name).read_bytes() != expected:
            raise ValueError(f"Distribution notice mismatch: {name}")
    source = (directory / "application-source.zip").read_bytes()
    if source != (resources / "application-source.zip").read_bytes():
        raise ValueError("Runtime/application source copies differ")
    provenance = json.loads((directory / "source-provenance.json").read_text(encoding="utf8"))
    if provenance != json.loads((resources / "source-provenance.json").read_text(encoding="utf8")):
        raise ValueError("Source provenance copies differ")
    count = verify_source(source, provenance)
    validate_pe(read_pe_identity(directory / "Seekdex.exe"), provenance)
    licenses = resources / "licenses"
    for name in ("dependencies.json", "RapidOCR-LICENSE.txt", "Chinese-CLIP-MIT.txt", "Inno-Setup-LICENSE.txt"):
        if not (licenses / name).is_file():
            raise ValueError(f"Missing upstream notice: {name}")
    return dict(version=VERSION,license_expression=LICENSE_EXPRESSION,commit=provenance["commit"],
                gpl_sha256=hashlib.sha256(official).hexdigest(),source_files=count,
                source_sha256=provenance["source_sha256"])


def stage(directory: Path) -> dict:
    if not directory.resolve().is_relative_to((ROOT / "dist").resolve()):
        raise ValueError("Notice staging must stay in this checkout's dist")
    resources = directory / "_internal/seekdex/resources"
    for name in (*NOTICES, "application-source.zip", "source-provenance.json"):
        shutil.copyfile(resources / name, directory / name)
    return verify_directory(directory)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--directory", type=Path, default=ROOT / "dist/Seekdex")
    args = parser.parse_args()
    result = stage(args.directory) if args.stage else verify_directory(args.directory)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

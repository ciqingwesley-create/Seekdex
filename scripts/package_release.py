from pathlib import Path
import argparse
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version",required=True)
    parser.add_argument("--hash-only",action="store_true")
    parser.add_argument("--edition", choices=("standard", "preinstalled", "all"), default="all")
    args = parser.parse_args()
    release = ROOT/"release"
    release.mkdir(exist_ok=True)
    if not args.hash_only:
        directory = ROOT/"dist"/"Seekdex"
        editions = ("standard", "preinstalled") if args.edition == "all" else (args.edition,)
        for edition in editions:
            suffix = "-Preinstalled" if edition == "preinstalled" else ""
            target = release/f"Seekdex-{args.version}-Windows-x64-Portable{suffix}.zip"
            temporary = target.with_suffix(".zip.tmp")
            try:
                with zipfile.ZipFile(temporary,"w",zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
                    for path in sorted(directory.rglob("*")):
                        if path.is_file():archive.write(path,path.relative_to(directory.parent))
                    if edition == "preinstalled":
                        payload = ROOT / "build" / "preinstalled-models"
                        from seekdex.bundled_models import read_manifest
                        read_manifest(payload)
                        for path in sorted(payload.rglob("*")):
                            if path.is_file():
                                archive.write(path,Path("Seekdex/preinstalled-models") / path.relative_to(payload))
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    entries = []
    for path in sorted(release.glob(f"Seekdex-{args.version}-Windows-x64-*")):
        if path.suffix not in {".zip",".exe"}:continue
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""):
                checksum.update(block)
        digest = checksum.hexdigest()
        entries.append(dict(name=path.name,bytes=path.stat().st_size,sha256=digest,
                            edition="preinstalled" if "Preinstalled" in path.name else "standard"))
    (release/"SHA256SUMS.txt").write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in entries),encoding="ascii")
    (release/"release-manifest.json").write_text(json.dumps(dict(version=args.version,files=entries),indent=2),encoding="utf8")
    notes = (ROOT/"CHANGELOG.md").read_text(encoding="utf8")
    (release/"RELEASE-NOTES.md").write_text(f"# Seekdex v{args.version}\n\n"+notes,encoding="utf8")

if __name__=="__main__":main()

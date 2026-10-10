"""Fetch only hash-pinned corresponding-source inputs, never models or credentials."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def fetch(url: str, destination: Path, sha256: str) -> None:
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == sha256:
        return
    temporary = destination.with_suffix(destination.suffix + '.part')
    try:
        with urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                output.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != sha256:
            raise ValueError('Upstream source checksum mismatch: ' + destination.name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def rawpy_build_inputs(original: Path, destination: Path) -> None:
    """Unmodified code/build inputs; explicitly exclude upstream camera media."""
    with tarfile.open(original) as archive, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as output:
        for member in archive:
            name = member.name.split('/', 1)[-1]
            if member.isfile() and not name.startswith(('test/', 'tests/', 'examples/', 'logo/')):
                path = Path(name)
                if path.suffix.lower() in {'.py', '.pyx', '.pxd', '.h', '.cpp', '.c', '.toml', '.cfg', '.in', '.cmake', '.md', '.rst', '.yml', '.yaml', '.txt', '.ps1', '.json', '.cmd', '.bat', '.sh'} or path.name.startswith(('LICENSE', 'COPYING', 'NOTICE')) or name == '.gitmodules':
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    output.writestr(info, archive.extractfile(member).read())


def main() -> None:
    plan = json.loads((ROOT / 'packaging/compliance/source-plan.json').read_text(encoding='utf-8'))
    destination = ROOT / '.verification/third-party-sources'
    destination.mkdir(parents=True, exist_ok=True)
    for component in plan['components']:
        for entry in component['archives']:
            target = destination / entry['file']
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Unsafe source file path')
            if entry.get('derived_from'):
                original = destination / 'rawpy-source.tar.gz'
                fetch('https://codeload.github.com/letmaik/rawpy/tar.gz/refs/tags/v0.27.1', original,
                      'b0e9af3e44a7bb2e66c6c9b4c8e397d0fa2ba307e0e8dc76d2de7ad76d84cba6')
                rawpy_build_inputs(original, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                    raise ValueError('Derived build-input checksum differs')
            else:
                fetch(entry['url'], target, entry['sha256'])
            print('Verified source: ' + entry['file'], flush=True)


if __name__ == '__main__':
    main()

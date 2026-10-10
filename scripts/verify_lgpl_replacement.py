"""Run the real frozen GUI in a separate directory/profile with alternate DLLs.

No installed application or OS policy is changed. A successful directory test
does not certify an upstream source match or a clean Windows installation.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from native_compliance import digest


def clone_bundle(source: Path, target: Path) -> None:
    if target.exists():
        raise ValueError('Use a new isolated test directory')
    def link(a, b):
        try: os.link(a, b)
        except OSError: shutil.copyfile(a, b)
    shutil.copytree(source, target, copy_function=link)


def replace_file(bundle: Path, relative: str, replacement: Path) -> dict:
    target = (bundle / relative).resolve()
    if not target.is_relative_to(bundle.resolve()):
        raise ValueError('Replacement escapes isolated application')
    before = digest(target) if target.exists() else None
    after = digest(replacement)
    if before == after:
        raise ValueError('Identical bytes do not establish a modified-library replacement')
    # Break the hard link before writing; never edit the source bundle's inode.
    target.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(replacement, target)
    return {'path': relative, 'before': before, 'after': after}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--replacements', required=True, type=Path,
                        help='JSON mapping of bundle-relative names to local alternate library files')
    parser.add_argument('--model-root', type=Path, help='Existing cached models, never downloaded or distributed')
    args = parser.parse_args()
    args.bundle = args.bundle.resolve()
    args.output = args.output.resolve()
    args.replacements = args.replacements.resolve()
    if args.output.resolve().is_relative_to(args.bundle.resolve()):
        raise ValueError('Verification output must be outside source bundle')
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = args.output / 'application'
    clone_bundle(args.bundle, bundle)
    mapping = json.loads(args.replacements.read_text(encoding='utf8'))
    if not mapping:
        raise ValueError('No alternate libraries supplied')
    changes = [replace_file(bundle, name, Path(path)) for name, path in mapping.items()]
    environment = {k.upper(): v for k, v in os.environ.items()}
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'QT_QPA_PLATFORM'):
        environment.pop(key, None)
    environment['PATH'] = environment['SYSTEMROOT'] + '/System32;' + environment['SYSTEMROOT']
    environment['SEEKDEX_HOME'] = str(args.output / 'profile')
    if args.model_root:
        environment['SEEKDEX_BUILD_MODEL_ROOT'] = str(args.model_root)
    mode = '--verify-full' if args.model_root else '--verify-runtime'
    command = [str(bundle / 'Seekdex.exe'), mode, str(args.output)]
    if args.model_root:
        command.extend(['--model-root', str(args.model_root)])
    completed = subprocess.run(command,
                               env=environment, cwd=bundle, timeout=1200, check=False)
    report_path = args.output / 'report.json'
    gui = json.loads(report_path.read_text(encoding='utf8')) if report_path.exists() else {'error': 'No GUI report'}
    result = {'status': 'PASS' if completed.returncode == 0 and not gui.get('error') else 'FAIL',
              'scope': 'separate directory/profile on host, not clean VM/Sandbox',
              'changes': changes, 'gui': gui, 'exit_code': completed.returncode}
    (args.output / 'replacement-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'status': result['status'], 'error': gui.get('error'), 'checks': len(gui.get('checks', []))}, indent=2))
    if result['status'] != 'PASS':
        sys.exit(1)


if __name__ == '__main__':
    main()

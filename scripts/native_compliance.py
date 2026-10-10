"""Inspect actual PE payloads and fail closed on unverified distribution obligations.

This records technical evidence; a PASS inventory is not a legal opinion.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_environment(policy: dict) -> None:
    for name, expected in policy.get('wheel_versions', {}).items():
        if metadata.version(name) != expected:
            raise ValueError('Audited native wheel version changed: ' + name)


def wheel_owners() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for dist in metadata.distributions():
        for record in dist.files or ():
            if Path(str(record)).suffix.lower() not in {'.dll', '.pyd', '.exe'}:
                continue
            path = Path(dist.locate_file(record))
            if not path.is_file():
                continue
            owner = {'distribution': dist.metadata['Name'], 'version': dist.version,
                     'wheel_path': str(record).replace('\\', '/'),
                     'declared_license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                     'project_urls': dist.metadata.get_all('Project-URL', [])}
            result.setdefault(digest(path), []).append(owner)
    return result


def pe_details(path: Path) -> dict:
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY[n] for n in (
        'IMAGE_DIRECTORY_ENTRY_IMPORT', 'IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT', 'IMAGE_DIRECTORY_ENTRY_RESOURCE')])
    strings = {}
    for group in getattr(pe, 'FileInfo', ()):
        for entry in group:
            for table in getattr(entry, 'StringTable', ()):
                strings.update({k.decode(errors='replace'): v.decode(errors='replace').rstrip('\0')
                                for k, v in table.entries.items()})
    imports = {entry.dll.decode(errors='replace').lower()
               for attribute in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT')
               for entry in getattr(pe, attribute, ())}
    pe.close()
    return {'pe_version': strings.get('FileVersion'), 'product_version': strings.get('ProductVersion'),
            'copyright': strings.get('LegalCopyright'), 'imports': sorted(imports)}


def scan(directory: Path, owners: dict | None = None, policy: dict | None = None) -> dict:
    directory = directory.resolve()
    owners = wheel_owners() if owners is None else owners
    policy = {} if policy is None else policy
    files, native = [], []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink is not allowed in audited payload')
        if not path.is_file():
            continue
        name = path.relative_to(directory).as_posix()
        sha = digest(path)
        files.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha})
        with path.open('rb') as stream:
            magic = stream.read(2)
        if magic != b'MZ':
            continue
        row = {'path': name, 'sha256': sha, 'bytes': path.stat().st_size,
               'owners': owners.get(sha, []), **pe_details(path)}
        matched = next((r for r in policy.get('rules', []) if any(
            fnmatch.fnmatchcase(name.lower(), pattern.lower()) for pattern in r['patterns'])), None)
        row['component'] = matched['component'] if matched else 'unreviewed'
        row['selected_license'] = matched.get('license') if matched else None
        row['source'] = matched.get('source') if matched else None
        row['review_status'] = matched.get('status', 'BLOCKED') if matched else 'BLOCKED'
        row['evidence'] = matched.get('evidence', []) if matched else []
        row['embedded_component_candidates'] = matched.get('embedded_component_candidates', []) if matched else []
        row['native_version'] = matched.get('native_version') if matched else None
        row['version_status'] = 'PASS' if row['pe_version'] or row['native_version'] else 'BLOCKED'
        native.append(row)
    shipped = {Path(row['path']).name.lower() for row in native}
    external = sorted({name for row in native for name in row['imports'] if name not in shipped})
    system = set(policy.get('windows_system_imports', []))
    unresolved = [name for name in external if name not in system and not name.startswith(('api-ms-win-', 'ext-ms-win-'))]
    return {'schema_version': 1, 'files': files, 'native': native, 'native_count': len(native),
            'external_imports': external, 'unresolved_imports': unresolved,
            'status': 'BLOCKED' if unresolved or any(r['review_status'] != 'PASS' for r in native) else 'PASS'}


def verify_inventory(directory: Path, report: dict) -> None:
    """Bind the report to every shipped native file; do not trust an old report."""
    actual = {}
    for path in directory.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                is_pe = stream.read(2) == b'MZ'
            if is_pe:
                actual[path.relative_to(directory).as_posix()] = digest(path)
    expected = {row['path']: row['sha256'] for row in report['native']}
    if actual != expected:
        raise ValueError('Native payload differs from audited inventory')


def compare_payloads(first: dict, second: dict) -> None:
    a = {f['path']: f['sha256'] for f in first['files']}
    b = {f['path']: f['sha256'] for f in second['files']}
    if a != b:
        raise ValueError('Portable and independently extracted Setup payloads differ')


def validate_sources(manifest: dict, root: Path) -> list[dict]:
    results = []
    for component in manifest.get('components', []):
        errors = []
        for archive in component.get('archives', []):
            path = (root / archive['file']).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('Source archive path escapes source directory')
            if not path.is_file() or digest(path) != archive['sha256']:
                errors.append('missing or changed archive: ' + archive['file'])
        # Downloaded sources and generic upstream URLs alone do not establish
        # exact binary provenance, patched build inputs or distribution access.
        required = ('binary_source_mapping_verified', 'build_information_verified',
                    'source_delivery_verified', 'replacement_verified')
        for field in required:
            if not component.get(field):
                errors.append(field)
        if not component.get('archives'):
            errors.append('no corresponding source archives')
        results.append({'component': component['component'], 'status': 'BLOCKED' if errors else 'PASS',
                        'unresolved': errors})
    return results


def require_public_ready(report: dict, source_results: list[dict]) -> None:
    if report['status'] != 'PASS' or not source_results or any(r['status'] != 'PASS' for r in source_results):
        raise ValueError('Public binary distribution is BLOCKED by unverified native obligations')


def write_csv(report: dict, path: Path) -> None:
    """Human-readable per-file evidence; keep nested provenance in the JSON."""
    fields = ('path', 'component', 'version', 'version_evidence', 'sha256', 'bytes',
              'selected_license', 'review_status', 'source', 'imports')
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report['native']:
            versions = '; '.join(f"{o['distribution']} {o['version']}" for o in row['owners'])
            writer.writerow({**{k: row.get(k) for k in fields if k in row},
                'version': row.get('pe_version') or row.get('native_version') or versions or 'UNVERIFIED',
                'version_evidence': 'PE' if row.get('pe_version') else (
                    'source/runtime' if row.get('native_version') else 'wheel package; native version may differ'),
                'source': json.dumps(row.get('source'), ensure_ascii=False),
                'imports': '; '.join(row['imports'])})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--compare-directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policy', type=Path, default=ROOT / 'packaging/compliance/native-policy.json')
    parser.add_argument('--sources', type=Path, default=ROOT / 'packaging/compliance/source-plan.json')
    parser.add_argument('--source-root', type=Path, default=ROOT / '.verification/third-party-sources')
    parser.add_argument('--public-ready', action='store_true')
    parser.add_argument('--installer-file', type=Path, help='Also record the outer Setup PE, without running it')
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding='utf-8')) if args.policy.exists() else {}
    verify_environment(policy)
    owners = wheel_owners()
    report = scan(args.directory, owners, policy)
    if args.compare_directory:
        other = scan(args.compare_directory, owners, policy)
        compare_payloads(report, other)
        report['setup_payload_comparison'] = 'PASS'
    if args.sources.exists():
        report['source_checks'] = validate_sources(json.loads(args.sources.read_text(encoding='utf-8')), args.source_root)
    if args.installer_file:
        report['setup_wrapper'] = {'filename': args.installer_file.name,
            'bytes': args.installer_file.stat().st_size, 'sha256': digest(args.installer_file),
            **pe_details(args.installer_file), 'tool': 'Inno Setup',
            'license_evidence': 'Original Inno license retained in licenses; wrapper dependencies require review',
            'status': 'BLOCKED'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv(report, args.output.with_suffix('.csv'))
    print(json.dumps({'native_count': report['native_count'], 'status': report['status'],
                      'unresolved_imports': report['unresolved_imports']}, indent=2))
    if args.public_ready:
        require_public_ready(report, report.get('source_checks', []))


if __name__ == '__main__':
    main()

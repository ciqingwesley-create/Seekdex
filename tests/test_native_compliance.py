"""Synthetic regression tests for fail-closed binary/source evidence."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_compliance as compliance


def test_inventory_discovers_pe_regardless_of_extension(tmp_path, monkeypatch):
    (tmp_path / 'hidden.data').write_bytes(b'MZsynthetic')
    (tmp_path / 'notice.txt').write_text('notice')
    monkeypatch.setattr(compliance, 'pe_details', lambda _: {
        'imports': ['kernel32.dll'], 'pe_version': None})
    report = compliance.scan(tmp_path, {}, {'windows_system_imports': ['kernel32.dll']})
    assert report['native_count'] == 1 and len(report['files']) == 2
    assert report['native'][0]['path'] == 'hidden.data'
    assert report['status'] == 'BLOCKED'


def test_unknown_transitive_import_blocks(tmp_path, monkeypatch):
    (tmp_path / 'plugin.dll').write_bytes(b'MZfake')
    monkeypatch.setattr(compliance, 'pe_details', lambda _: {
        'imports': ['unknown.dll', 'api-ms-win-crt-runtime-l1-1-0.dll'], 'pe_version': '1'})
    report = compliance.scan(tmp_path, {})
    assert report['unresolved_imports'] == ['unknown.dll']


@pytest.mark.parametrize('change', ['modify', 'add', 'delete'])
def test_native_inventory_rejects_payload_changes(tmp_path, change):
    file = tmp_path / 'library.dll'
    file.write_bytes(b'MZfirst')
    report = {'native': [{'path': file.name, 'sha256': compliance.digest(file)}]}
    compliance.verify_inventory(tmp_path, report)
    if change == 'modify': file.write_bytes(b'MZsecond')
    elif change == 'add': (tmp_path / 'other.pyd').write_bytes(b'MZnew')
    else: file.unlink()
    with pytest.raises(ValueError, match='inventory'):
        compliance.verify_inventory(tmp_path, report)


def test_setup_portable_payload_mismatch_rejected():
    first = {'files': [{'path': 'file', 'sha256': 'a'}]}
    compliance.compare_payloads(first, first)
    with pytest.raises(ValueError, match='differ'):
        compliance.compare_payloads(first, {'files': [{'path': 'file', 'sha256': 'b'}]})


def test_sources_without_delivery_or_build_evidence_block(tmp_path):
    file = tmp_path / 'source.tar.xz'
    file.write_bytes(b'not an actual archive')
    plan = {'components': [{'component': 'example', 'archives': [
        {'file': file.name, 'sha256': compliance.digest(file)}]}]}
    results = compliance.validate_sources(plan, tmp_path)
    assert results[0]['status'] == 'BLOCKED'
    assert 'source_delivery_verified' in results[0]['unresolved']
    with pytest.raises(ValueError, match='BLOCKED'):
        compliance.require_public_ready({'status': 'PASS'}, results)


def test_source_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match='escapes'):
        compliance.validate_sources({'components': [{'component': 'x', 'archives': [
            {'file': '../outside', 'sha256': 'a'}]}]}, tmp_path)


def test_changed_source_archive_blocks(tmp_path):
    file = tmp_path / 'source.tar.xz'
    file.write_bytes(b'first')
    plan = {'components': [{'component': 'x', 'archives': [{'file': file.name, 'sha256': compliance.digest(file)}],
                           **dict.fromkeys(('binary_source_mapping_verified', 'build_information_verified',
                                            'source_delivery_verified', 'replacement_verified'), True)}]}
    assert compliance.validate_sources(plan, tmp_path)[0]['status'] == 'PASS'
    file.write_bytes(b'second')
    assert compliance.validate_sources(plan, tmp_path)[0]['status'] == 'BLOCKED'


def test_inventory_or_missing_sources_cannot_clear_public_gate():
    with pytest.raises(ValueError):
        compliance.require_public_ready({'status': 'BLOCKED'}, [{'status': 'PASS'}])
    with pytest.raises(ValueError):
        compliance.require_public_ready({'status': 'PASS'}, [])


def test_csv_preserves_filename_hash_and_version_evidence(tmp_path):
    import csv
    report = {'native': [{'path': 'library.pyd', 'sha256': 'a' * 64, 'bytes': 42,
        'owners': [{'distribution': 'wrapper', 'version': '2.0'}], 'imports': ['kernel32.dll'],
        'component': 'library', 'selected_license': 'MIT', 'review_status': 'BLOCKED',
        'source': ['immutable source reference']}]}
    path = tmp_path / 'inventory.csv'
    compliance.write_csv(report, path)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['sha256'] == 'a' * 64 and row['version'] == 'wrapper 2.0'
    assert 'native version may differ' in row['version_evidence']


def test_ocr_geometry_dependency_is_not_excluded():
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'packaging/Seekdex.spec').read_text(encoding='utf-8')
    assert "'shapely'" not in spec
    assert 'opencv_videoio_ffmpeg' in spec


def test_changed_native_wheel_invalidates_source_mapping(monkeypatch):
    monkeypatch.setattr(compliance.metadata, 'version', lambda _: '2.0')
    compliance.verify_environment({'wheel_versions': {'component': '2.0'}})
    with pytest.raises(ValueError, match='wheel version changed'):
        compliance.verify_environment({'wheel_versions': {'component': '1.0'}})


def test_replacement_does_not_modify_hard_linked_original(tmp_path):
    from verify_lgpl_replacement import clone_bundle, replace_file
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'library.dll').write_bytes(b'original')
    target = tmp_path / 'isolated'
    clone_bundle(original, target)
    replacement = tmp_path / 'alternate.dll'
    replacement.write_bytes(b'changed')
    replace_file(target, 'library.dll', replacement)
    assert (original / 'library.dll').read_bytes() == b'original'
    assert (target / 'library.dll').read_bytes() == b'changed'
    with pytest.raises(ValueError, match='Identical'):
        replace_file(target, 'library.dll', replacement)
    with pytest.raises(ValueError, match='escapes'):
        replace_file(target, '../original/library.dll', replacement)

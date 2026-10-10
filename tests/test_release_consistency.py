"""Release regressions: stale binaries/resources must never pass the gate."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from seekdex import app_info
from seekdex.release_identity import identity, installer_description, validate_identity, validate_pe

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_checks_test", ROOT / "scripts/release_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.fixture
def provenance():
    commit = "a" * 40
    return dict(**identity(), commit=commit, source_url=app_info.HOMEPAGE + "/tree/" + commit)


def test_matching_release_identity(provenance):
    validate_identity(provenance, commit="a" * 40)


@pytest.mark.parametrize("field,value", [
    ("version", "0.5.0-rc1"), ("package_version", "0.5.0rc1"),
    ("windows_version", [0, 5, 0, 1]), ("license_expression", "MIT"),
    ("homepage", "https://github.com/example/old-project"),
    ("source_url", "https://github.com/example/old-project/tree/" + "a" * 40),
    ("commit", "not-a-commit"),
])
def test_stale_release_identity_is_rejected(provenance, field, value):
    provenance[field] = value
    with pytest.raises(ValueError):
        validate_identity(provenance)


def test_wrong_commit_is_rejected(provenance):
    with pytest.raises(ValueError, match="commit"):
        validate_identity(provenance, commit="b" * 40)


def pe(provenance, *, installer=False):
    return dict(windows_version=provenance["windows_version"],
                product_windows_version=provenance["windows_version"],
                FileVersion=app_info.VERSION, ProductVersion=app_info.VERSION,
                FileDescription=installer_description(provenance) if installer else "Seekdex",
                SourceCommit=provenance["commit"], Homepage=app_info.HOMEPAGE,
                ProductName=f"Seekdex; source {provenance['commit']}",
                LegalCopyright=f"{app_info.COPYRIGHT}; {provenance['homepage']}",
                Comments=f"Seekdex source license: {app_info.LICENSE}; third-party licenses retained.")


def test_matching_exe_and_installer_resources(provenance):
    validate_pe(pe(provenance), provenance)
    validate_pe(pe(provenance, installer=True), provenance, installer=True)


@pytest.mark.parametrize("field,value", [
    ("FileVersion", "0.5.0-rc1"), ("ProductVersion", "0.5.0-rc1"),
    ("windows_version", [0, 5, 0, 1]), ("product_windows_version", [0, 5, 0, 1]),
    ("Comments", "MIT"), ("Homepage", "https://example.invalid"), ("SourceCommit", "b" * 40),
])
def test_stale_exe_is_rejected(provenance, field, value):
    resource = pe(provenance)
    resource[field] = value
    with pytest.raises(ValueError):
        validate_pe(resource, provenance)


def test_stale_installer_identity_is_rejected(provenance):
    resource = pe(provenance, installer=True)
    resource["FileDescription"] = resource["FileDescription"].replace("GPL-3.0-only", "MIT")
    with pytest.raises(ValueError, match="Installer"):
        validate_pe(resource, provenance, installer=True)


@pytest.mark.parametrize("field", ["ProductName", "LegalCopyright"])
def test_truncated_installer_commit_or_homepage_rejected(provenance, field):
    resource = pe(provenance, installer=True)
    resource[field] = resource[field][:-1]
    with pytest.raises(ValueError, match="Installer"):
        validate_pe(resource, provenance, installer=True)


def test_installer_metadata_fits_inno_fixed_capacity(provenance):
    resource = pe(provenance, installer=True)
    for key, limit in (("FileDescription", 60), ("ProductName", 60), ("LegalCopyright", 100)):
        assert len(resource[key]) <= limit


def test_manifest_only_planned_standard_pair():
    assert checks.planned_files(app_info.VERSION) == [
        f"Seekdex-{app_info.VERSION}-Windows-x64-Portable.zip",
        f"Seekdex-{app_info.VERSION}-Windows-x64-Setup.exe"]
    with pytest.raises(ValueError, match="version"):
        checks.planned_files("0.5.0-rc1")


@pytest.mark.parametrize("name", ["models/weights.bin", "preinstalled-models/manifest.json",
                                 "_internal/rapidocr/model.onnx", "model.safetensors"])
def test_standard_packages_reject_weights(name):
    with pytest.raises(ValueError, match="forbidden"):
        checks.standard_payload([name])


def test_standard_libraries_are_allowed():
    checks.standard_payload(["Seekdex.exe", "_internal/torch/lib/torch_cpu.dll", "LICENSE",
                             "_internal/onnxruntime/transformers/models/bart/__init__.py"])


def test_binary_model_weight_in_library_tree_is_rejected():
    with pytest.raises(ValueError, match="forbidden"):
        checks.standard_payload(["_internal/rapidocr/models/pytorch_model.bin"])


def test_dirty_build_rejected(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: b" M README.md\n")
    with pytest.raises(ValueError, match="clean"):
        checks.preflight()


def test_payload_modified_after_verification_rejected(tmp_path, monkeypatch, provenance):
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(checks, "preflight", lambda: "a" * 40)
    monkeypatch.setitem(sys.modules, "release_licenses", SimpleNamespace(verify_directory=lambda _: None))
    (tmp_path / "Seekdex.exe").write_bytes(b"verified executable")
    (tmp_path / 'native-inventory.json').write_text(json.dumps({'native': []}))
    files = checks.payload_hashes(tmp_path)
    runtime = dict(provenance, exe_sha256=files["Seekdex.exe"])
    (tmp_path / "build-verification.json").write_text(json.dumps(dict(runtime=runtime, files=files)))
    checks.verify_receipt(tmp_path)
    (tmp_path / "Seekdex.exe").write_bytes(b"stale executable")
    with pytest.raises(ValueError, match="changed"):
        checks.verify_receipt(tmp_path)


def test_missing_package_receipt_fails(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(checks, "preflight", lambda: "a" * 40)
    monkeypatch.setitem(sys.modules, "release_licenses", SimpleNamespace(verify_directory=lambda _: None))
    with pytest.raises(FileNotFoundError):
        checks.verify_receipt(tmp_path)


def test_matching_existing_tag(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: ("a" * 40).encode())
    checks.verify_tag("v" + app_info.VERSION, "a" * 40)


@pytest.mark.parametrize("tag,commit", [("v0.5.0-rc1", "a" * 40), ("v0.5.0-rc2", "b" * 40)])
def test_wrong_tag_version_or_commit_rejected(monkeypatch, tag, commit):
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: ("a" * 40).encode())
    with pytest.raises(ValueError, match="tag"):
        checks.verify_tag(tag, commit)

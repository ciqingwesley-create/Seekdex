"""Brand changes must preserve persisted identities rather than reindex users."""
from pathlib import Path
import json
import sqlite3

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QSettings

from seekdex.brand_migration import migrate_brand, migrate_qsettings, MigrationConflict
from seekdex.paths import legacy_profiles, get_storage_root, get_database_path
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.search import SearchOptions
from seekdex.ai.store import EmbeddingStore
from seekdex.ocr.store import OCRStore
from seekdex.ocr.interfaces import OCRResult
from seekdex.settings import AppSettings


@pytest.fixture
def old_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEKDEX_HOME", str(tmp_path / "Seekdex"))
    old = tmp_path / "LocalImageSearch"
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (30, 20), "blue").save(photos / "a.jpg")
    with FileIndex(old / "data/database.sqlite3") as db:
        Indexer(db).refresh(photos)
        record = next(db.query(SearchOptions(photos)))
        EmbeddingStore(db, "test-model", 3).save(record, np.array([1, 0, 0]))
        OCRStore(db).save(record, OCRResult("Seekdex sample text"))
        db.commit()
    config = AppSettings(old / "config/settings.ini")
    config.set("search/folder", str(photos))
    config.set("ai/batch", 8)
    config.set("ai/model_dir", str(old / "models"))
    config.set("ocr/model_dir", str(old / "models/ocr"))
    config.set("window/geometry", b"window")
    config.save()
    for relative in ("models/test/weight.bin", "models/ocr/weight.onnx",
                     "models/test/tuning.json", "models/test/compiled/model.xml",
                     "cache/thumbnails/preview.jpg", "logs/application.log"):
        path = old / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    return old, tmp_path / "Seekdex", photos, record


def test_brand_migration_preserves_database_embedding_ocr(old_profile):
    old, new, photos, prior = old_profile
    migrate_brand(new, old)
    with FileIndex(new / "data/database.sqlite3") as db:
        record = next(db.query(SearchOptions(photos)))
        assert (record.id, record.file_uid) == (prior.id, prior.file_uid)
        assert EmbeddingStore(db, "test-model", 3).get(record) is not None
        assert OCRStore(db).get(record)["ocr_text"] == "Seekdex sample text"
        assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (old / "data/database.sqlite3").exists()


def test_model_caches_and_settings_preserved(old_profile):
    old, new, photos, _ = old_profile
    migrate_brand(new, old)
    for relative in ("models/test/weight.bin", "models/ocr/weight.onnx",
                     "models/test/tuning.json", "models/test/compiled/model.xml",
                     "cache/thumbnails/preview.jpg"):
        assert (new / relative).read_bytes() == (old / relative).read_bytes()
    config = AppSettings(new / "config/settings.ini")
    assert config.get("search/folder") == str(photos)
    assert config.get("ai/batch") == 8
    assert config.get("window/geometry") == b"window"
    assert config.get("ai/model_dir") == str(new / "models")
    assert config.get("ocr/model_dir") == str(new / "models/ocr")
    assert (new / "logs/legacy/application.log").exists()


def test_qsettings_namespace_migration(tmp_path):
    source = QSettings(str(tmp_path / "old.ini"), QSettings.IniFormat)
    source.setValue("search/folder", "D:/Photos")
    source.setValue("ai/batch", 8)
    source.sync()
    new = tmp_path / "new"
    migrate_qsettings(new, [source])
    config = AppSettings(new / "config/settings.ini")
    assert config.get("search/folder") == "D:/Photos"
    assert config.get("ai/batch") == 8
    config.set("ai/batch", 4)
    config.save()
    migrate_qsettings(new, [source])
    assert AppSettings(new / "config/settings.ini").get("ai/batch") == 4


def test_migration_interrupted_resumes_without_loss(old_profile):
    old, new, photos, record = old_profile
    def stop(message):
        if "缓存" in message:
            raise InterruptedError("cancel")
    with pytest.raises(InterruptedError):
        migrate_brand(new, old, progress=stop)
    assert (new / "data/database.sqlite3").exists()
    assert not (new / "config/brand-migration-v1.done").exists()
    migrate_brand(new, old)
    assert (new / "config/brand-migration-v1.done").exists()


def test_both_databases_never_overwritten(old_profile):
    old, new, _, _ = old_profile
    target = new / "data/database.sqlite3"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE keep(value)")
    before = target.read_bytes()
    with pytest.raises(MigrationConflict):
        migrate_brand(new, old)
    assert target.read_bytes() == before
    assert not (new / "config/brand-migration-v1.done").exists()


def test_existing_config_wins_and_external_cache_stays(old_profile, tmp_path):
    old, new, _, _ = old_profile
    config = AppSettings(new / "config/settings.ini")
    config.set("ai/batch", 16)
    config.save()
    original = AppSettings(old / "config/settings.ini")
    original.set("thumbnail/directory", str(tmp_path / "external"))
    original.save()
    migrate_brand(new, old)
    result = AppSettings(new / "config/settings.ini")
    assert result.get("ai/batch") == 16
    assert result.get("thumbnail/directory") == str(tmp_path / "external")


def test_brand_migration_idempotent_does_not_restore_deleted_cache(old_profile):
    old, new, _, _ = old_profile
    migrate_brand(new, old)
    path = new / "models/test/weight.bin"
    path.unlink()
    assert migrate_brand(new, old) == []
    assert not path.exists()


def test_different_model_cache_stops_safely(old_profile):
    old, new, _, _ = old_profile
    weight = new / "models/test/weight.bin"
    weight.parent.mkdir(parents=True)
    weight.write_bytes(b"new")
    with pytest.raises(MigrationConflict):
        migrate_brand(new, old)
    assert weight.read_bytes() == b"new"
    assert (old / "models/test/weight.bin").read_bytes() == b"fixture"


def test_old_directories_and_old_home_override(tmp_path, monkeypatch):
    paths = legacy_profiles(platform="win32", environ={"LOCALAPPDATA": str(tmp_path)}, home=tmp_path)
    assert paths == (tmp_path / "LocalImageSearch", tmp_path / "local-image-search")
    monkeypatch.delenv("SEEKDEX_HOME", raising=False)
    monkeypatch.setenv("LOCAL_IMAGE_SEARCH_HOME", str(tmp_path / "isolated"))
    assert get_storage_root() == tmp_path / "isolated"
    assert migrate_brand() == []


def test_explicit_legacy_source_is_discovered(old_profile, monkeypatch):
    old, new, _, _ = old_profile
    monkeypatch.setenv("SEEKDEX_LEGACY_HOME", str(old))
    migrate_brand()
    assert get_database_path().exists()


def test_installer_upgrade_identity_preserved():
    source = (Path(__file__).parents[1] / "packaging/installer.iss").read_text(encoding="utf8")
    assert "AppId={{B2853172-9740-4A21-B7A7-BF42BC3370F8}" in source
    assert "UsePreviousAppDir=yes" in source
    assert "UsePreviousGroup=no" in source
    assert 'Name: "{app}\\LocalImageSearch.exe"' in source
    assert 'Filename: "{app}\\Seekdex.exe"' in source
    assert "VersionInfoProductVersion={#WindowsVersion}" in source
    assert "VersionInfoProductTextVersion={#ProductVersion}" in source


def test_seekdex_package_and_old_imports_removed():
    import seekdex
    from seekdex.app_info import APP_NAME, DISPLAY_NAME, VERSION, PACKAGE_VERSION
    assert APP_NAME == "Seekdex" and DISPLAY_NAME == "Seekdex · 索星仪"
    assert VERSION == "0.5.0-rc1" and PACKAGE_VERSION == "0.5.0rc1"
    root = Path(__file__).parents[1]
    assert not (root / "src" / "local_image_search").exists()
    for path in (root / "src/seekdex").rglob("*.py"):
        assert "from local_image_search" not in path.read_text(encoding="utf8")
        assert "import local_image_search" not in path.read_text(encoding="utf8")

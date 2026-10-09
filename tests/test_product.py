from pathlib import Path
from dataclasses import replace
import json
import os
import sqlite3
import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from seekdex.app_info import VERSION, APP_NAME
from seekdex.settings import AppSettings, SCHEMA_VERSION
from seekdex.paths import (get_storage_root,get_database_path,get_config_path,
    get_model_cache_dir,get_ocr_model_root,get_thumbnail_cache_dir,unique_roots)
from seekdex.storage import migrate_legacy,copy_missing_tree
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.index.catalog import IndexCatalog
from seekdex.search import SearchOptions,SearchResult
from seekdex.ai.store import EmbeddingStore
from seekdex.ocr.store import OCRStore
from seekdex.ocr.interfaces import OCRResult
from seekdex.worker import SearchThread
from seekdex.cache_management import prune_thumbnails
from seekdex.resources import resource_path
from test_ocr_concurrent_search import wait_until


@pytest.fixture
def profile(tmp_path,monkeypatch):
    monkeypatch.setenv("SEEKDEX_HOME",str(tmp_path/"profile"))
    return tmp_path/"profile"


def test_single_authoritative_version():
    import tomllib
    values = tomllib.loads((Path(__file__).resolve().parents[1]/"pyproject.toml").read_text())
    assert "version" not in values["project"]
    assert values["tool"]["setuptools"]["dynamic"]["version"]["attr"]=="seekdex.app_info.PACKAGE_VERSION"
    assert VERSION and APP_NAME


def test_windows_product_paths_do_not_use_cwd(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"LOCALAPPDATA":str(tmp_path/"local")}
    assert get_storage_root(platform="win32",environ=env,home=tmp_path)==tmp_path/"local"/APP_NAME


def test_profile_paths_and_model_settings(profile):
    assert get_database_path()==profile/"data"/"database.sqlite3"
    assert get_config_path()==profile/"config"/"settings.ini"
    config = AppSettings()
    custom = profile/"custom-models"
    config.set("ai/model_dir",str(custom))
    config.save()
    assert get_model_cache_dir()==custom
    assert get_ocr_model_root()==custom/"ocr"
    assert get_thumbnail_cache_dir()==profile/"cache"/"thumbnails"


def test_invalid_relative_custom_cache_falls_back(profile):
    config = AppSettings()
    config.set("ai/model_dir","../photos")
    config.save()
    assert get_model_cache_dir()==profile/"models"


def test_config_roundtrip_schema_and_first_launch(profile):
    config = AppSettings()
    assert config.first_launch
    assert config.get("config/schema_version")==SCHEMA_VERSION
    config.set("general/remember_filters",True)
    config.set("ai/batch",8)
    config.complete_onboarding()
    reloaded = AppSettings()
    assert reloaded.get("general/remember_filters") and reloaded.get("ai/batch")==8
    assert not reloaded.first_launch


def test_old_config_migration_preserves_scope(profile,tmp_path):
    path = profile/"config"/"settings.ini"
    settings = QSettings(str(path),QSettings.IniFormat)
    settings.setValue("search/folder",str(tmp_path/"old"))
    settings.setValue("search/recursive",False)
    settings.sync()
    config = AppSettings()
    assert config.get("search/folder")==str(tmp_path/"old")
    assert config.settings.value("search/recursive",True,type=bool) is False


@pytest.fixture
def scopes(tmp_path):
    root = tmp_path/"photos"
    sub = root/"sub"
    other = tmp_path/"other"
    sub.mkdir(parents=True)
    other.mkdir()
    for path in (root/"a.jpg",sub/"b.png",other/"c.jpg"):
        Image.new("RGB",(30,20),"blue").save(path)
    (root/"note.txt").write_text("text")
    database = FileIndex(tmp_path/"index.db")
    Indexer(database).refresh(root)
    Indexer(database).refresh(other)
    yield root,sub,other,database
    database.__exit__(None,None,None)


def test_catalog_add_duplicate_and_overlap(scopes):
    root,sub,other,database = scopes
    catalog = IndexCatalog(database)
    for path in (root,root,sub,other):catalog.add(path)
    assert len(catalog.directories())==3
    stats = catalog.statistics()
    assert {r["root_path"]:r["total"] for r in stats}=={str(root):3,str(sub):1,str(other):1}


def test_multi_directory_sql_search_is_unique(scopes):
    root,sub,other,database = scopes
    options = SearchOptions(root,folders=(root,sub,other))
    records = list(database.query(options))
    assert len(records)==4 and len({r.file_uid for r in records})==4
    assert len(list(database.query(replace(options,recursive=False))))==4


def test_multi_directory_progressive_worker(scopes):
    root,sub,other,database = scopes
    task = SearchThread(SearchOptions(root,folders=(root,sub,other)),database_path=database.database_path)
    results,done = [],[]
    task.batch_ready.connect(lambda batch:results.extend(batch))
    task.search_done.connect(lambda *args:done.append(args))
    task.run()
    assert done==[(4,False,"")]
    assert len({item.path for item in results})==4


def test_result_limit_keeps_partial_index(tmp_path):
    root = tmp_path/"photos"
    root.mkdir()
    for i in range(30):(root/f"{i}.txt").write_text("text")
    db_path = tmp_path/"index.db"
    task = SearchThread(SearchOptions(root,max_results=5),database_path=db_path)
    done=[]
    task.search_done.connect(lambda *args:done.append(args))
    task.run()
    assert done==[(5,False,"")]
    with FileIndex(db_path) as database:
        assert database.coverage_state(root,True)=="partial"


def test_remove_index_preserves_disk_and_other_overlap(scopes):
    root,sub,other,database = scopes
    catalog = IndexCatalog(database)
    catalog.add(root)
    catalog.add(sub)
    catalog.add(other)
    assert catalog.remove(root)==2
    assert (root/"a.jpg").exists() and (root/"note.txt").exists()
    assert {r.path for r in database.query(SearchOptions(root,folders=(root,other)))}=={sub/"b.png",other/"c.jpg"}


def test_remove_child_keeps_ancestor_uid_and_embedding(scopes):
    root,sub,other,database = scopes
    catalog = IndexCatalog(database)
    catalog.add(root)
    catalog.add(sub)
    record = next(database.query(SearchOptions(sub)))
    EmbeddingStore(database,"test-model",3).save(record,np.array([1,0,0]))
    database.commit()
    assert catalog.remove(sub)==0
    assert EmbeddingStore(database,"test-model",3).get(record) is not None
    assert (sub/"b.png").exists()


def test_root_normalization_does_not_match_sibling(tmp_path):
    root = tmp_path/"Photo"
    assert unique_roots([root,root/"2026",tmp_path/"Photos2",root])==(root,tmp_path/"Photos2")


def test_storage_migration_keeps_all_identity_and_models(tmp_path,monkeypatch):
    monkeypatch.delenv("SEEKDEX_HOME",raising=False)
    legacy = tmp_path/"legacy"
    cache = legacy/"thumbnails"
    new = tmp_path/"new"
    photos = tmp_path/"photos"
    photos.mkdir()
    Image.new("RGB",(20,20),"red").save(photos/"a.png")
    with FileIndex(legacy/"index.db") as database:
        Indexer(database).refresh(photos)
        record = next(database.query(SearchOptions(photos)))
        EmbeddingStore(database,"test-model",3).save(record,np.array([1,0,0]))
        OCRStore(database).save(record,OCRResult("Windows Update"))
        database.commit()
        cache.mkdir()
        (cache/"cache.jpg").write_bytes(b"preview")
        (legacy/"models"/"test").mkdir(parents=True)
        (legacy/"models"/"test"/"weight.bin").write_bytes(b"existing")
        migrate_legacy(new,legacy,cache)
    with FileIndex(new/"data"/"database.sqlite3") as migrated:
        current = next(migrated.query(SearchOptions(photos)))
        assert (current.id,current.file_uid)==(record.id,record.file_uid)
        assert EmbeddingStore(migrated,"test-model",3).get(current) is not None
        assert OCRStore(migrated).get(current)["ocr_text"]=="Windows Update"
    assert (new/"models"/"test"/"weight.bin").read_bytes()==b"existing"
    assert (new/"cache"/"thumbnails"/"cache.jpg").read_bytes()==b"preview"
    assert (legacy/"index.db").exists()
    assert not AppSettings(new/"config"/"settings.ini").first_launch


def test_migration_never_overwrites_existing_database(tmp_path,monkeypatch):
    monkeypatch.delenv("SEEKDEX_HOME",raising=False)
    old,new = tmp_path/"old",tmp_path/"new"
    old.mkdir()
    with sqlite3.connect(old/"index.db") as db:db.execute("CREATE TABLE old(value)")
    target = new/"data"/"database.sqlite3"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as db:db.execute("CREATE TABLE keep(value)")
    notes = migrate_legacy(new,old,old/"thumbnails")
    assert any("未覆盖" in message for message in notes)
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='keep'").fetchone()


def test_isolated_profile_does_not_import_real_data(profile):
    assert migrate_legacy()==[]
    assert not get_database_path().exists()


def test_copy_cache_preserves_existing_and_rejects_nested(tmp_path):
    source,target = tmp_path/"old",tmp_path/"new"
    source.mkdir();target.mkdir()
    (source/"weight.bin").write_bytes(b"old")
    (target/"weight.bin").write_bytes(b"new")
    copy_missing_tree(source,target)
    assert (target/"weight.bin").read_bytes()==b"new"
    with pytest.raises(ValueError):copy_missing_tree(source,source/"nested")


def test_cache_cleanup_does_not_delete_user_photos(tmp_path):
    (tmp_path/("a"*64+".jpg")).write_bytes(b"cache")
    (tmp_path/"holiday.jpg").write_bytes(b"user-photo")
    assert prune_thumbnails(tmp_path,0,clear=True)==1
    assert (tmp_path/"holiday.jpg").read_bytes()==b"user-photo"


def test_resource_path_frozen_location(tmp_path,monkeypatch):
    import seekdex.resources as module
    monkeypatch.setattr(module,"__file__",str(tmp_path/"frozen"/"seekdex"/"resources.py"))
    monkeypatch.setattr(__import__("sys"),"frozen",True,raising=False)
    assert module.resource_path("app.ico")==tmp_path/"frozen"/"seekdex"/"resources"/"app.ico"
    with pytest.raises(ValueError):module.resource_path("../../outside")


@pytest.fixture
def product_window(profile,monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM","offscreen")
    from seekdex.ai.panel import AIPanel
    from seekdex.ocr.panel import OCRPanel
    from seekdex.ui import MainWindow
    monkeypatch.setattr(AIPanel,"check_status",lambda self:None)
    monkeypatch.setattr(OCRPanel,"check_status",lambda self:None)
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    wait_until(lambda:window._catalog_task is None)
    yield window
    window.close()
    wait_until(lambda:not window.isVisible())
    application.processEvents()


def test_about_uses_authoritative_version(product_window):
    from seekdex.about import AboutDialog
    dialog = AboutDialog(product_window)
    assert VERSION in dialog.version_label.text()
    dialog.close()


def test_first_run_skip_marks_completed_without_indexing(product_window):
    product_window.show_first_run()
    wizard = product_window._wizard
    assert wizard.pageIds()==list(range(7))
    wizard.reject()
    assert not product_window.config.first_launch
    assert product_window._worker is None


def test_window_geometry_filters_and_view_restore(product_window,profile):
    window = product_window
    window.config.set("general/remember_filters",True)
    window.config.save()
    window.filename.setText("remember-this")
    window.extensions.setText("nef,png")
    window._set_result_view("grid")
    window.resize(1430,870)
    window._save_product_preferences()
    from seekdex.ui import MainWindow
    reopened = MainWindow()
    try:
        assert reopened.filename.text()=="remember-this"
        assert reopened.extensions.text()=="nef,png"
        assert reopened._using_grid
        # Qt deliberately clamps saved geometry when the available screen shrinks.
        assert reopened.config.get("window/geometry")==window.config.get("window/geometry")
        assert reopened.width()<=QApplication.primaryScreen().availableGeometry().width()
    finally:
        reopened.close()


def test_list_sort_preserves_selection_identity(product_window,tmp_path):
    window = product_window
    window.model.append([SearchResult(tmp_path/"z.txt",3,1),SearchResult(tmp_path/"a.txt",1,2)])
    window.table.setCurrentIndex(window.model.index(0,1))
    window.model.sort(1)
    assert window.model.results[0].path.name=="a.txt"
    assert window.model.results[window.table.currentIndex().row()].path.name=="z.txt"


def test_grid_shares_results_and_ocr_hit_hint(product_window,tmp_path):
    window = product_window
    window.model.append([SearchResult(tmp_path/"screen.png",3,1,similarity=.5)])
    window.model.ocr_query = "Windows"
    window._set_result_view("grid")
    assert window.grid.model() is window.table.model()
    assert "OCR 命中" in window.model.data(window.model.index(0,0))


def test_missing_models_do_not_disable_ordinary_search(product_window):
    window = product_window
    window.ai._installed = False
    window.ocr._installed = False
    assert window.search_button.isEnabled() and window.filename.isEnabled()


def test_product_scan_status_and_integrity(scopes):
    from seekdex.product_worker import ProductTask
    root,sub,other,database = scopes
    task = ProductTask("scan",database.database_path,roots=[root,other])
    results=[]
    task.completed.connect(lambda value,error:results.append((value,error)))
    task.run()
    assert not results[0][1]
    assert all(item["status"]=="complete" for item in results[0][0])
    assert len(IndexCatalog(database).directories())==2


def test_model_cleanup_preserves_embedding(scopes,tmp_path):
    from seekdex.ai.model_cache import snapshot_dir
    from seekdex.product_worker import ProductTask
    root,sub,other,database = scopes
    record = next(r for r in database.query(SearchOptions(root)) if r.is_image)
    EmbeddingStore(database,"model",3).save(record,np.array([1,0,0]))
    database.commit()
    model_root = tmp_path/"models"
    snapshot_dir(model_root).mkdir(parents=True)
    (snapshot_dir(model_root)/"model.safetensors").write_bytes(b"fixture")
    task = ProductTask("clear_models",database.database_path,parameters=dict(kind="ai",directory=str(model_root)))
    task.run()
    assert not snapshot_dir(model_root).exists()
    assert EmbeddingStore(database,"model",3).get(record) is not None


def test_thumbnail_limit_preserves_user_files(tmp_path):
    (tmp_path/("a"*64+".jpg")).write_bytes(b"a"*100)
    (tmp_path/("b"*64+".jpg")).write_bytes(b"b"*100)
    (tmp_path/"photo.jpg").write_bytes(b"keep")
    assert prune_thumbnails(tmp_path,100)==1
    assert (tmp_path/"photo.jpg").read_bytes()==b"keep"


def test_privacy_log_redacts_paths(profile):
    import logging
    from seekdex.logging_setup import PrivacyFormatter
    config = AppSettings()
    config.set("general/log_paths",False)
    config.save()
    record = logging.LogRecord("seekdex",40,"",0,'File "C:\\Private\\photo.jpg" failed',(),None)
    message = PrivacyFormatter("%(message)s").format(record)
    assert "Private" not in message and "隐藏" in message


def test_readonly_cache_can_migrate(tmp_path):
    source,target = tmp_path/"source",tmp_path/"target"
    source.mkdir()
    blob=source/"compiled.blob"
    blob.write_bytes(b"compiled")
    blob.chmod(0o444)
    try:
        copy_missing_tree(source,target)
        assert (target/blob.name).read_bytes()==b"compiled"
        assert not list(target.glob("*.tmp"))
    finally:blob.chmod(0o666)


def test_single_directory_error_does_not_stop_managed_batch(scopes,tmp_path):
    from seekdex.product_worker import ProductTask
    root,sub,other,database = scopes
    task = ProductTask("scan",database.database_path,roots=[tmp_path/"missing",root])
    outcomes=[]
    task.completed.connect(lambda value,error:outcomes.append((value,error)))
    task.run()
    assert not outcomes[0][1]
    assert outcomes[0][0][0]["status"]=="error"
    assert outcomes[0][0][1]["status"]=="complete"


def test_multi_directory_search_skips_unavailable_scope(scopes,tmp_path):
    root,sub,other,database=scopes
    missing=tmp_path/"missing"
    task=SearchThread(SearchOptions(missing,folders=(missing,root)),database_path=database.database_path)
    rows=[]
    done=[]
    task.batch_ready.connect(rows.extend)
    task.search_done.connect(lambda count,cancelled,error:done.append((count,error)))
    task.run()
    assert rows and not done[0][1]
    assert all(r.path.is_relative_to(root) for r in rows)


def test_logging_does_not_preempt_legacy_configuration(profile):
    from seekdex.logging_setup import setup_logging
    setup_logging(profile)
    assert not get_config_path().exists()


def test_migration_merges_missing_config_preserves_current(tmp_path,monkeypatch):
    monkeypatch.delenv("SEEKDEX_HOME",raising=False)
    old,new=tmp_path/"old",tmp_path/"new"
    old.mkdir()
    prior=QSettings(str(old/"settings.ini"),QSettings.IniFormat)
    prior.setValue("search/folder","D:/Photos")
    prior.setValue("search/recursive",False)
    prior.sync()
    current=AppSettings(new/"config"/"settings.ini")
    current.set("search/folder","E:/Pictures")
    current.save()
    migrate_legacy(new,old,old/"thumbnails")
    restored=AppSettings(new/"config"/"settings.ini")
    assert restored.get("search/folder")=="E:/Pictures"
    assert restored.get("search/recursive",True) is False

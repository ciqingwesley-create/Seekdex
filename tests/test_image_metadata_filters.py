from dataclasses import replace
from pathlib import Path
from datetime import datetime
import os
import sqlite3
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from seekdex.image_metadata import (read_image_metadata, ensure_image_metadata,
    camera_display_name, METADATA_VERSION)
from seekdex.image_filters import matches_image_filters
from seekdex.metadata_worker import complete_metadata, MetadataThread
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.index.models import FileRecord
from seekdex.paths import path_key
from seekdex.search import SearchOptions, inspect_image
from seekdex.ai.store import EmbeddingStore
from seekdex.worker import SearchThread, matches


def photo(path, size=(60,40), make='NIKON CORPORATION', model='NIKON D800', orientation=1):
    exif = Image.Exif()
    if make: exif[271] = make
    if model: exif[272] = model
    exif[274] = orientation
    exif[34665] = {36867:'2026:05:06 14:23:10'}
    with Image.new('RGB',size,'green') as image:
        image.save(path,exif=exif)


def test_jpeg_camera_preserves_original_values_and_time(tmp_path):
    path = tmp_path/'camera.jpg'
    photo(path)
    metadata = read_image_metadata(path,path.stat().st_mtime)
    assert metadata.camera_make=='NIKON CORPORATION' and metadata.camera_model=='NIKON D800'
    assert camera_display_name(metadata.camera_make,metadata.camera_model)=='Nikon D800'
    assert (metadata.width,metadata.height)==(60,40)
    assert metadata.capture.text=='2026-05-06T14:23:10'


@pytest.mark.parametrize('make,model,expected',[
    (' NIKON CORPORATION ','NIKON   D800','Nikon D800'),
    ('Canon','Canon EOS R5','Canon EOS R5'),
    ('SONY','ILCE-7M4','Sony ILCE-7M4'),
    ('Apple','iPhone 16 Pro','Apple iPhone 16 Pro'),
    (None,'Unknown Camera','Unknown Camera'), (None,None,''),
    ('ACME','ACME2 Camera','Acme ACME2 Camera'),
])
def test_conservative_device_display(make,model,expected):
    assert camera_display_name(make,model)==expected


@pytest.mark.parametrize('extension',['nef','nrw'])
def test_raw_reads_make_model_and_sensor_header_not_thumbnail(tmp_path,monkeypatch,extension):
    path = tmp_path/f'camera.{extension}'
    path.write_bytes(b'raw fixture')
    class Raw:
        sizes = SimpleNamespace(width=7360,height=4912,flip=6)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def unpack(self): raise AssertionError('RAW decoded')
        def postprocess(self,**kwargs): raise AssertionError('RAW decoded')
        def extract_thumb(self): raise AssertionError('preview generated')
    import rawpy
    monkeypatch.setattr(rawpy,'imread',lambda _:Raw())
    monkeypatch.setattr('seekdex.image_metadata.read_exif_tags',lambda _: {
        'Image Make':'NIKON CORPORATION','Image Model':'NIKON D800',
        'Image ImageWidth':160,'Image ImageLength':120,'Image SubIFDs':[42],
        'Image Orientation':6})
    metadata = read_image_metadata(path,1)
    assert (metadata.width,metadata.height)==(4912,7360)
    assert metadata.camera_model=='NIKON D800' and metadata.error is None


def test_nef_tiff_exif_is_read_directly(tmp_path,monkeypatch):
    path = tmp_path/'fixture.nef'
    exif = Image.Exif()
    exif[271],exif[272] = 'NIKON CORPORATION','NIKON D800'
    Image.new('RGB',(60,40)).save(path,format='TIFF',exif=exif)
    import rawpy
    monkeypatch.setattr(rawpy,'imread',lambda _:(_ for _ in ()).throw(ValueError('No raw pixels')))
    metadata = read_image_metadata(path,1)
    assert metadata.camera_make=='NIKON CORPORATION' and metadata.camera_model=='NIKON D800'
    assert (metadata.width,metadata.height)==(60,40)


@pytest.mark.parametrize('orientation',range(1,9))
def test_exif_orientation_dimensions_and_thumbnail(tmp_path,orientation):
    path = tmp_path/'oriented.jpg'
    photo(path,orientation=orientation)
    metadata = read_image_metadata(path,1)
    expected = (40,60) if orientation>=5 else (60,40)
    assert (metadata.width,metadata.height)==expected
    width,height,thumbnail = inspect_image(path,with_thumbnail=True)
    assert (width,height)==expected
    from io import BytesIO
    with Image.open(BytesIO(thumbnail)) as preview:
        assert preview.size==expected


@pytest.mark.parametrize('extension',['jpg','png','webp'])
def test_no_exif_has_no_guessed_device(tmp_path,extension):
    path = tmp_path/f'screenshot.{extension}'
    Image.new('RGB',(24,16)).save(path)
    metadata = read_image_metadata(path,1)
    assert metadata.camera_make is None and metadata.camera_model is None
    assert (metadata.width,metadata.height)==(24,16)
    assert metadata.capture.source=='filesystem_mtime'


def test_metadata_never_decodes_jpeg_or_png_pixels(tmp_path,monkeypatch):
    from PIL import JpegImagePlugin, PngImagePlugin
    paths = [tmp_path/'a.jpg',tmp_path/'b.png']
    for path in paths: photo(path,orientation=6)
    def forbidden(*args,**kwargs): raise AssertionError('pixel decoding')
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile,'load',forbidden)
    monkeypatch.setattr(PngImagePlugin.PngImageFile,'load',forbidden)
    for path in paths:
        metadata = read_image_metadata(path,1)
        assert metadata.error is None and (metadata.width,metadata.height)==(40,60)


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path/'photos'
    root.mkdir()
    with FileIndex(tmp_path/'index.db') as database:
        sizes = {'large.nef':(7360,4912),'horizontal.jpg':(3840,2160),'vertical.jpg':(2160,3840),
                 'small.png':(1919,1080),'square.png':(4000,4000),'8k.jpg':(4320,7680)}
        for name,(width,height) in sizes.items():
            database.upsert(FileRecord(root/name,name,Path(name).suffix.lstrip('.'),100,1000,1000000000000,
                True,width,height,root,1,camera_make='NIKON CORPORATION',camera_model='NIKON D800',
                metadata_version=METADATA_VERSION,capture_time=1000,capture_time_source='filesystem_mtime',
                capture_time_text=datetime.fromtimestamp(1000).isoformat(timespec='seconds')))
        database.upsert(FileRecord(root/'note.txt','note.txt','txt',5,1000,1000000000000,False,None,None,root,1))
        database.commit()
        yield root,database


@pytest.mark.parametrize('field,value,expected',[
    ('min_width',4000,{'large.nef','square.png','8k.jpg'}),
    ('max_width',2160,{'vertical.jpg','small.png'}),
    ('min_height',4912,{'large.nef','8k.jpg'}),
    ('max_height',2160,{'horizontal.jpg','small.png'}),
    ('min_megapixels',30,{'large.nef','8k.jpg'}),
    ('max_megapixels',8.3,{'horizontal.jpg','vertical.jpg','small.png'}),
    ('orientation','landscape',{'large.nef','horizontal.jpg','small.png'}),
    ('orientation','portrait',{'vertical.jpg','8k.jpg'}),
    ('orientation','square',{'square.png'}),
    ('resolution','fhd',{'large.nef','horizontal.jpg','vertical.jpg','square.png','8k.jpg'}),
    ('resolution','4k',{'large.nef','horizontal.jpg','vertical.jpg','square.png','8k.jpg'}),
    ('resolution','8k',{'8k.jpg'}),
])
def test_resolution_filters_match_sql_and_progressive(indexed,field,value,expected):
    root,database = indexed
    options = replace(SearchOptions(root),**{field:value})
    assert {r.name for r in database.query(options)}==expected
    assert {r.name for r in database.query(SearchOptions(root)) if matches(r,options)}==expected


@pytest.mark.parametrize('query',['Nikon','nikon corporation','NIKON D800','D800','d800','Nikon D800'])
def test_camera_make_model_display_and_casefold_queries(indexed,query):
    root,database = indexed
    options = SearchOptions(root,camera=query)
    assert len(list(database.query(options)))==6
    assert all(matches_image_filters(r,options) for r in database.query(options))


def test_combined_filters_and_sql_search_never_read_images(indexed,monkeypatch):
    root,database = indexed
    options = SearchOptions(root,camera='Nikon D800',extensions=frozenset({'nef'}),min_megapixels=30,
                            filename='large',time_type='capture',modified_from=0,modified_before=2000)
    monkeypatch.setattr('seekdex.image_metadata.read_image_metadata',lambda *args:
        (_ for _ in ()).throw(AssertionError('search reads an image')))
    assert [r.name for r in database.query(options)]==['large.nef']
    results,done = [],[]
    task = SearchThread(options,database_path=database.database_path)
    database.mark_root_complete(root)
    database.commit()
    task.batch_ready.connect(lambda batch:results.extend(batch))
    task.search_done.connect(lambda *args:done.append(args))
    task.run()
    assert done==[(1,False,'')]
    assert [r.path.name for r in results]==['large.nef']


def test_unknown_dimensions_and_camera_do_not_match_image_filters(tmp_path):
    record = FileRecord(tmp_path/'a.jpg','a.jpg','jpg',1,1,1,True,None,None,tmp_path,1)
    for options in (SearchOptions(tmp_path,min_megapixels=0),SearchOptions(tmp_path,orientation='portrait'),
                    SearchOptions(tmp_path,resolution='4k'),SearchOptions(tmp_path,camera='Nikon')):
        assert not matches_image_filters(record,options)


def test_legacy_unrotated_dimensions_remain_stored_until_completion(indexed):
    root,database = indexed
    record = next(database.query(SearchOptions(root,filename='vertical')))
    legacy = replace(record,width=3840,height=2160,metadata_version=0)
    database.upsert(legacy)
    assert database.get_by_id(record.id).width==3840
    options = SearchOptions(root,filename='vertical',orientation='landscape')
    assert not list(database.query(options)) and not matches_image_filters(legacy,options)


def test_metadata_write_rejects_replaced_uid(indexed):
    root,database = indexed
    record = next(database.query(SearchOptions(root,filename='vertical')))
    database.delete_path(path_key(record.path))
    database.upsert(replace(record,id=None,file_uid=None,camera_make='Apple',camera_model='iPhone'))
    replacement = database.get_by_key(path_key(record.path))
    assert replacement.file_uid!=record.file_uid
    assert not database.update_image_metadata(record)
    assert database.get_by_id(replacement.id).camera_model=='iPhone'


def test_legacy_search_api_accepts_device_and_oriented_filters(tmp_path):
    from seekdex.search import search
    path = tmp_path/'a.jpg'
    photo(path,orientation=6)
    options = SearchOptions(tmp_path,camera='d800',min_width=40,max_height=60,orientation='portrait')
    result = list(search(options))
    assert [r.path.name for r in result]==['a.jpg']
    assert result[0].camera_model=='NIKON D800' and (result[0].width,result[0].height)==(40,60)


@pytest.mark.parametrize('kwargs',[{'min_width':100,'max_width':50},{'max_height':-1},
    {'min_megapixels':float('nan')},{'min_megapixels':float('inf')},{'orientation':'bad'},{'resolution':'16k'}])
def test_invalid_filter_bounds(kwargs,tmp_path):
    with pytest.raises(ValueError): SearchOptions(tmp_path,**kwargs)


def test_metadata_completion_persists_skips_and_invalidates(tmp_path,monkeypatch):
    root = tmp_path/'photos'
    root.mkdir()
    path = root/'a.jpg'
    photo(path,orientation=6)
    with FileIndex(tmp_path/'index.db') as database:
        Indexer(database).refresh(root)
        before = database.get_by_key(path_key(path))
        store = EmbeddingStore(database,'test',3)
        store.save(before,np.array([1,2,3],dtype=np.float32))
        database.commit()
        blob_before = database.connection.execute('SELECT embedding,indexed_at FROM image_embeddings').fetchone()[:]
        revision = store.revision()
        completed = complete_metadata(database,SearchOptions(root))
        assert completed.processed==1 and completed.failed==0
        record = database.get_by_key(path_key(path))
        assert (record.width,record.height)==(40,60) and record.camera_model=='NIKON D800'
        assert database.connection.execute('SELECT embedding,indexed_at FROM image_embeddings').fetchone()[:]==blob_before
        assert store.revision()==revision and record.file_uid==before.file_uid
        assert complete_metadata(database,SearchOptions(root)).processed==0
        import seekdex.image_metadata as metadata_module
        original = metadata_module.read_image_metadata
        monkeypatch.setattr(metadata_module,'read_image_metadata',lambda *a:(_ for _ in ()).throw(AssertionError('reread')))
        Indexer(database).refresh(root)
        assert ensure_image_metadata(database.get_by_key(path_key(path)),database)==record
        monkeypatch.setattr(metadata_module,'read_image_metadata',original)
        old = path.stat()
        photo(path,size=(90,50),make='Canon',model='Canon EOS R5')
        os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns+2_000_000_000))
        Indexer(database).refresh(root)
        changed = database.get_by_key(path_key(path))
        assert changed.metadata_version==0 and changed.camera_make is None and changed.width is None
        assert store.get(changed) is None
        assert complete_metadata(database,SearchOptions(root)).processed==1
        assert database.get_by_key(path_key(path)).camera_model=='Canon EOS R5'


def test_old_database_migration_preserves_dimensions_uid_and_embedding(tmp_path):
    root = tmp_path/'photos'
    root.mkdir()
    photo(root/'a.jpg')
    db_path = tmp_path/'index.db'
    with FileIndex(db_path) as database:
        Indexer(database).refresh(root)
        record = next(database.query(SearchOptions(root)))
        database.update_image_details(path_key(record.path),record.mtime_ns,record.size,60,40)
        EmbeddingStore(database,'test',3).save(record,np.ones(3))
        database.commit()
        uid = record.file_uid
        blob = database.connection.execute('SELECT embedding FROM image_embeddings').fetchone()[0]
    with sqlite3.connect(db_path) as connection:
        for field in ('camera_make','camera_model','camera_search_text','metadata_version','metadata_error'):
            connection.execute(f'ALTER TABLE files DROP COLUMN {field}')
    with FileIndex(db_path) as database:
        record = next(database.query(SearchOptions(root)))
        assert record.file_uid==uid and (record.width,record.height)==(60,40)
        assert record.camera_make is None and record.metadata_version==0
        columns = {r[1] for r in database.connection.execute('PRAGMA table_info(files)')}
        assert 'image_width' not in columns and 'image_height' not in columns
        assert database.connection.execute('SELECT embedding FROM image_embeddings').fetchone()[0]==blob
        complete_metadata(database,SearchOptions(root))
        assert database.get_by_id(record.id).file_uid==uid
        assert database.connection.execute('SELECT embedding FROM image_embeddings').fetchone()[0]==blob


def test_completion_cancel_resume_scope_and_missing_file(tmp_path):
    root = tmp_path/'photos'
    sub = root/'sub'
    sub.mkdir(parents=True)
    for i in range(5): photo(root/f'{i}.jpg')
    photo(sub/'nested.jpg')
    (root/'broken.jpg').write_bytes(b'broken')
    with FileIndex(tmp_path/'index.db',batch_size=100) as database:
        Indexer(database).refresh(root)
        saved = []
        stats = complete_metadata(database,SearchOptions(root,recursive=False),
            cancelled=lambda:len(saved)>=2,on_record=lambda r:saved.append(r))
        assert stats.cancelled and stats.processed==2
        assert database.metadata_status(SearchOptions(root,recursive=False),1)[1]==4
        assert database.connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        stats = complete_metadata(database,SearchOptions(root,recursive=False))
        assert stats.processed==4 and stats.failed==1
        assert database.metadata_status(SearchOptions(root,recursive=False),1)[1]==0
        assert database.get_by_key(path_key(sub/'nested.jpg')).metadata_version==0
        devices,pending,total = database.metadata_status(SearchOptions(root),1)
        assert devices==['Nikon D800'] and pending==1 and total==7
        assert complete_metadata(database,SearchOptions(root)).processed==1


def test_metadata_thread_cancels_without_losing_committed_records(tmp_path):
    root = tmp_path/'photos'
    root.mkdir()
    for i in range(25): photo(root/f'{i}.jpg')
    db_path = tmp_path/'index.db'
    with FileIndex(db_path) as database: Indexer(database).refresh(root)
    task = MetadataThread(SearchOptions(root),db_path)
    done = []
    task.batch_ready.connect(lambda batch:task.cancel())
    task.completed.connect(lambda stats,error:done.append((stats,error)))
    task.run()
    assert done[0][0].cancelled and done[0][0].processed==20 and not done[0][1]
    with FileIndex(db_path) as database:
        assert database.metadata_status(SearchOptions(root),1)[1]==5
        assert complete_metadata(database,SearchOptions(root)).processed==5


def test_camera_metadata_survives_move_copy_and_undo(tmp_path):
    from seekdex.organize.engine import Organizer
    from seekdex.organize.planner import prepare_plan
    from seekdex.thumbnail_cache import ThumbnailCache
    root = tmp_path/'photos'
    root.mkdir()
    path = root/'portrait.jpg'
    photo(path,orientation=6)
    with FileIndex(tmp_path/'index.db') as database:
        Indexer(database).refresh(root)
        complete_metadata(database,SearchOptions(root))
        original = database.get_by_key(path_key(path))
        store = EmbeddingStore(database,'test',3)
        store.save(original,np.ones(3))
        database.commit()
        organizer = Organizer(database,ThumbnailCache(tmp_path/'cache'))
        moved = organizer.execute(prepare_plan([path],tmp_path/'moved',database,
            action='move',template='{filename}'),dry_run=False)[0]
        assert moved.status=='success'
        record = database.get_by_key(path_key(moved.target))
        assert record.file_uid==original.file_uid and record.camera_model=='NIKON D800'
        assert (record.width,record.height)==(40,60) and store.get(record) is not None
        copied = organizer.execute(prepare_plan([moved.target],tmp_path/'copies',database,
            action='copy',template='{filename}'),dry_run=False)[0]
        copy_record = database.get_by_key(path_key(copied.target))
        assert copy_record.camera_model=='NIKON D800' and copy_record.metadata_version==1
        assert copy_record.file_uid!=record.file_uid and store.get(copy_record) is None
        assert organizer.undo(moved.operation_id).status=='success'
        restored = database.get_by_key(path_key(path))
        assert restored.file_uid==original.file_uid and restored.camera_model=='NIKON D800'
        assert (restored.width,restored.height)==(40,60) and store.get(restored) is not None


def test_ui_combines_and_clears_new_filters_and_details(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from seekdex.ui import MainWindow
    from seekdex.search import SearchResult
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path/'index.db',tmp_path/'cache')
    try:
        window.folder.setText(str(tmp_path))
        panel = window.image_filters
        panel.group.setChecked(True)
        panel.set_devices(['Nikon D800','Apple iPhone 16 Pro'],0,2)
        panel.camera.setCurrentText('d800')
        panel.bounds['min_megapixels'].setValue(30)
        panel.orientation.setCurrentIndex(2)
        panel.resolution.setCurrentIndex(2)
        options = window._search_options()
        assert options.camera=='d800' and options.min_megapixels==30
        assert options.orientation=='portrait' and options.resolution=='4k'
        panel.group.setChecked(False)
        cleared = window._search_options()
        assert cleared.camera=='' and cleared.min_megapixels is None and cleared.orientation=='all'
        window.model.append([SearchResult(tmp_path/'a.jpg',1,1,width=4912,height=7360,is_image=True,
            camera_make='NIKON CORPORATION',camera_model='NIKON D800',metadata_version=1)])
        window.table.selectRow(0)
        window._show_image_details()
        assert 'Nikon D800' in window.image_details.text() and '36.2 MP' in window.image_details.text()
        assert 'None' not in window.image_details.text()
    finally:
        window.close()
        app.processEvents()

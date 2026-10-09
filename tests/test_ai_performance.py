"""Performance mechanisms tested with small inputs, without model downloads."""
from contextlib import nullcontext
from pathlib import Path
from threading import Event, enumerate as threads
from types import SimpleNamespace
import json
import sys

import numpy as np
from PIL import Image, ImageOps
import pytest

from seekdex.ai.interfaces import EmbeddingBackend
from seekdex.ai.model_cache import MODEL_ID
from seekdex.ai.openvino_backend import OpenVINOEmbeddingBackend, detect_openvino
from seekdex.ai.profiling import IndexProfiler
from seekdex.ai.service import AIService
from seekdex.ai.store import EmbeddingStore
from seekdex.index.database import FileIndex
from seekdex.index.indexer import Indexer
from seekdex.search import SearchOptions


class PreparedBackend(EmbeddingBackend):
    model_id = MODEL_ID
    embedding_dimension = 512
    batch_size = 4
    device = "test"
    model = object()
    def __init__(self):
        self.batches = []
        self.prepared = 0
    def load_model(self):
        pass
    def prepare_image(self, image):
        self.prepared += 1
        return np.asarray(image, dtype=np.float32).mean(axis=(0, 1))
    def encode_prepared(self, items):
        self.batches.append(len(items))
        result = np.zeros((len(items), 512), dtype=np.float32)
        result[:, :3] = items
        return result
    def encode_images(self, images):
        raise AssertionError("pipeline must infer prepared batches")
    def encode_text(self, text):
        return np.ones(512, dtype=np.float32)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for n in range(24):
        Image.new('RGB', (20, 16), (n+1, 30, 90)).save(root / f'{n:02}.png')
    with FileIndex(tmp_path / 'index.db') as database:
        Indexer(database).refresh(root)
        yield root, database


def test_true_prepared_batch_inference(library):
    root, database = library
    backend = PreparedBackend()
    profiler = IndexProfiler()
    stats = AIService(database, backend, profiler=profiler).build(SearchOptions(root))
    assert stats.completed == 24 and stats.failed == 0
    assert backend.batches == [4]*6 and backend.prepared == 24
    assert profiler.calls['sqlite'] == 6
    assert stats.pictures_s > 0 and stats.batch_size == 4


def test_pipeline_prepares_next_batch_during_inference(library):
    root, database = library
    backend = PreparedBackend()
    next_image = Event()
    original_prepare, original_encode = backend.prepare_image, backend.encode_prepared
    def prepare(image):
        value = original_prepare(image)
        if backend.prepared >= 5:
            next_image.set()
        return value
    def encode(items):
        assert next_image.wait(2), 'reader did not overlap inference'
        return original_encode(items)
    backend.prepare_image, backend.encode_prepared = prepare, encode
    assert AIService(database, backend).build(SearchOptions(root)).completed == 24


def test_pipeline_cancel_is_bounded_durable_and_resumable(library):
    root, database = library
    backend = PreparedBackend()
    stop = Event()
    original = backend.encode_prepared
    def encode(items):
        value = original(items)
        stop.set()
        return value
    backend.encode_prepared = encode
    stats = AIService(database, backend).build(SearchOptions(root), cancelled=stop.is_set)
    assert stats.cancelled and stats.completed == 4
    assert backend.prepared <= 4*3+1
    assert database.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    backend.encode_prepared = original
    resumed = AIService(database, backend).build(SearchOptions(root))
    assert resumed.existing == 4 and resumed.completed == 20
    assert not any(t.name.startswith('clip-') for t in threads())


def test_one_sqlite_transaction_per_batch(library):
    root, database = library
    statements = []
    database.connection.set_trace_callback(statements.append)
    records = list(database.query(SearchOptions(root)))[:4]
    saved = EmbeddingStore(database, MODEL_ID, 512).save_batch(records, np.ones((4, 512)))
    assert saved == [True]*4
    assert sum(s.startswith('BEGIN') for s in statements) == 1
    assert sum(s == 'COMMIT' for s in statements) == 1


def test_invalid_vector_does_not_discard_good_batch_rows(library):
    root, database = library
    records = list(database.query(SearchOptions(root)))[:4]
    vectors = np.ones((4, 512), dtype=np.float32)
    vectors[1] = 0
    vectors[2, 0] = np.nan
    store = EmbeddingStore(database, MODEL_ID, 512)
    assert store.save_batch(records, vectors) == [True, False, False, True]
    assert len(store.valid_uids()) == 2


def test_failed_batch_transaction_rolls_back(library, monkeypatch):
    root, database = library
    records = list(database.query(SearchOptions(root)))[:4]
    store = EmbeddingStore(database, MODEL_ID, 512)
    original = store._save_normalized
    def save(record, vector):
        if record == records[1]:
            raise OSError('simulated database failure')
        return original(record, vector)
    monkeypatch.setattr(store, '_save_normalized', save)
    with pytest.raises(OSError):
        store.save_batch(records, np.ones((4, 512)))
    assert store.valid_uids() == set()
    assert database.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_writer_failure_does_not_leave_blocked_threads(library, monkeypatch):
    import seekdex.ai.pipeline as pipeline
    root, database = library
    def fail(*args, **kwargs):
        raise OSError('writer unavailable')
    monkeypatch.setattr(pipeline, 'FileIndex', fail)
    with pytest.raises(RuntimeError):
        AIService(database, PreparedBackend()).build(SearchOptions(root))
    assert not any(t.name.startswith('clip-') for t in threads())


def test_backend_switch_reuses_existing_embeddings_without_loading(library):
    root, database = library
    AIService(database, PreparedBackend()).build(SearchOptions(root))
    before = [tuple(row) for row in database.connection.execute('SELECT * FROM image_embeddings')]
    other = PreparedBackend()
    other.device = 'OpenVINO test'
    other.load_model = lambda: pytest.fail('no pending work should load a model')
    result = AIService(database, other).build(SearchOptions(root))
    assert result.existing == 24 and result.completed == 0
    assert before == [tuple(row) for row in database.connection.execute('SELECT * FROM image_embeddings')]


def test_incompatible_tuned_backend_cannot_write_old_space(library):
    root, database = library
    other = PreparedBackend()
    other.model_id = 'different-model'
    service = AIService(database, PreparedBackend(), backend_setup=lambda *args: other)
    with pytest.raises(ValueError, match='向量空间'):
        service.build(SearchOptions(root))
    assert service.store.valid_uids() == set()


def test_openvino_not_installed_falls_back_to_torch(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'openvino', None)
    assert not detect_openvino()['available']
    backend = OpenVINOEmbeddingBackend('GPU', tmp_path)
    backend.reference.load_model = lambda: None
    backend.reference.model, backend.reference.batch_size = object(), 2
    backend.load_model()
    assert backend._fallback and backend.device == 'PyTorch CPU'
    assert backend.model_id == MODEL_ID


def test_openvino_gpu_compile_failure_falls_back_cpu(tmp_path):
    backend = OpenVINOEmbeddingBackend('GPU', tmp_path)
    called = []
    def compile(model, device, config):
        called.append(device)
        if device == 'GPU':
            raise RuntimeError('unsupported GPU')
        return object()
    backend.core = SimpleNamespace(compile_model=compile)
    backend._compile('GPU')
    assert called == ['GPU', 'CPU']
    assert backend.device == 'OpenVINO CPU' and not backend._fallback


def test_openvino_runtime_failure_tries_cpu_before_torch(tmp_path, monkeypatch):
    backend = OpenVINOEmbeddingBackend('GPU', tmp_path)
    backend.model = object()
    def fail(items):
        raise RuntimeError('GPU driver error')
    backend._compiled = fail
    called = []
    def compile(device):
        called.append(device)
        backend.device = 'OpenVINO CPU'
        backend._compiled = lambda items: [np.ones((len(items[0]), 512))]
    monkeypatch.setattr(backend, '_compile', compile)
    monkeypatch.setattr(backend, '_validate_compatibility', lambda: called.append('validate'))
    assert backend.encode_prepared([np.ones((3, 224, 224))]*4).shape == (4, 512)
    assert called == ['CPU', 'validate'] and not backend._fallback


def test_openvino_cpu_runtime_failure_falls_back_torch(tmp_path):
    backend = OpenVINOEmbeddingBackend('CPU', tmp_path)
    backend.model = object()
    backend._compiled = lambda _: (_ for _ in ()).throw(RuntimeError('CPU error'))
    backend.reference.load_model = lambda: None
    backend.reference.model, backend.reference.batch_size = object(), 2
    backend.reference.encode_prepared = lambda items: np.ones((len(items), 512))
    assert backend.encode_prepared([np.ones((3, 224, 224))]).shape == (1, 512)
    assert backend._fallback and backend.device == 'PyTorch CPU'


@pytest.mark.parametrize('compatible', [True, False])
def test_openvino_compatibility_gate_and_cached_check(tmp_path, compatible):
    backend = OpenVINOEmbeddingBackend('CPU', tmp_path)
    backend.conversion_dir.mkdir(parents=True)
    backend.ov = SimpleNamespace(__version__='test-version')
    backend.core = SimpleNamespace(available_devices=['CPU'], get_property=lambda *args: 'test-hardware')
    backend.reference.load_model = lambda: None
    backend.reference.torch = SimpleNamespace(inference_mode=nullcontext, from_numpy=lambda x: x)
    expected = np.zeros((3, 512), dtype=np.float32)
    expected[:, 0] = 1
    backend.reference.model = SimpleNamespace(get_image_features=lambda **kwargs: SimpleNamespace(numpy=lambda: expected))
    received = expected.copy()
    if not compatible:
        received = np.roll(received, 1, axis=1)
    class Compiled:
        def get_property(self, name): return ['CPU']
        def __call__(self, items): return [received]
    backend._compiled = Compiled()
    if not compatible:
        with pytest.raises(ValueError, match='兼容性'):
            backend._validate_compatibility()
        assert not list(backend.conversion_dir.glob('compatible-*.json'))
    else:
        backend._validate_compatibility()
        assert backend.compatibility_cosine == pytest.approx(1)
        backend.reference.load_model = lambda: pytest.fail('compatibility cache not reused')
        backend._validate_compatibility()


def test_conversion_cache_does_not_convert_again(tmp_path):
    backend = OpenVINOEmbeddingBackend('CPU', tmp_path)
    folder = backend.conversion_dir
    folder.mkdir(parents=True)
    xml, manifest = folder / 'image.xml', folder / 'metadata.json'
    xml.write_text('cached')
    xml.with_suffix('.bin').write_bytes(b'cached')
    metadata = {'model_id': MODEL_ID, 'format': 'test', 'dimension': 512}
    manifest.write_text(json.dumps(metadata))
    backend.ov = object()
    backend.reference.load_model = lambda: pytest.fail('conversion not cached')
    backend._ensure_ir(xml, manifest, metadata)


def test_jpeg_decode_optimization_preserves_previous_pixels(tmp_path):
    from seekdex.ai.images import read_ai_image
    path = tmp_path / 'photo.jpg'
    rng = np.random.default_rng(14)
    Image.fromarray(rng.integers(0, 256, (600, 900, 3), dtype=np.uint8)).save(path)
    with Image.open(path) as image:
        image.draft('RGB', (448, 448))
        image.thumbnail((448, 448), Image.Resampling.LANCZOS)
        expected = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
    profiler = IndexProfiler()
    with read_ai_image(path, profiler) as actual:
        assert np.array_equal(expected, np.asarray(actual))
    assert profiler.seconds['file_read'] > 0 and profiler.calls['pillow_decode'] == 1


def test_tuning_cache_roundtrip_and_runtime_invalidation(tmp_path, monkeypatch):
    import seekdex.ai.tuning as tuning
    tuning.save_tuning({'preferred': 'ov-cpu'}, tmp_path)
    assert tuning.load_tuning(tmp_path)['preferred'] == 'ov-cpu'
    monkeypatch.setattr(tuning, 'tuning_identity', lambda: 'changed-runtime')
    assert tuning.load_tuning(tmp_path) == {}


def test_manual_batch_setting_bypasses_tuning(tmp_path, monkeypatch):
    from seekdex.ai.worker import AIRuntime
    import seekdex.ai.tuning as tuning
    runtime = AIRuntime(tmp_path)
    runtime.batch_override = 8
    monkeypatch.setattr(tuning, 'tune_backend', lambda *args: pytest.fail('manual batch was ignored'))
    backend = PreparedBackend()
    assert runtime.configure_for_index(backend, [], lambda: False, lambda _: None) is backend
    assert backend.batch_size == 8


def test_profiler_reports_overlapped_work_separately():
    profiler = IndexProfiler()
    profiler.add('inference', 2)
    profiler.add('pillow_decode', 1)
    profiler.pictures, profiler.wall_seconds = 4, 2
    report = profiler.report()
    assert report['inference_pictures_s'] == 2
    assert report['decode_pictures_s'] == 4
    assert report['overall_pictures_s'] == 2


def test_cpu_configuration_applied_in_new_inference_thread():
    from threading import Thread
    from seekdex.ai.backend import ChineseClipBackend
    backend = ChineseClipBackend('cpu')
    configured = []
    backend.torch = SimpleNamespace(set_num_threads=configured.append,
                                    set_num_interop_threads=lambda _: None)
    backend.cpu_threads = 4
    backend.configure_execution()
    backend.configure_execution()
    thread = Thread(target=backend.configure_execution)
    thread.start()
    thread.join()
    assert configured == [4, 4]


def test_auto_selection_uses_measured_fastest_backend(tmp_path, monkeypatch):
    from seekdex.ai.worker import AIRuntime
    import seekdex.ai.backend as pytorch
    import seekdex.ai.openvino_backend as openvino
    import seekdex.ai.tuning as tuning
    runtime = AIRuntime(tmp_path)
    runtime.requested_device = 'auto'
    class Backend(PreparedBackend):
        def __init__(self, device, *args):
            super().__init__()
            self.device = 'cpu' if device == 'cpu' else 'OpenVINO '+device
            self.device_message = ''
    monkeypatch.setattr(pytorch, 'ChineseClipBackend', Backend)
    monkeypatch.setattr(openvino, 'OpenVINOEmbeddingBackend', Backend)
    monkeypatch.setattr(openvino, 'detect_openvino', lambda: {'available': True, 'devices': {'CPU': {}, 'GPU': {}}})
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    rates = {'cpu': 5, 'OpenVINO CPU': 10, 'OpenVINO GPU': 6}
    def tune(backend, *args):
        backend.batch_size = 4
        return {'pictures_s': rates[backend.device], 'batch_size': 4, 'threads': 6}
    monkeypatch.setattr(tuning, 'tune_backend', tune)
    path = tmp_path / 'sample.jpg'
    Image.new('RGB', (20, 20), 'red').save(path)
    records = [SimpleNamespace(path=path)]
    winner = runtime.configure_for_index(Backend('cpu'), records, lambda: False, lambda _: None)
    assert winner.device == 'OpenVINO CPU'
    assert tuning.load_tuning(tmp_path)['preferred'] == 'ov-cpu'


def test_cancel_during_tuning_returns_without_error(tmp_path, monkeypatch):
    from seekdex.ai.worker import AIRuntime
    import seekdex.ai.tuning as tuning
    runtime = AIRuntime(tmp_path)
    runtime.requested_device = 'cpu'
    backend = PreparedBackend()
    backend.device = 'cpu'
    def interrupted(*args):
        raise InterruptedError('cancelled tuning')
    monkeypatch.setattr(tuning, 'tune_backend', interrupted)
    path = tmp_path / 'sample.jpg'
    Image.new('RGB', (20, 20), 'red').save(path)
    assert runtime.configure_for_index(backend, [SimpleNamespace(path=path)], lambda: False, lambda _: None) is backend


def test_gpu_incompatible_on_load_uses_openvino_cpu(tmp_path, monkeypatch):
    import seekdex.ai.openvino_backend as module
    backend = OpenVINOEmbeddingBackend('GPU', tmp_path)
    monkeypatch.setattr(module, 'installed', lambda _: True)
    monkeypatch.setattr(backend, '_ensure_ir', lambda *args: None)
    monkeypatch.setitem(sys.modules, 'openvino', SimpleNamespace(__version__='test',
        Core=lambda: SimpleNamespace(available_devices=['CPU', 'GPU'], read_model=lambda _: object())))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(ChineseCLIPProcessor=SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: object())))
    calls = []
    def compile(device):
        calls.append(device)
        backend.device = 'OpenVINO '+device
    def validate():
        if backend.device == 'OpenVINO GPU':
            raise ValueError('incompatible GPU vector')
    monkeypatch.setattr(backend, '_compile', compile)
    monkeypatch.setattr(backend, '_validate_compatibility', validate)
    backend.load_model()
    assert calls == ['GPU', 'CPU'] and backend.device == 'OpenVINO CPU'
    assert not backend._fallback

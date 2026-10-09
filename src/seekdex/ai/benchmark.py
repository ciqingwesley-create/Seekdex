"""Reproducible full-work benchmarks; never touch the user's persistent index."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from .backend import ChineseClipBackend
from .images import read_ai_image
from .profiling import IndexProfiler, timed
from .store import EmbeddingStore, normalize
from ..index.database import FileIndex
from ..organize.planner import current_record


def serial_benchmark(backend, paths: list[Path], batch_size: int, reader=read_ai_image) -> dict:
    """Legacy sequence: read -> preprocess/infer -> normalize -> SQLite batch commit."""
    backend.load_model()
    profiler = IndexProfiler()
    backend.profiler = profiler
    with TemporaryDirectory(prefix="image-benchmark-") as folder:
        with FileIndex(Path(folder) / "index.db", batch_size=100000) as database:
            records = [current_record(path, database) for path in paths]
            database.commit()
            store = EmbeddingStore(database, backend.model_id, backend.embedding_dimension)
            profiler.started = perf_counter()
            for offset in range(0, len(paths), batch_size):
                subset = records[offset:offset+batch_size]
                images = [reader(record.path, profiler) for record in subset]
                try:
                    embeddings = backend.encode_images(images)
                    with timed(profiler, "embedding_normalize"):
                        normalized = normalize(embeddings)
                    with timed(profiler, "sqlite"):
                        for record, vector in zip(subset, normalized):
                            store.save(record, vector)
                        database.commit()
                finally:
                    for image in images:
                        image.close()
            profiler.finish(len(paths))
    backend.profiler = None
    result = profiler.report()
    result.update(backend=backend.device, batch_size=batch_size,
                  effective_batch_size=min(batch_size, len(paths)))
    return result


def pipeline_benchmark(backend, paths: list[Path], batch_size: int) -> dict:
    from .service import AIService
    from ..search import SearchOptions
    backend.load_model()
    backend.batch_size = batch_size
    profiler = IndexProfiler()
    with TemporaryDirectory(prefix="pipeline-benchmark-") as folder:
        with FileIndex(Path(folder) / 'index.db') as database:
            service = AIService(database, backend, profiler=profiler)
            stats = service.build(SearchOptions(paths[0].parent), paths=paths)
            if stats.failed or stats.completed != len(set(paths)):
                raise RuntimeError(f"Benchmark work incomplete: {stats}")
    result = profiler.report()
    result.update(backend=backend.device, batch_size=batch_size,
                  effective_batch_size=min(batch_size, len(paths)), pipeline=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--manifest", type=Path, help="JSON mapping of group names to paths; repeats allowed")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batches", default="2")
    parser.add_argument("--threads", default="6")
    parser.add_argument("--backend", choices=("torch", "ov-cpu", "ov-gpu", "ov-auto"), default="torch")
    parser.add_argument("--pipeline", action="store_true")
    parser.add_argument("--warm-inputs", action="store_true", help="Read selected previews once before timing, for consistent warm-cache comparisons")
    args = parser.parse_args()
    groups = json.loads(args.manifest.read_text(encoding="utf8")) if args.manifest else {"images": [str(p) for p in args.paths]}
    if not groups or any(not entries for entries in groups.values()):
        parser.error("至少提供一张图片，manifest 的分组不能为空")
    batches, thread_counts = [int(n) for n in args.batches.split(',')], [int(n) for n in args.threads.split(',')]
    if any(n < 1 or n > 64 for n in batches) or any(n < 1 for n in thread_counts):
        parser.error("batch 必须为 1–64，线程数必须为正数")
    if args.backend == "torch":
        backend = ChineseClipBackend("cpu")
    else:
        from .openvino_backend import OpenVINOEmbeddingBackend
        backend = OpenVINOEmbeddingBackend({"ov-cpu": "CPU", "ov-gpu": "GPU", "ov-auto": "AUTO"}[args.backend])
    backend.load_model()
    if args.warm_inputs:
        for path in dict.fromkeys(p for entries in groups.values() for p in entries):
            with read_ai_image(Path(path)):
                pass
    import torch
    output = {"torch": torch.__version__, "cpu": torch.__config__.parallel_info(), "results": []}
    for threads in thread_counts:
        torch.set_num_threads(threads)
        if args.backend == "torch":
            backend.cpu_threads = threads
        elif backend._fallback:
            backend.reference.cpu_threads = threads
        else:
            backend.threads = threads
            backend._compile(backend.requested_device)
            backend._validate_compatibility()
        for batch in batches:
            # Warm tensor kernels independently of disk/cache work.
            with read_ai_image(Path(next(iter(groups.values()))[0])) as image:
                backend.encode_images([image]*batch)
            for name, entries in groups.items():
                runner = pipeline_benchmark if args.pipeline else serial_benchmark
                result = runner(backend, [Path(p) for p in entries], batch)
                result.update(group=name, threads=threads)
                result['input_cache'] = 'warmed' if args.warm_inputs else 'OS-managed'
                output["results"].append(result)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(output, indent=2), encoding="utf8")
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

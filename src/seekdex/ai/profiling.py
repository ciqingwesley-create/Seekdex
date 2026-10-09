"""Opt-in, thread-safe stage timers; wall time is distinct from overlapped work."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from time import perf_counter


class IndexProfiler:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self._lock = Lock()
        self.started = perf_counter()
        self.wall_seconds = 0.0
        self.pictures = 0

    def add(self, stage: str, seconds: float) -> None:
        with self._lock:
            self.seconds[stage] += seconds
            self.calls[stage] += 1

    @contextmanager
    def measure(self, stage: str):
        start = perf_counter()
        try:
            yield
        finally:
            self.add(stage, perf_counter()-start)

    def finish(self, pictures: int) -> dict:
        self.wall_seconds = perf_counter()-self.started
        self.pictures = pictures
        return self.report()

    def report(self) -> dict:
        stages = {name: round(self.seconds[name], 6) for name in (
            "file_read", "preview_extract", "raw_unpack", "pillow_decode", "preprocess",
            "tensor_batch", "inference", "embedding_normalize", "sqlite")}
        decode = sum(stages[name] for name in ("file_read", "preview_extract", "raw_unpack", "pillow_decode", "preprocess"))
        return {"pictures": self.pictures, "wall_seconds": round(self.wall_seconds, 6),
                "stages": stages, "calls": dict(self.calls),
                "decode_pictures_s": self.pictures/decode if decode else 0,
                "inference_pictures_s": self.pictures/stages["inference"] if stages["inference"] else 0,
                "overall_pictures_s": self.pictures/self.wall_seconds if self.wall_seconds else 0}


@contextmanager
def timed(profiler: IndexProfiler | None, stage: str):
    if profiler is None:
        yield
    else:
        with profiler.measure(stage):
            yield

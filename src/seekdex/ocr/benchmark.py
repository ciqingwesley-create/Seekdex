"""Optional offline OCR measurements on explicit images and manual keywords."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import tempfile
from time import perf_counter

from ..index.database import FileIndex
from ..organize.planner import current_record
from ..paths import path_key
from .backend import RapidOCRBackend, available_backends
from .images import read_ocr_image
from .store import OCRStore
from .text import normalize_text


def peak_memory_mib() -> float | None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize / 1048576
    return None


def quality_metrics(database: FileIndex, store: OCRStore, samples: list[dict]) -> dict:
    expected: dict[str, set[str]] = {}
    hits = total = exact = 0
    for sample in samples:
        record = database.get_by_key(path_key(Path(sample["path"])))
        row = store.get(record) if record else None
        text = row["normalized_text"] if row else ""
        original = row["ocr_text"] if row else ""
        for keyword in sample.get("keywords", []):
            total += 1
            hits += normalize_text(keyword) in text
            exact += keyword in original
            if record:
                expected.setdefault(keyword, set()).add(record.file_uid)
    sums = {k: 0.0 for k in (1, 5, 10)}
    rankings = {}
    for keyword, uids in expected.items():
        sql, params = store.query_sql("1", [], keyword)
        retrieved = [row["file_uid"] for row in database.connection.execute(sql, params)]
        rankings[keyword] = retrieved
        for k in sums:
            sums[k] += len(uids.intersection(retrieved[:k])) / len(uids)
    return {"keywords": total, "normalized_keyword_hits": hits, "exact_keyword_hits": exact,
            "keyword_recall": hits/total if total else None,
            "exact_keyword_hit_rate": exact/total if total else None,
            "recall_at_k": {str(k): value/len(expected) if expected else None for k,value in sums.items()},
            "queries": len(expected), "rankings": rankings}


def run_benchmark(samples: list[dict], backend, max_side: int = 2048) -> dict:
    """Model load/warmup excluded; all measured images are decoded, OCRed and committed."""
    report = {"requested_backend": backend.backend_id, "model_id": backend.model_id,
              "max_side": max_side, "images": len(samples), "platform": platform.platform(),
              "devices": available_backends()[1]}
    begin = perf_counter()
    backend.load_model()
    report["load_s"] = perf_counter()-begin
    begin = perf_counter()
    with read_ocr_image(Path(samples[0]["path"]), max_side) as image:
        backend.recognize_image(image)
    report["warmup_s"] = perf_counter()-begin
    rows = []
    stages = dict(input_s=0.0, detection_s=0.0, recognition_s=0.0, database_s=0.0)
    with tempfile.TemporaryDirectory(prefix="seekdex-ocr-bench-") as directory:
        with FileIndex(Path(directory)/"index.db") as database:
            records = [current_record(Path(s["path"]).resolve(), database) for s in samples]
            database.commit()
            store = OCRStore(database, backend.model_id)
            buffer = []
            def flush() -> None:
                begin = perf_counter()
                with database.transaction():
                    for record, result in buffer:
                        if not store.save(record, result):
                            raise RuntimeError("Benchmark source signature changed")
                stages["database_s"] += perf_counter()-begin
                buffer.clear()
            started = perf_counter()
            for sample, record in zip(samples, records):
                begin = perf_counter()
                try:
                    image = read_ocr_image(record.path, max_side)
                    input_s = perf_counter()-begin
                    with image:
                        result = backend.recognize_image(image)
                    stat = record.path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (record.size, record.mtime_ns):
                        raise RuntimeError("Source changed during benchmark")
                    stages["input_s"] += input_s
                    stages["detection_s"] += result.detection_s
                    stages["recognition_s"] += result.recognition_s
                    rows.append(dict(sample, input_s=input_s, **asdict(result)))
                    buffer.append((record, result))
                    if len(buffer) >= 8:
                        flush()
                except Exception as exc:
                    rows.append(dict(sample, error=f"{type(exc).__name__}: {exc}"))
            flush()
            report["total_s"] = perf_counter()-started
            report["quality"] = quality_metrics(database, store, samples)
            report["sqlite_integrity"] = database.connection.execute("PRAGMA integrity_check").fetchone()[0]
    successes = sum("error" not in row for row in rows)
    report.update(stages)
    report.update(actual_backend=backend.backend_id, device=backend.device,
                  fallback=getattr(backend,"device_message",""), successful=successes,
                  pictures_s=successes/report["total_s"], peak_ram_mib=peak_memory_mib(), results=rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backend", choices=("ort-cpu","ov-cpu","ov-gpu","ov-auto"), default="ort-cpu")
    parser.add_argument("--variant", choices=("small","medium"), default="small")
    parser.add_argument("--max-side", type=int, default=2048)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    samples = manifest["images"]
    if not samples:
        parser.error("manifest images must not be empty")
    for sample in samples:
        source = Path(sample["path"])
        sample["path"] = str((args.manifest.parent/source if not source.is_absolute() else source).resolve())
    backend = RapidOCRBackend(args.backend, variant=args.variant, max_side=args.max_side)
    report = run_benchmark(samples, backend, args.max_side)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key not in {"quality","results"}},ensure_ascii=True),flush=True)


if __name__ == "__main__":
    main()

"""RapidOCR preprocessing / decoding with ORT or explicit OpenVINO devices."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

from PIL import Image

from .interfaces import OCRBackend, OCRResult
from .model_cache import installed, model_dir, model_files, model_id


def available_backends() -> tuple[list[str], dict[str, str]]:
    devices = ["ort-cpu"]
    details = {}
    try:
        import openvino as ov
        core = ov.Core()
        for device in core.available_devices:
            details[device] = str(core.get_property(device, "FULL_DEVICE_NAME"))
        if "CPU" in details:
            devices.extend(["ov-cpu", "ov-auto"])
        if any(key.startswith("GPU") for key in details):
            devices.append("ov-gpu")
    except Exception as exc:
        details["error"] = str(exc)
    return devices, details


class _OVSession:
    def __init__(self, core, path: Path, device: str) -> None:
        import openvino as ov
        config = {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": ov.Type.f32}
        if device == "CPU":
            config.update(INFERENCE_NUM_THREADS=4, NUM_STREAMS=1)
        self.compiled = core.compile_model(core.read_model(str(path)), device, config)
        self.request = self.compiled.create_infer_request()
        self.devices = list(self.compiled.get_property("EXECUTION_DEVICES"))

    def __call__(self, tensor):
        self.request.infer([tensor])
        return self.request.get_output_tensor().data.copy()


class RapidOCRBackend(OCRBackend):
    def __init__(self, backend_id: str = "ort-cpu", root: Path | None = None,
                 variant: str = "small", max_side: int = 2048) -> None:
        if backend_id not in {"ort-cpu", "ov-cpu", "ov-gpu", "ov-auto"}:
            raise ValueError("未知 OCR 后端")
        self.backend_id = backend_id
        self.root, self.variant, self.max_side = root, variant, max_side
        self.model_id = model_id(variant, max_side)
        self.device = "ONNXRuntime CPU"
        self.device_message = ""
        self.engine = None

    def _create_engine(self, backend_id: str):
        from rapidocr import RapidOCR
        folder = model_dir(self.root, self.variant)
        params = {
            "Global.log_level": "error", "Global.text_score": 0.0,
            "Global.max_side_len": self.max_side,
            "Global.model_root_dir": str(folder),
            "EngineConfig.onnxruntime.intra_op_num_threads": getattr(self,"cpu_threads",4),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.limit_type": "max",
            "Det.limit_side_len": min(self.max_side, 1536),
        }
        for task, (name, _) in model_files(self.variant).items():
            params[f"{task.title()}.model_path"] = str(folder / name)
        # All three paths are explicit, including classification. No implicit downloads.
        engine = RapidOCR(params=params)
        if backend_id == "ort-cpu":
            self.device = "ONNXRuntime CPU"
            return engine
        import openvino as ov
        core = ov.Core()
        cache = folder / "openvino-compiled"
        cache.mkdir(exist_ok=True)
        core.set_property({"CACHE_DIR": str(cache)})
        device = {"ov-cpu": "CPU", "ov-gpu": "GPU", "ov-auto": "AUTO"}[backend_id]
        executed = set()
        for task, (name, _) in model_files(self.variant).items():
            session = _OVSession(core, folder / name, device)
            getattr(engine, "text_" + task).session = session
            executed.update(session.devices)
        self.device = f"OpenVINO {device} ({', '.join(sorted(executed))})"
        return engine

    def load_model(self) -> None:
        if self.engine is not None:
            return
        if not installed(self.root, self.variant):
            raise FileNotFoundError("OCR 模型未安装或校验失败，请点击“安装 / 检查 OCR 模型”")
        chain = [self.backend_id]
        if self.backend_id in {"ov-gpu", "ov-auto"}:
            chain.append("ov-cpu")
        if chain[-1] != "ort-cpu":
            chain.append("ort-cpu")
        errors = []
        for choice in chain:
            try:
                self.engine = self._create_engine(choice)
                self.backend_id = choice
                self.device_message = "；".join(errors)
                return
            except Exception as exc:
                errors.append(f"{choice} 不可用：{type(exc).__name__}: {exc}")
        raise RuntimeError("；".join(errors))

    def _recognize(self, image: Image.Image) -> OCRResult:
        import numpy as np
        from rapidocr.main import RapidOCRError
        rgb = image.convert("RGB")
        try:
            array = np.asarray(rgb)[:, :, ::-1].copy()
        finally:
            rgb.close()
        started = perf_counter()
        prepared, operations = self.engine.preprocess_img(array)
        try:
            crops, detection = self.engine.detect_and_crop(prepared, operations)
        except RapidOCRError as exc:
            if str(exc) != "The text detection result is empty":
                raise
            return OCRResult(detection_s=perf_counter()-started,
                             limitations=image.info.get("ocr_limitations", ""))
        detection_s = perf_counter()-started
        started = perf_counter()
        crops, classification = self.engine.cls_and_rotate(crops)
        recognition = self.engine.recognize_txt(crops)
        recognition_s = perf_counter()-started
        output = self.engine.build_final_output(array, detection, classification, recognition, crops, operations)
        blocks = [{"text": text, "confidence": float(score), "box": box.tolist()}
                  for text, score, box in zip(output.txts or (), output.scores or (),
                                             output.boxes if output.boxes is not None else ())]
        return OCRResult("\n".join(block["text"] for block in blocks), blocks,
                         sum(block["confidence"] for block in blocks)/len(blocks) if blocks else None,
                         detection_s, recognition_s, image.info.get("ocr_limitations", ""))

    def recognize_image(self, image: Image.Image) -> OCRResult:
        self.load_model()
        try:
            return self._recognize(image)
        except Exception as exc:
            if self.backend_id == "ort-cpu":
                raise
            previous = self.backend_id
            self.backend_id = "ov-cpu" if previous in {"ov-gpu", "ov-auto"} else "ort-cpu"
            self.engine = None
            self.load_model()
            self.device_message = f"{previous} 运行失败，回退：{exc}；" + self.device_message
            return self.recognize_image(image)

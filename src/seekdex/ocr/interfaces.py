from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from PIL import Image


@dataclass(frozen=True)
class OCRResult:
    text: str = ""
    blocks: list[dict] = field(default_factory=list)
    confidence: float | None = None
    detection_s: float = 0.0
    recognition_s: float = 0.0
    limitations: str = ""


class OCRBackend(ABC):
    backend_id: str
    model_id: str
    device: str = "CPU"

    @abstractmethod
    def load_model(self) -> None: ...

    @abstractmethod
    def recognize_image(self, image: Image.Image) -> OCRResult: ...

    def recognize_batch(self, images: Sequence[Image.Image]) -> list[OCRResult]:
        # Recognition of cropped text lines is batched by the concrete engine.
        return [self.recognize_image(image) for image in images]

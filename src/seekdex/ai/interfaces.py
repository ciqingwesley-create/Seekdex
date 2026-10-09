"""Replaceable inference and retrieval contracts."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from PIL import Image
import numpy as np


class EmbeddingBackend(ABC):
    device: str = "cpu"
    model_id: str
    embedding_dimension: int
    batch_size: int = 2

    @abstractmethod
    def load_model(self) -> None: ...

    @abstractmethod
    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray: ...

    def encode_image(self, image: Image.Image) -> np.ndarray:
        return self.encode_images([image])[0]

    def prepare_image(self, image: Image.Image):
        """Default adapters retain PIL inputs; accelerated backends return model tensors."""
        return image

    def encode_prepared(self, items) -> np.ndarray:
        return self.encode_images(items)

    @abstractmethod
    def encode_text(self, text: str) -> np.ndarray: ...


class VectorSearchBackend(ABC):
    @abstractmethod
    def refresh(self, store) -> None: ...

    @abstractmethod
    def search(self, query: np.ndarray, candidates: set[str], top_k: int,
               exclude: str | None = None) -> list[tuple[str, float]]: ...

"""Local embedding generation via Ollama (Phase 3)."""

from __future__ import annotations

from typing import Protocol

import httpx

from jarvis.config import Settings
from jarvis.core.exceptions import JarvisError
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class EmbeddingError(JarvisError):
    """Raised when local embedding generation fails or mismatches dimensions."""


class EmbeddingProvider(Protocol):
    """Abstract embedding provider consumed by memory stores."""

    @property
    def dimension(self) -> int: ...

    async def embed(self, text: str) -> list[float]: ...


class OllamaEmbeddingService:
    """Calls local Ollama ``/api/embeddings`` with strict dimension checks."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.ollama_embedding_model
        self._dimension = int(settings.embedding_dim)
        self._timeout = float(timeout_seconds)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._host, timeout=self._timeout
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def embed(self, text: str) -> list[float]:
        """Generate a normalized embedding vector matching ``self.dimension``."""
        clean = text.strip()
        if not clean:
            raise EmbeddingError("cannot embed empty text")
        try:
            response = await self._client.post(
                "/api/embeddings",
                json={"model": self._model, "prompt": clean},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"local embedding request failed: {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            raise EmbeddingError("invalid JSON from embedding endpoint") from exc

        vector = payload.get("embedding")
        if not isinstance(vector, list):
            raise EmbeddingError("embedding response missing 'embedding' list")
        if len(vector) != self._dimension:
            raise EmbeddingError(
                f"embedding dimension mismatch: expected {self._dimension}, "
                f"got {len(vector)}"
            )
        try:
            return [float(x) for x in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("embedding contains non-numeric values") from exc

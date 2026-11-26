"""Windows DPAPI-encrypted local biometric profile store (Phase 6.2).

Embeddings are encrypted per-user with Windows DPAPI, never stored in
PostgreSQL, never logged, and never included in CloudPayload. Face matching
is strictly an identification signal and never grants authorization.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time

from jarvis.config import Settings
from jarvis.core.exceptions import CredentialStoreError
from jarvis.monitoring.logging import get_logger
from jarvis.security.credentials import _dpapi_protect, _dpapi_unprotect

log = get_logger(__name__)


class BiometricStoreError(CredentialStoreError):
    """Base error for DPAPI-backed biometric storage."""


class BiometricConsentError(BiometricStoreError):
    """Raised when biometric enrollment is attempted without explicit consent."""


class DuplicateBiometricError(BiometricStoreError):
    """Raised when enrolling a duplicate profile ID or already-enrolled face."""


@dataclass(frozen=True)
class BiometricProfile:
    """Non-sensitive metadata for an enrolled local profile."""

    profile_id: str
    display_name: str
    created_at: float


@dataclass(frozen=True)
class BiometricMatch:
    """Result of a local biometric comparison (never contains raw embeddings)."""

    profile_id: str
    display_name: str
    similarity: float


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity in [-1.0, 1.0] between two embeddings."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class BiometricStore:
    """User-scoped DPAPI-encrypted local biometric embedding store."""

    def __init__(
        self,
        storage_dir: Path | None = None,
        settings: Settings | None = None,
        *,
        default_threshold: float = 0.62,
        duplicate_threshold: float = 0.85,
    ) -> None:
        if storage_dir is not None:
            self._dir = Path(storage_dir)
        elif settings is not None:
            self._dir = settings.log_dir.parent / "biometrics"
        else:
            self._dir = Path(__file__).resolve().parents[3] / "biometrics"
        self._default_threshold = float(default_threshold)
        self._duplicate_threshold = float(duplicate_threshold)

    def has_profiles(self) -> bool:
        return self._dir.exists() and any(self._dir.glob("*.bio.bin"))

    def list_profiles(self) -> tuple[BiometricProfile, ...]:
        if not self._dir.exists():
            return ()
        profiles: list[BiometricProfile] = []
        for path in sorted(self._dir.glob("*.bio.bin")):
            payload = self._read_decrypted(path)
            profiles.append(
                BiometricProfile(
                    profile_id=str(payload["profile_id"]),
                    display_name=str(payload["display_name"]),
                    created_at=float(payload["created_at"]),
                )
            )
        return tuple(profiles)

    def enroll_profile(
        self,
        profile_id: str,
        display_name: str,
        embedding: Sequence[float],
        *,
        consent_granted: bool = False,
    ) -> BiometricProfile:
        """Encrypt and persist a biometric profile after explicit consent."""
        if not consent_granted:
            raise BiometricConsentError(
                "Explicit user consent is required for biometric enrollment"
            )
        clean_id = profile_id.strip()
        if not clean_id or not all(c.isalnum() or c in ("_", "-") for c in clean_id):
            raise ValueError("profile_id must be non-empty alphanumeric/_/-")
        clean_name = display_name.strip() or clean_id
        vec = [float(v) for v in embedding]
        if not vec or not all(math.isfinite(v) for v in vec):
            raise ValueError("embedding must be a non-empty finite vector")

        target = self._profile_path(clean_id)
        if target.exists():
            raise DuplicateBiometricError(
                f"Biometric profile {clean_id!r} already exists"
            )

        # Prevent duplicate face enrollment under a different profile_id.
        if self._dir.exists():
            for path in sorted(self._dir.glob("*.bio.bin")):
                existing = self._read_decrypted(path)
                sim = cosine_similarity(vec, existing["embedding"])
                if sim >= self._duplicate_threshold:
                    raise DuplicateBiometricError(
                        "A matching biometric profile is already enrolled"
                    )

        created_at = time.time()
        record = {
            "profile_id": clean_id,
            "display_name": clean_name,
            "created_at": created_at,
            "embedding": vec,
        }
        plaintext = bytearray(json.dumps(record).encode("utf-8"))
        try:
            encrypted = _dpapi_protect(bytes(plaintext))
        except Exception as exc:  # noqa: BLE001
            raise BiometricStoreError("DPAPI biometric encryption failed") from exc
        finally:
            for idx in range(len(plaintext)):
                plaintext[idx] = 0

        self._dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypted)
        log.info("enrolled local biometric profile: %s", clean_id)
        return BiometricProfile(
            profile_id=clean_id,
            display_name=clean_name,
            created_at=created_at,
        )

    def match_profile(
        self,
        embedding: Sequence[float],
        *,
        threshold: float | None = None,
    ) -> BiometricMatch | None:
        """Compare ``embedding`` against DPAPI-encrypted profiles locally."""
        if not self.has_profiles():
            return None
        cutoff = self._default_threshold if threshold is None else float(threshold)
        vec = [float(v) for v in embedding]
        if not vec or not all(math.isfinite(v) for v in vec):
            return None

        best: BiometricMatch | None = None
        for path in sorted(self._dir.glob("*.bio.bin")):
            payload = self._read_decrypted(path)
            sim = cosine_similarity(vec, payload["embedding"])
            if sim >= cutoff and (best is None or sim > best.similarity):
                best = BiometricMatch(
                    profile_id=str(payload["profile_id"]),
                    display_name=str(payload["display_name"]),
                    similarity=sim,
                )
        return best

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a stored biometric profile. Returns True if it existed."""
        clean_id = profile_id.strip()
        if not clean_id or not all(c.isalnum() or c in ("_", "-") for c in clean_id):
            raise ValueError("profile_id must be non-empty alphanumeric/_/-")
        target = self._profile_path(clean_id)
        if not target.exists():
            return False
        target.unlink()
        log.info("deleted local biometric profile: %s", profile_id)
        return True

    def _profile_path(self, profile_id: str) -> Path:
        return self._dir / f"{profile_id}.bio.bin"

    def _read_decrypted(self, path: Path) -> dict[str, object]:
        try:
            raw = _dpapi_unprotect(path.read_bytes())
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BiometricStoreError(
                f"Failed to decrypt biometric profile {path.stem!r}"
            ) from exc

"""Unit & security tests for Phase 6.2 Windows DPAPI BiometricStore."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.security.biometrics import (
    BiometricConsentError,
    BiometricStore,
    BiometricStoreError,
    DuplicateBiometricError,
    cosine_similarity,
)


def test_biometric_store_dpapi_enroll_match_delete_and_consent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = BiometricStore(storage_dir=tmp_path, default_threshold=0.75)

    # Enrollment requires explicit consent
    with pytest.raises(BiometricConsentError):
        store.enroll_profile("user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=False)

    with caplog.at_level(logging.DEBUG):
        prof = store.enroll_profile(
            "user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=True
        )
    assert prof.profile_id == "user_1"
    assert store.has_profiles() is True
    assert len(store.list_profiles()) == 1

    # Persisted file is DPAPI ciphertext, never plaintext JSON or embedding numbers
    raw_disk = (tmp_path / "user_1.bio.bin").read_bytes()
    assert b"Tharun" not in raw_disk
    assert b"embedding" not in raw_disk
    assert "embedding" not in caplog.text.lower()

    # Duplicate profile_id or duplicate face embedding is rejected
    with pytest.raises(DuplicateBiometricError):
        store.enroll_profile("user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=True)
    with pytest.raises(DuplicateBiometricError):
        store.enroll_profile("user_2", "Clone", [0.99, 0.05, 0.0], consent_granted=True)

    # Matching & unknown face rejection
    match = store.match_profile([0.98, 0.1, 0.0])
    assert match is not None
    assert match.profile_id == "user_1"
    assert match.similarity > 0.9

    assert store.match_profile([0.0, 1.0, 0.0]) is None

    # Profile deletion
    assert store.delete_profile("user_1") is True
    assert store.delete_profile("user_1") is False
    assert store.has_profiles() is False


def test_biometric_store_handles_dpapi_failure_without_plaintext_fallback(
    tmp_path: Path,
) -> None:
    store = BiometricStore(storage_dir=tmp_path)
    with patch(
        "jarvis.security.biometrics._dpapi_protect",
        side_effect=OSError("DPAPI unavailable"),
    ):
        with pytest.raises(BiometricStoreError, match="DPAPI"):
            store.enroll_profile(
                "user_1", "Tharun", [0.5, 0.5], consent_granted=True
            )
    assert list(tmp_path.glob("*")) == []


def test_cosine_similarity_edge_cases() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

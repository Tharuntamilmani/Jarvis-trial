"""Unit tests for Phase 6.1 VisionStateMachine & core isolation."""

from __future__ import annotations

import sys

import pytest

from jarvis.vision import (
    InvalidVisionTransitionError,
    VisionState,
    VisionStateMachine,
)


def test_vision_package_never_imports_pyside6() -> None:
    assert not any(
        name.startswith("jarvis.vision") and "PySide6" in name
        for name in sys.modules
    )


def test_vision_state_machine_enforces_explicit_opt_in() -> None:
    sm = VisionStateMachine()
    assert sm.state is VisionState.DISABLED
    assert sm.camera_active is False

    # Cannot jump directly from DISABLED to ACTIVE without explicit enable -> IDLE
    with pytest.raises(InvalidVisionTransitionError):
        sm.transition(VisionState.ACTIVE)

    assert sm.transition(VisionState.IDLE) is VisionState.IDLE
    assert sm.transition(VisionState.ACTIVE) is VisionState.ACTIVE
    assert sm.camera_active is True

    # Locking suspends active capture; cannot auto-unlock straight to ACTIVE
    assert sm.transition(VisionState.LOCKED) is VisionState.LOCKED
    assert sm.camera_active is False
    with pytest.raises(InvalidVisionTransitionError):
        sm.transition(VisionState.ACTIVE)

    assert sm.transition(VisionState.IDLE) is VisionState.IDLE
    assert sm.transition(VisionState.DISABLED) is VisionState.DISABLED

"""Identifier normalisation at the persistence boundary (Stage 2.8)."""

from __future__ import annotations

import uuid

import pytest

from jarvis.conversations.errors import InvalidIdentifierError
from jarvis.conversations.models import to_hex, to_uuid


def test_accepts_existing_32_char_hex_form() -> None:
    raw = uuid.uuid4().hex
    assert len(raw) == 32
    assert to_uuid(raw) == uuid.UUID(raw)


def test_accepts_dashed_uuid() -> None:
    value = uuid.uuid4()
    assert to_uuid(str(value)) == value


def test_accepts_uuid_instance() -> None:
    value = uuid.uuid4()
    assert to_uuid(value) is value


def test_to_hex_round_trip() -> None:
    value = uuid.uuid4()
    assert to_hex(value) == value.hex
    assert to_hex(str(value)) == value.hex


def test_to_hex_passes_none_through() -> None:
    assert to_hex(None) is None


@pytest.mark.parametrize(
    "value",
    ["", "   ", "not-a-uuid", "0" * 31, "z" * 32, "12345"],
)
def test_rejects_malformed_identifiers(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        to_uuid(value)


def test_rejects_non_string_identifier() -> None:
    with pytest.raises(InvalidIdentifierError):
        to_uuid(1234)  # type: ignore[arg-type]


def test_error_message_is_bounded() -> None:
    with pytest.raises(InvalidIdentifierError) as excinfo:
        to_uuid("x" * 400)
    assert len(str(excinfo.value)) < 120

"""Event contract tests (serialization must match the public contract)."""

from __future__ import annotations

from jarvis.core.events import ChatToken, ServiceStatusChanged


def test_as_dict_excludes_timestamp() -> None:
    event = ServiceStatusChanged(
        service="engine", status="healthy", detail="running"
    )

    payload = event.as_dict()

    assert payload == {
        "service": "engine",
        "status": "healthy",
        "detail": "running",
    }
    assert "timestamp" not in payload


def test_as_dict_preserves_event_payload_fields() -> None:
    event = ChatToken(request_id="req-1", seq=3, text="hello")

    assert event.as_dict() == {
        "request_id": "req-1",
        "seq": 3,
        "text": "hello",
    }

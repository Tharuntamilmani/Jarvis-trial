"""Service registry tests."""

from __future__ import annotations

import pytest

from jarvis.core.exceptions import ServiceError, ServiceNotRegisteredError
from jarvis.core.service_registry import ServiceRegistry


@pytest.mark.asyncio
async def test_lazy_async_resolution() -> None:
    registry = ServiceRegistry()
    created: list[int] = []

    async def factory() -> int:
        created.append(1)
        return 42

    registry.register("answer", factory)
    assert registry.names == ("answer",)
    assert not registry.is_resolved("answer")
    assert await registry.get("answer") == 42
    assert await registry.get("answer") == 42
    assert created == [1]  # constructed once


def test_duplicate_registration_rejected() -> None:
    registry = ServiceRegistry()
    registry.register("a", lambda: 1)
    with pytest.raises(ServiceError):
        registry.register("a", lambda: 2)
    registry.register("a", lambda: 2, replace=True)


def test_unregistered_lookup_raises() -> None:
    registry = ServiceRegistry()
    with pytest.raises(ServiceNotRegisteredError):
        registry.get_sync("missing")


@pytest.mark.asyncio
async def test_register_instance() -> None:
    registry = ServiceRegistry()
    registry.register_instance("svc", {"k": "v"})
    assert (await registry.get("svc")) == {"k": "v"}

"""Security foundations tests: credentials, allowlist, permissions,
approval (fail-closed), audit."""

from __future__ import annotations

import asyncio
import sys

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import ApprovalDecided
from jarvis.core.exceptions import (
    ApprovalDeniedError,
    CredentialStoreError,
)
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditService
from jarvis.security.credentials import CredentialStore
from jarvis.security.permissions import (
    Capability,
    PermissionPolicy,
    RiskLevel,
    ToolPermission,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows DPAPI required"
)


# -- Credentials ------------------------------------------------------------------


def test_credential_roundtrip(settings, tmp_path) -> None:
    store = CredentialStore(settings)
    store.store_secret("test_key", "s3cret-value")
    assert store.load_secret("test_key") == "s3cret-value"
    assert "test_key" in store.list_secret_names()
    assert store.delete_secret("test_key")
    assert store.load_secret("test_key") is None
    assert not store.delete_secret("test_key")


def test_credential_invalid_name(settings) -> None:
    store = CredentialStore(settings)
    with pytest.raises(ValueError):
        store.store_secret("bad name!", "x")


def test_credential_tampered_blob(settings, tmp_path) -> None:
    store = CredentialStore(settings)
    store.store_secret("tamper", "value")
    blob = store._dir / "tamper.bin"
    blob.write_bytes(b"garbage-not-encrypted")
    with pytest.raises(CredentialStoreError):
        store.load_secret("tamper")
    store.delete_secret("tamper")


# -- Allowlist ----------------------------------------------------------------------


def test_allowlist_exact_and_pattern() -> None:
    al = CommandAllowlist()
    al.add_exact("Get-Process")
    al.add_pattern("git status*")
    assert al.is_allowed("get-process")
    assert al.is_allowed("git status")
    assert al.is_allowed("git status --short")
    assert not al.is_allowed("Remove-Item C:\\")
    assert al.remove("Get-Process")
    assert not al.is_allowed("Get-Process")


# -- Permissions ----------------------------------------------------------------------


def test_permission_default_deny_dangerous() -> None:
    policy = PermissionPolicy.default_policy()
    dangerous = ToolPermission(
        "shell", RiskLevel.DANGEROUS, frozenset({Capability.PROCESS})
    )
    safe = ToolPermission(
        "clipboard_read", RiskLevel.SAFE, frozenset({Capability.CLIPBOARD})
    )
    assert not policy.is_allowed(dangerous)   # default deny
    assert policy.is_allowed(safe)


def test_permission_explicit_grant_overrides() -> None:
    policy = PermissionPolicy.default_policy()
    policy.granted_tools.add("shell")
    policy.granted_capabilities.add(Capability.PROCESS)
    shell = ToolPermission(
        "shell", RiskLevel.DANGEROUS, frozenset({Capability.PROCESS})
    )
    assert policy.is_allowed(shell)
    policy.denied_tools.add("shell")
    assert not policy.is_allowed(shell)


# -- Approval (fail-closed) ------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_denied_without_responder(settings) -> None:
    bus = EventBus()
    service = ApprovalService(settings, bus)
    decided: list[ApprovalDecided] = []
    bus.subscribe(ApprovalDecided, decided.append)
    with pytest.raises(ApprovalDeniedError):
        await service.request("run_command")
    assert len(decided) == 1
    assert decided[0].approved is False
    assert decided[0].decided_by == "fail_closed_default"


@pytest.mark.asyncio
async def test_approval_with_responder_grant(settings) -> None:
    bus = EventBus()
    service = ApprovalService(settings, bus)

    async def responder(event) -> bool:
        return True

    service.add_responder(responder)
    await service.request("run_command")  # must not raise


@pytest.mark.asyncio
async def test_approval_timeout_denies(settings) -> None:
    bus = EventBus()
    service = ApprovalService(settings, bus)

    async def slow_responder(event) -> bool:
        await asyncio.sleep(10)
        return True

    service.add_responder(slow_responder)
    with pytest.raises(ApprovalDeniedError):
        await service.request("run_command", timeout=0.05)


@pytest.mark.asyncio
async def test_approval_direct_resolution(settings) -> None:
    bus = EventBus()
    service = ApprovalService(settings, bus)

    class NeverResponder:
        pass

    async def hanging_responder(event) -> bool:
        await asyncio.sleep(10)
        return False

    service.add_responder(hanging_responder)
    task = asyncio.ensure_future(service.request("run_command", timeout=5))

    async def wait_pending() -> None:
        for _ in range(50):
            if service.pending_count:
                return
            await asyncio.sleep(0.02)

    await wait_pending()
    request_id = next(iter(service._pending))
    service.resolve_pending(request_id, False)
    with pytest.raises(ApprovalDeniedError):
        await task


# -- Audit -----------------------------------------------------------------------------


def test_audit_append_only(settings) -> None:
    bus = EventBus()
    audit = AuditService(settings, bus)
    audit.record("open_app", result="ok", approved_by="user", detail={"app": "notepad"})
    audit.record("run_command", result="denied")
    entries = audit.read_all()
    assert [e["action"] for e in entries] == ["open_app", "run_command"]
    assert entries[0]["seq"] == 1
    assert entries[1]["seq"] == 2
    assert entries[0]["detail"] == {"app": "notepad"}

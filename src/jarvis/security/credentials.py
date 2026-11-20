"""Windows DPAPI-protected credential store.

Secrets (API keys etc.) are encrypted per-user with Windows DPAPI
(crypt32 CryptProtectData) and persisted as opaque ``.bin`` blobs under the
application data directory. This is a *real* Phase 1 implementation: it is
used by later phases to hold provider API keys without touching ``.env``.

Non-Windows platforms raise ``CredentialStoreError`` (the project targets
Windows 11 only).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from pathlib import Path

from jarvis.config import Settings
from jarvis.core.exceptions import CredentialStoreError
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_from_bytes(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


class CredentialStore:
    """Store and retrieve DPAPI-encrypted secrets by name."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._dir = self._secrets_dir(settings)

    @staticmethod
    def _secrets_dir(settings: Settings | None) -> Path:
        if settings is not None:
            base = settings.log_dir.parent
        else:
            base = Path(__file__).resolve().parents[3]
        return base / "secrets"

    # -- Public API -----------------------------------------------------------

    def store_secret(self, name: str, value: str) -> None:
        """Encrypt and persist ``value`` under ``name``."""
        if not name or not name.isidentifier():
            raise ValueError("secret name must be a valid identifier")
        encrypted = _dpapi_protect(value.encode("utf-8"))
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{name}.bin"
        target.write_bytes(encrypted)
        log.info("stored secret: %s", name)

    def load_secret(self, name: str) -> str | None:
        """Return the decrypted secret, or ``None`` if not stored."""
        target = self._dir / f"{name}.bin"
        if not target.exists():
            return None
        try:
            decrypted = _dpapi_unprotect(target.read_bytes())
            return decrypted.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise CredentialStoreError(f"failed to decrypt secret {name!r}") from exc

    def delete_secret(self, name: str) -> bool:
        """Delete a stored secret. Returns True if it existed."""
        target = self._dir / f"{name}.bin"
        if not target.exists():
            return False
        target.unlink()
        log.info("deleted secret: %s", name)
        return True

    def list_secret_names(self) -> tuple[str, ...]:
        if not self._dir.exists():
            return ()
        return tuple(sorted(p.stem for p in self._dir.glob("*.bin")))


# -- DPAPI wrappers ------------------------------------------------------------

# Explicit signatures: without them ctypes mis-converts the variadic-style
# call and CryptProtectData silently fails.
_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32

_PCRYPT_BLOB = ctypes.POINTER(_DATA_BLOB)

_crypt32.CryptProtectData.restype = ctypes.c_int
_crypt32.CryptProtectData.argtypes = [
    _PCRYPT_BLOB,       # pDataIn
    wt.LPCWSTR,         # szDataDescr
    _PCRYPT_BLOB,       # pOptionalEntropy
    wt.DWORD,           # pvReserved
    ctypes.c_void_p,    # pPromptStruct
    wt.DWORD,           # dwFlags
    _PCRYPT_BLOB,       # pDataOut
]
_crypt32.CryptUnprotectData.restype = ctypes.c_int
_crypt32.CryptUnprotectData.argtypes = [
    _PCRYPT_BLOB,       # pDataIn
    ctypes.POINTER(wt.LPCWSTR),  # ppszDataDescr
    _PCRYPT_BLOB,       # pOptionalEntropy
    wt.DWORD,           # pvReserved
    ctypes.c_void_p,    # pPromptStruct
    wt.DWORD,           # dwFlags
    _PCRYPT_BLOB,       # pDataOut
]


def _dpapi_protect(data: bytes) -> bytes:
    out = _DATA_BLOB()
    in_blob = _blob_from_bytes(data)
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,  # description
        None,  # entropy
        0,
        None,  # prompt
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise CredentialStoreError("CryptProtectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _dpapi_unprotect(data: bytes) -> bytes:
    out = _DATA_BLOB()
    in_blob = _blob_from_bytes(data)
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        0,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise CredentialStoreError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


__all__ = ["CredentialStore"]


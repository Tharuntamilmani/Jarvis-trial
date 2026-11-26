"""Windows named-mutex single-instance enforcement (Phase 8.5)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

_ERROR_ALREADY_EXISTS = 183


class SingleInstanceLock:
    """Process-level named mutex preventing duplicate JARVIS instances."""

    def __init__(self, mutex_name: str = "Local\\JARVIS_AI_ASSISTANT_MUTEX") -> None:
        self._name = mutex_name
        self._handle: wt.HANDLE | None = None
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
        self._kernel32.CreateMutexW.restype = wt.HANDLE
        self._kernel32.ReleaseMutex.argtypes = [wt.HANDLE]
        self._kernel32.ReleaseMutex.restype = wt.BOOL
        self._kernel32.CloseHandle.argtypes = [wt.HANDLE]
        self._kernel32.CloseHandle.restype = wt.BOOL

    def acquire(self) -> bool:
        """Attempt to acquire the named mutex. Returns False if another instance holds it."""
        if self._handle is not None:
            return True
        self._kernel32.SetLastError(0)
        handle = self._kernel32.CreateMutexW(None, True, self._name)
        if not handle:
            return False
        if self._kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Release and close the named mutex cleanly."""
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                self._kernel32.ReleaseMutex(handle)
            finally:
                self._kernel32.CloseHandle(handle)

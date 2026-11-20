"""Command allowlist primitive.

Phase 7's executor will run shell-level actions only when the command is
allowlisted here or explicitly approved. This module implements the matching
semantics now (exact command or vetted wildcard pattern) so the security
contract is fixed and tested early.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


@dataclass
class CommandAllowlist:
    """Exact commands plus limited wildcard patterns (fnmatch-style)."""

    exact: set[str] = field(default_factory=set)
    patterns: set[str] = field(default_factory=set)

    def add_exact(self, command: str) -> None:
        self.exact.add(command.strip())

    def add_pattern(self, pattern: str) -> None:
        self.patterns.add(pattern.strip())

    def remove(self, entry: str) -> bool:
        if entry in self.exact:
            self.exact.discard(entry)
            return True
        if entry in self.patterns:
            self.patterns.discard(entry)
            return True
        return False

    def is_allowed(self, command: str) -> bool:
        """Return True when the command matches exactly or a pattern.

        Matching is case-insensitive and whitespace-normalised.
        """
        normalized = " ".join(command.strip().lower().split())
        if normalized in {c.lower() for c in self.exact}:
            return True
        return any(
            fnmatch.fnmatchcase(normalized, p.lower()) for p in self.patterns
        )

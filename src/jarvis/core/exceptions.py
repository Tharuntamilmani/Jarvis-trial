"""Core exception hierarchy for JARVIS.

Every subsystem maps its failures onto this hierarchy so callers never deal
with vendor-specific exception types.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base class for all JARVIS errors."""


class ConfigurationError(JarvisError):
    """Invalid or missing configuration."""


class ServiceError(JarvisError):
    """A core service failed to start, stop, or report health."""


class ServiceNotRegisteredError(ServiceError):
    """Requested service is not registered."""


class DatabaseError(JarvisError):
    """Database connectivity or query failure."""


class SecurityError(JarvisError):
    """Security subsystem failure (credentials, policy, audit)."""


class CredentialStoreError(SecurityError):
    """Failed to store or retrieve a secret."""


class ApprovalDeniedError(SecurityError):
    """An action was denied — by the user or by the fail-closed default."""


class KillSwitchActiveError(SecurityError):
    """Operation refused because the emergency stop is engaged."""

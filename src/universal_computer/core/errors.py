"""Exception hierarchy for the Universal Computer Control engine.

Every error inherits from :class:`UniversalComputerError` so callers can catch
the whole family with a single ``except``. The MCP tool layer maps these onto
structured JSON error payloads instead of crashing the server.
"""

from __future__ import annotations


class UniversalComputerError(Exception):
    """Base class for all errors raised by the computer-control engine."""


class ConfigurationError(UniversalComputerError):
    """Raised when configuration loading or validation fails."""


class BackendUnavailableError(UniversalComputerError):
    """Raised when no backend capable of performing an operation is available."""

    def __init__(self, capability: str, reason: str = "") -> None:
        self.capability = capability
        self.reason = reason
        message = f"No available backend for capability '{capability}'"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)


class ObservationError(UniversalComputerError):
    """Raised when an observation cannot be produced."""


class VisionUnavailableError(UniversalComputerError):
    """Raised when no OCR/vision provider can serve a request."""


class TargetResolutionError(UniversalComputerError):
    """Raised when a click/type target cannot be resolved to screen coordinates."""

    def __init__(self, target: object, reason: str = "") -> None:
        self.target = target
        self.reason = reason
        message = f"Could not resolve target {target!r}"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)


class ActionFailedError(UniversalComputerError):
    """Raised inside an attempt when a physical/semantic action cannot be done."""


class InputNotSupportedError(ActionFailedError):
    """Raised when the input backend cannot express the requested input."""


class SecurityViolationError(UniversalComputerError):
    """Raised when an action is denied by the security policy."""


class RateLimitExceededError(SecurityViolationError):
    """Raised when the action rate limit is exhausted."""


class EmergencyStopError(UniversalComputerError):
    """Raised when an action is attempted while the emergency stop is engaged."""


class WindowNotFoundError(UniversalComputerError):
    """Raised when a window matching a query does not exist."""

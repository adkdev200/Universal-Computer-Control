"""Security policy: security modes, allowlists, rate limiting, confirmations.

Modes:
- ``permissive``: everything allowed (still rate-limited). For trusted VMs.
- ``standard`` (default): ``run_command`` requires the command to be listed in
  ``security.allowed_commands``; ``launch_application`` is allowed unless a
  non-empty ``allowed_applications`` list excludes the app; dangerous actions
  (see ``confirmation_required``) additionally need an explicit ``confirm=true``.
- ``strict``: shell execution is disabled unless explicitly allowlisted, and
  launching requires an exact allowlist match.
"""

from __future__ import annotations

import fnmatch
import shlex
import time
from collections import deque
from collections.abc import Callable

from pydantic import BaseModel

from universal_computer.config import SecurityConfig

ConfirmationHook = Callable[[str], bool]


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str | None = None


class RateLimiter:
    """Simple sliding-window rate limiter (max N actions per minute)."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max(1, max_per_minute)
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > 60.0:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


class SecurityPolicy:
    """Enforces the configured security rules for the engine."""

    def __init__(self, cfg: SecurityConfig) -> None:
        self.cfg = cfg
        self._rate_limiter = RateLimiter(cfg.max_actions_per_minute)
        self._confirmation_hook: ConfirmationHook | None = None

    # -- configuration -----------------------------------------------------
    @property
    def mode(self) -> str:
        return self.cfg.mode

    def set_confirmation_hook(self, hook: ConfirmationHook | None) -> None:
        """Register a programmatic confirmation callback (e.g. a UI prompt)."""
        self._confirmation_hook = hook

    # -- checks ------------------------------------------------------------
    def check_rate(self, action: str) -> PolicyDecision:
        if self._rate_limiter.allow():
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Rate limit exceeded (max {self.cfg.max_actions_per_minute}/min); "
                f"action '{action}' denied"
            ),
        )

    def _first_token(self, command: str) -> str:
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = command.split()
        token = parts[0] if parts else command
        return token.strip().strip('"').strip("'")

    def _command_allowed(self, command: str) -> bool:
        for pattern in self.cfg.allowed_commands:
            if fnmatch.fnmatch(command, pattern):
                return True
            if fnmatch.fnmatch(self._first_token(command), pattern):
                return True
            base = self._first_token(command).split("/")[-1].split("\\")[-1]
            pattern_base = pattern.split("/")[-1].split("\\")[-1]
            if base and base == pattern_base:
                return True
        return False

    def check_command(self, command: str) -> PolicyDecision:
        """Check whether ``computer.run_command`` may execute ``command``."""
        if not command.strip():
            return PolicyDecision(allowed=False, reason="Empty command")
        if self.cfg.mode == "permissive":
            return PolicyDecision(allowed=True)
        if self.cfg.mode == "strict" and not self.cfg.allow_shell:
            if not self._command_allowed(command):
                return PolicyDecision(
                    allowed=False,
                    reason="Strict mode: command is not in security.allowed_commands",
                )
            return PolicyDecision(allowed=True)
        if self.cfg.allow_shell:
            return PolicyDecision(allowed=True)
        if not self.cfg.allowed_commands:
            return PolicyDecision(
                allowed=False,
                reason=(
                    "computer.run_command is denied: security.allowed_commands is "
                    "empty. Add commands to the allowlist or set "
                    "security.mode=permissive."
                ),
            )
        if not self._command_allowed(command):
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Command not allowed: {self._first_token(command)!r} is not in "
                    "security.allowed_commands"
                ),
            )
        return PolicyDecision(allowed=True)

    def check_application(self, app: str) -> PolicyDecision:
        """Check whether ``computer.launch_application`` may launch ``app``."""
        token = self._first_token(app)
        name = token.split("/")[-1].split("\\")[-1]
        if self.cfg.mode == "permissive":
            return PolicyDecision(allowed=True)
        if not self.cfg.allowed_applications:
            if self.cfg.mode == "strict":
                return PolicyDecision(
                    allowed=False,
                    reason="Strict mode: security.allowed_applications is empty",
                )
            return PolicyDecision(allowed=True)
        for pattern in self.cfg.allowed_applications:
            if fnmatch.fnmatch(app, pattern) or fnmatch.fnmatch(name, pattern):
                return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=f"Application {name!r} is not in security.allowed_applications",
        )

    def requires_confirmation(self, action: str) -> bool:
        return action in self.cfg.confirmation_required

    def check_confirmation(self, action: str, confirmed: bool) -> PolicyDecision:
        """Enforce confirmations, honouring an optional programmatic hook."""
        if not self.requires_confirmation(action):
            return PolicyDecision(allowed=True)
        if confirmed:
            return PolicyDecision(allowed=True)
        if self._confirmation_hook is not None and self._confirmation_hook(action):
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Action '{action}' requires confirmation; re-invoke with "
                "confirm=true to proceed"
            ),
        )

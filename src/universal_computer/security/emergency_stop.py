"""Global emergency stop.

The stop state lives in three synchronized places so it works from any entry
point:

1. an in-process ``threading.Event`` (fast checks),
2. a stop-flag file (``<home>/state/EMERGENCY_STOP``) so external processes or
   a watchdog can trigger/observe the stop,
3. the ``computer.emergency_stop`` / ``computer.reset_emergency_stop`` tools.

While engaged, the engine raises :class:`EmergencyStopError` before resolving
and before every execution attempt. Physical backends are only ever invoked
through the engine, so the stop cannot be bypassed by the MCP API surface.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from universal_computer.core.errors import EmergencyStopError
from universal_computer.logging import get_logger

logger = get_logger("security.stop")


class EmergencyStop:
    """Cooperative global kill switch for all automation."""

    FILE_NAME = "EMERGENCY_STOP"
    _FILE_CHECK_INTERVAL_S = 1.0

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._file = self._state_dir / self.FILE_NAME
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._last_file_check = 0.0
        self._file_present = False
        self._reason = ""
        if self._file.exists():  # a stop left over from a previous session
            self._reason = "stop flag present at startup"
            self._event.set()
            self._file_present = True

    @property
    def flag_path(self) -> Path:
        return self._file

    @property
    def reason(self) -> str:
        return self._reason

    def trigger(self, reason: str = "manual") -> None:
        """Engage the emergency stop everywhere."""
        with self._lock:
            self._reason = reason or "manual"
            self._event.set()
            self._file_present = True
            try:
                self._state_dir.mkdir(parents=True, exist_ok=True)
                self._file.write_text(
                    f"emergency stop engaged at "
                    f"{datetime.now(UTC).isoformat()} reason={reason}\n",
                    encoding="utf-8",
                )
            except OSError as exc:  # pragma: no cover - unwritable disk
                logger.error("Could not persist emergency stop flag: %s", exc)
        logger.critical("EMERGENCY STOP engaged: %s", reason)

    def reset(self) -> None:
        """Clear the stop (does not undo any action already performed)."""
        with self._lock:
            self._event.clear()
            self._file_present = False
            self._reason = ""
            try:
                self._file.unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover
                logger.error("Could not remove emergency stop flag: %s", exc)
        logger.warning("Emergency stop reset")

    @property
    def triggered(self) -> bool:
        if self._event.is_set():
            return True
        now = time.monotonic()
        if now - self._last_file_check >= self._FILE_CHECK_INTERVAL_S:
            self._last_file_check = now
            self._file_present = self._file.exists()
            if self._file_present and not self._reason:
                self._reason = "stop flag file present"
        return self._file_present

    def check(self) -> None:
        """Raise :class:`EmergencyStopError` if the stop is engaged."""
        if self.triggered:
            raise EmergencyStopError(
                f"Emergency stop is engaged ({self._reason or 'unknown reason'}). "
                "Call computer.reset_emergency_stop to clear it."
            )

"""Application launch backends (never command execution).

Launching is deliberately separated from ``run_command``: launching starts a
GUI application detached, whereas command execution is a shell-level,
security-sensitive operation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from universal_computer.backends.base import ApplicationBackend, Cap
from universal_computer.core.errors import ActionFailedError
from universal_computer.logging import get_logger

logger = get_logger("backends.applications")


class WindowsApplicationLauncher(ApplicationBackend):
    """Launch GUI applications on Windows (os.startfile / start)."""

    name = "windows-launch"
    platform = "windows"
    priority = 50

    def _probe(self) -> bool:
        return sys.platform == "win32"

    def capabilities(self) -> set[str]:
        return {Cap.LAUNCH}

    def launch(self, command: str) -> int | None:
        command = command.strip()
        if not command:
            raise ActionFailedError("empty launch command")
        candidate = Path(command)
        if candidate.is_file() and candidate.suffix.lower() in {".exe", ".lnk", ".bat", ".cmd"}:
            os.startfile(str(candidate))  # noqa: S606 - intended Windows behaviour
            return None
        resolved = shutil.which(command)
        if resolved:
            proc = subprocess.Popen([resolved], close_fds=True)  # noqa: S603
            return proc.pid
        # Delegate to the shell association (URLs, documents, Start-menu entries).
        os.startfile(command)  # noqa: S606
        return None


class UnixApplicationLauncher(ApplicationBackend):
    """Launch GUI applications on Linux (detached process or xdg-open)."""

    name = "unix-launch"
    platform = "linux"
    priority = 50

    def _probe(self) -> bool:
        return sys.platform in {"linux", "darwin"}

    def capabilities(self) -> set[str]:
        return {Cap.LAUNCH}

    def launch(self, command: str) -> int | None:
        command = command.strip()
        if not command:
            raise ActionFailedError("empty launch command")
        candidate = Path(command)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            proc = subprocess.Popen(  # noqa: S603
                [str(candidate)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        resolved = shutil.which(command)
        if resolved:
            proc = subprocess.Popen(  # noqa: S603
                [resolved],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        # Desktop entries (.desktop files) and documents: let xdg-open handle it.
        xdg_open = shutil.which("xdg-open")
        if xdg_open:
            proc = subprocess.Popen(  # noqa: S603
                [xdg_open, command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        raise ActionFailedError(
            f"cannot launch {command!r}: not an executable, not on PATH, and xdg-open missing"
        )

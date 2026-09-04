"""Clipboard backends: pyperclip, PowerShell (Windows), xclip/xsel (Linux).

Clipboard text is never logged; only lengths are recorded.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from universal_computer.backends.base import Cap, ClipboardBackend
from universal_computer.logging import get_logger

logger = get_logger("backends.clipboard")


class PyperclipClipboardBackend(ClipboardBackend):
    """Cross-platform clipboard via the optional ``pyperclip`` package."""

    name = "clipboard-pyperclip"
    platform = "any"
    priority = 50

    def _probe(self) -> bool:
        try:
            import pyperclip  # noqa: PLC0415

            pyperclip.paste()  # may require a display/clipboard service
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.CLIPBOARD}

    def get_text(self) -> str | None:
        import pyperclip  # noqa: PLC0415

        return pyperclip.paste()

    def set_text(self, text: str) -> bool:
        import pyperclip  # noqa: PLC0415

        pyperclip.copy(text)
        return True


class PowerShellClipboardBackend(ClipboardBackend):
    """Windows clipboard via PowerShell (no extra dependencies)."""

    name = "clipboard-powershell"
    platform = "windows"
    priority = 40

    def _probe(self) -> bool:
        if sys.platform != "win32":
            self._probe_error = "not Windows"
            return False
        if not shutil.which("powershell"):
            self._probe_error = "powershell not found"
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.CLIPBOARD}

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    def get_text(self) -> str | None:
        try:
            result = self._run("Get-Clipboard -Raw")
            if result.returncode == 0:
                return result.stdout.rstrip("\r\n")
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Get-Clipboard failed: %s", exc)
        return None

    def set_text(self, text: str) -> bool:
        try:
            # Base64 transport avoids any escaping issues with quotes/newlines.
            import base64  # noqa: PLC0415

            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            script = (
                "$data = [System.Text.Encoding]::UTF8.GetString("
                f"[Convert]::FromBase64String('{encoded}')); "
                "Set-Clipboard -Value $data"
            )
            result = self._run(script)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Set-Clipboard failed: %s", exc)
            return False


class XClipboardBackend(ClipboardBackend):
    """Linux/X11 clipboard via ``xclip`` or ``xsel``."""

    name = "clipboard-x11"
    platform = "linux"
    priority = 40

    def __init__(self) -> None:
        super().__init__()
        self._xclip = shutil.which("xclip")
        self._xsel = shutil.which("xsel")

    def _probe(self) -> bool:
        if sys.platform != "linux":
            self._probe_error = "not Linux"
            return False
        if not self._xclip and not self._xsel:
            self._probe_error = (
                "neither xclip nor xsel found; install one (e.g. 'sudo apt install xclip')"
            )
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.CLIPBOARD}

    def get_text(self) -> str | None:
        try:
            if self._xclip:
                result = subprocess.run(  # noqa: S603
                    [self._xclip, "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout
            if self._xsel:
                result = subprocess.run(  # noqa: S603
                    [self._xsel, "--clipboard", "--output"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("clipboard read failed: %s", exc)
        return None

    def set_text(self, text: str) -> bool:
        try:
            if self._xclip:
                result = subprocess.run(  # noqa: S603
                    [self._xclip, "-selection", "clipboard", "-i"],
                    input=text,
                    text=True,
                    timeout=5,
                    check=False,
                )
                return result.returncode == 0
            if self._xsel:
                result = subprocess.run(  # noqa: S603
                    [self._xsel, "--clipboard", "--input"],
                    input=text,
                    text=True,
                    timeout=5,
                    check=False,
                )
                return result.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("clipboard write failed: %s", exc)
        return False

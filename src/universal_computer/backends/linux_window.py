"""Linux window management via ``wmctrl`` / ``xdotool`` (X11).

Both tools are optional system packages; if neither is present the backend
reports itself unavailable and window management falls back to an
informational error. Wayland sessions: ``wmctrl``/``xdotool`` generally do not
work; the backend probes the session type and degrades gracefully (documented
in the troubleshooting guide).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from universal_computer.backends.base import Cap, WindowManagementBackend
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.models.window import WindowInfo, WindowState

logger = get_logger("backends.linux_window")

_WMCTRL_LINE = re.compile(r"^(?P<id>0x[0-9a-fA-F]+)\s+(?P<desk>-?\d+)\s+(?P<pid>\d+)\s+\S+\s*(?P<title>.*)$")
_GEO_RE = re.compile(r"window\s+0x[0-9a-fA-F]+\s+geometry\s+(?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+)")


class LinuxWindowBackend(WindowManagementBackend):
    """X11 window management via wmctrl/xdotool (priority 70)."""

    name = "linux-window"
    platform = "linux"
    priority = 70

    def __init__(self) -> None:
        super().__init__()
        self._wmctrl = shutil.which("wmctrl")
        self._xdotool = shutil.which("xdotool")

    def _probe(self) -> bool:
        import sys  # noqa: PLC0415

        if sys.platform != "linux":
            self._probe_error = "not Linux"
            return False
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            self._probe_error = (
                "Wayland session without XWayland DISPLAY; wmctrl/xdotool cannot manage windows"
            )
            return False
        if not self._wmctrl and not self._xdotool:
            self._probe_error = (
                "neither wmctrl nor xdotool found; install them (e.g. "
                "'sudo apt install wmctrl xdotool') for window management"
            )
            return False
        try:
            self.list_windows()
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.WINDOWS}

    # -- helpers -----------------------------------------------------------------
    def _run(self, args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _geometry(self, hex_id: str) -> BoundingBox | None:
        if not self._xdotool:
            return None
        try:
            result = self._run([self._xdotool, "getwindowgeometry", "--shell", hex_id])
            values: dict[str, int] = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    try:
                        values[key.strip()] = int(value.strip())
                    except ValueError:
                        continue
            if {"X", "Y", "WIDTH", "HEIGHT"} <= values.keys():
                return BoundingBox.from_xywh(values["X"], values["Y"], values["WIDTH"], values["HEIGHT"])
            match = _GEO_RE.search(result.stdout)
            if match:
                return BoundingBox.from_ltrb(
                    int(match.group("x")),
                    int(match.group("y")),
                    int(match.group("x")) + int(match.group("w")),
                    int(match.group("y")) + int(match.group("h")),
                )
        except Exception:  # noqa: BLE001
            pass
        return None

    def _active_hex(self) -> str | None:
        if self._xdotool:
            try:
                result = self._run([self._xdotool, "getactivewindow"])
                if result.returncode == 0 and result.stdout.strip():
                    return f"0x{int(result.stdout.strip()):x}"
            except Exception:  # noqa: BLE001
                pass
        if self._wmctrl:
            try:
                result = self._run([self._wmctrl, "-l"])
                for line in result.stdout.splitlines():
                    if " _NET_ACTIVE_WINDOW" in line or ":*" in line:
                        match = _WMCTRL_LINE.match(line)
                        if match:
                            return match.group("id")
            except Exception:  # noqa: BLE001
                pass
        return None

    def _window_info(self, hex_id: str, title: str, pid: int | None) -> WindowInfo:
        return WindowInfo(
            title=title,
            application=None,
            process_id=pid,
            handle=hex_id,
            bbox=self._geometry(hex_id),
            state=WindowState.NORMAL,
            is_active=(hex_id == self._active_hex()),
        )

    # -- WindowManagementBackend API -------------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        if not self._wmctrl:
            raise RuntimeError("wmctrl is not installed; cannot list windows")
        result = self._run([self._wmctrl, "-l", "-p"])
        if result.returncode != 0:
            raise RuntimeError(f"wmctrl failed: {result.stderr.strip()}")
        windows: list[WindowInfo] = []
        for line in result.stdout.splitlines():
            match = _WMCTRL_LINE.match(line)
            if not match:
                continue
            pid = int(match.group("pid")) if match.group("pid").isdigit() else None
            app = None
            if pid:
                try:
                    app = open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace").read().strip() or None
                except OSError:
                    app = None
            info = self._window_info(match.group("id"), match.group("title").strip(), pid)
            info.application = app
            windows.append(info)
        return windows

    def get_active_window(self) -> WindowInfo | None:
        active_hex = self._active_hex()
        if not active_hex:
            return None
        for window in self.list_windows():
            if window.handle == active_hex:
                return window
        return None

    def _find_window(self, window: WindowInfo) -> WindowInfo | None:
        if window.handle:
            for candidate in self.list_windows():
                if candidate.handle == window.handle:
                    return candidate
        for candidate in self.list_windows():
            if candidate.title == window.title:
                return candidate
        return None

    def focus_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._wmctrl:
            result = self._run([self._wmctrl, "-i", "-a", target.handle])
            return result.returncode == 0
        if self._xdotool:
            result = self._run([self._xdotool, "windowactivate", target.handle])
            return result.returncode == 0
        return False

    def minimize_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None or not self._xdotool:
            return False
        return self._run([self._xdotool, "windowminimize", target.handle]).returncode == 0

    def maximize_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None or not self._wmctrl:
            return False
        result = self._run(
            [self._wmctrl, "-i", "-r", target.handle, "-b", "add,maximized_vert,maximized_horz"]
        )
        return result.returncode == 0

    def restore_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None or not self._wmctrl:
            return False
        result = self._run(
            [self._wmctrl, "-i", "-r", target.handle, "-b", "remove,maximized_vert,maximized_horz"]
        )
        return result.returncode == 0

    def close_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None or not self._wmctrl:
            return False
        # -ic sends a graceful WM_CLOSE; modal confirmations stay visible.
        return self._run([self._wmctrl, "-i", "-c", target.handle]).returncode == 0

"""Screenshot backends and screen-geometry helpers.

Two implementations are provided:

- :class:`MssScreenshotBackend` (priority 60): fast, multi-monitor capture via
  the optional ``mss`` package. Preferred when installed.
- :class:`PillowScreenshotBackend` (priority 40): Pillow ``ImageGrab`` with
  PyAutoGUI as a last-resort fallback.

All coordinates use the **virtual-desktop pixel space** (the union of all
monitors, which may include negative coordinates). On Windows the process is
made DPI-aware at import of the Windows backends so screenshot pixels equal
OS pixels; :mod:`universal_computer.core.coordinates` offers the
transformation abstraction for environments that expose logical coordinates.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from PIL.Image import Image

from universal_computer.backends.base import Cap, ScreenshotBackend
from universal_computer.core.errors import ActionFailedError
from universal_computer.logging import get_logger
from universal_computer.models.observation import MonitorInfo, ScreenInfo

logger = get_logger("backends.screenshot")

_XRANDR_LINE = re.compile(
    r"^(?P<name>\S+)\s+connected\s+(?:primary\s+)?(?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+)"
)


# ---------------------------------------------------------------------------
# Platform geometry helpers
# ---------------------------------------------------------------------------


def ensure_dpi_awareness() -> None:
    """Best-effort per-monitor-v2 DPI awareness on Windows (no-op elsewhere)."""
    import sys  # noqa: PLC0415

    if sys.platform != "win32":
        return
    try:  # pragma: no cover - Windows only
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    except Exception:  # noqa: BLE001 - never fatal
        pass


def get_virtual_screen_bounds() -> tuple[int, int, int, int]:
    """(x, y, width, height) of the virtual desktop; falls back to (0,0,sw,sh)."""
    import sys  # noqa: PLC0415

    if sys.platform == "win32":  # pragma: no cover - Windows only
        try:
            import ctypes

            user32 = ctypes.windll.user32
            x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if width and height:
                return int(x), int(y), int(width), int(height)
        except Exception:  # noqa: BLE001
            pass
    elif sys.platform == "linux":
        bounds = _xrandr_virtual_bounds()
        if bounds is not None:
            return bounds
    # Fallback: PyAutoGUI primary screen size.
    try:
        import pyautogui  # noqa: PLC0415

        size = pyautogui.size()
        return 0, 0, int(size.width), int(size.height)
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 0


def enumerate_monitors() -> list[MonitorInfo]:
    """Enumerate monitors with their virtual-desktop offsets."""
    import sys  # noqa: PLC0415

    if sys.platform == "win32":  # pragma: no cover - Windows only
        return _enumerate_windows_monitors()
    if sys.platform == "linux":
        return _enumerate_xrandr_monitors()
    x, y, width, height = get_virtual_screen_bounds()
    return [MonitorInfo(index=0, x=x, y=y, width=width, height=height, primary=True)]


def _enumerate_windows_monitors() -> list[MonitorInfo]:  # pragma: no cover - Windows only
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        monitors: list[MonitorInfo] = []

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p
        )

        def _callback(hmonitor: Any, _hdc: Any, _rect: Any, _data: Any) -> int:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                rc = info.rcMonitor
                scale = 1.0
                try:
                    shcore = ctypes.windll.shcore
                    dpi = ctypes.c_uint()
                    if shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi), ctypes.byref(dpi)) == 0:
                        scale = max(1.0, dpi.value / 96.0)
                except Exception:  # noqa: BLE001
                    pass
                monitors.append(
                    MonitorInfo(
                        index=len(monitors),
                        x=int(rc.left),
                        y=int(rc.top),
                        width=int(rc.right - rc.left),
                        height=int(rc.bottom - rc.top),
                        primary=bool(info.dwFlags & 1),
                        scale_factor=scale,
                    )
                )
            return 1

        user32.EnumDisplayMonitors(0, 0, callback_type(_callback), 0)
        if monitors and not any(m.primary for m in monitors):
            monitors[0].primary = True
        for i, m in enumerate(monitors):
            m.index = i
        return monitors
    except Exception as exc:  # noqa: BLE001
        logger.debug("Windows monitor enumeration failed: %s", exc)
        return []


def _enumerate_xrandr_monitors() -> list[MonitorInfo]:
    monitors: list[MonitorInfo] = []
    try:
        output = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for line in output.stdout.splitlines():
            match = _XRANDR_LINE.match(line.strip())
            if not match:
                continue
            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    x=int(match.group("x")),
                    y=int(match.group("y")),
                    width=int(match.group("w")),
                    height=int(match.group("h")),
                    primary="primary" in line,
                    name=match.group("name"),
                )
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("xrandr monitor enumeration failed: %s", exc)
    if monitors and not any(m.primary for m in monitors):
        monitors[0].primary = True
    for i, m in enumerate(monitors):
        m.index = i
    return monitors


def _xrandr_virtual_bounds() -> tuple[int, int, int, int] | None:
    monitors = _enumerate_xrandr_monitors()
    if not monitors:
        return None
    left = min(m.x for m in monitors)
    top = min(m.y for m in monitors)
    right = max(m.x + m.width for m in monitors)
    bottom = max(m.y + m.height for m in monitors)
    return left, top, right - left, bottom - top


def _image_from_mss(raw: Any, width: int, height: int) -> Image:
    from PIL import Image as PILImage  # noqa: PLC0415

    return PILImage.frombytes("RGB", (width, height), raw.rgb)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class MssScreenshotBackend(ScreenshotBackend):
    """Multi-monitor screenshot backend using the optional ``mss`` package."""

    name = "mss"
    platform = "any"
    priority = 60

    def _probe(self) -> bool:
        try:
            import mss  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            with mss.mss() as scanner:
                if not scanner.monitors:
                    raise RuntimeError("mss reports no monitors")
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.SCREENSHOT}

    def _monitor_dict(self, scanner: Any, monitor: int | None) -> dict[str, int]:
        monitors = scanner.monitors
        if monitor is None or monitor < 0:
            return monitors[0]  # mss monitor 0 == the whole virtual desktop
        if monitor + 1 < len(monitors):
            return monitors[monitor + 1]
        raise ActionFailedError(
            f"monitor index {monitor} out of range (have {len(monitors) - 1} monitors)"
        )

    def take_screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image:
        import mss  # noqa: PLC0415

        with mss.mss() as scanner:
            if region is not None:
                left, top, width, height = region
                grab_region = {"left": int(left), "top": int(top), "width": max(1, int(width)), "height": max(1, int(height))}
            else:
                grab_region = self._monitor_dict(scanner, monitor)
            shot = scanner.grab(grab_region)
            return _image_from_mss(shot, shot.width, shot.height)

    def get_screen_info(self) -> ScreenInfo:
        import mss  # noqa: PLC0415

        with mss.mss() as scanner:
            virtual = scanner.monitors[0] if scanner.monitors else {"left": 0, "top": 0, "width": 0, "height": 0}
        return ScreenInfo(
            width=int(virtual["width"]),
            height=int(virtual["height"]),
            virtual_x=int(virtual["left"]),
            virtual_y=int(virtual["top"]),
            monitors=self.list_monitors(),
        )

    def list_monitors(self) -> list[MonitorInfo]:
        import mss  # noqa: PLC0415

        monitors: list[MonitorInfo] = []
        with mss.mss() as scanner:
            for i, mon in enumerate(scanner.monitors[1:]):
                monitors.append(
                    MonitorInfo(
                        index=i,
                        x=int(mon["left"]),
                        y=int(mon["top"]),
                        width=int(mon["width"]),
                        height=int(mon["height"]),
                        primary=i == 0,
                    )
                )
        return monitors or [MonitorInfo(index=0, width=0, height=0, primary=True)]


class PillowScreenshotBackend(ScreenshotBackend):
    """Pillow ImageGrab screenshot backend (works on Windows and X11)."""

    name = "pillow"
    platform = "any"
    priority = 40

    def _probe(self) -> bool:
        try:
            from PIL import ImageGrab  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            shot = self._grab(ImageGrab)
            if shot is None or shot.size[0] < 2:
                self._probe_error = "ImageGrab returned an empty image"
                return False
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    @staticmethod
    def _grab(image_grab: Any, region: tuple[int, int, int, int] | None = None) -> Image | None:
        ensure_dpi_awareness()
        try:
            return image_grab.grab(all_screens=True)
        except TypeError:
            return image_grab.grab()
        except Exception:  # noqa: BLE001 - try PyAutoGUI's own capture path
            try:
                import pyautogui  # noqa: PLC0415

                kwargs = {"region": region} if region else {}
                return pyautogui.screenshot(**kwargs)
            except Exception:  # noqa: BLE001
                return None

    def capabilities(self) -> set[str]:
        return {Cap.SCREENSHOT}

    def take_screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image:
        from PIL import ImageGrab  # noqa: PLC0415

        shot = self._grab(ImageGrab, region)
        if shot is None:
            raise ActionFailedError("no screenshot mechanism available (ImageGrab/PyAutoGUI)")
        return shot

    def get_screen_info(self) -> ScreenInfo:
        vx, vy, vw, vh = get_virtual_screen_bounds()
        monitors = enumerate_monitors()
        if vw == 0 and monitors:
            primary = next((m for m in monitors if m.primary), monitors[0])
            vx, vy, vw, vh = primary.x, primary.y, primary.width, primary.height
        return ScreenInfo(width=vw, height=vh, virtual_x=vx, virtual_y=vy, monitors=monitors)

    def list_monitors(self) -> list[MonitorInfo]:
        return enumerate_monitors()

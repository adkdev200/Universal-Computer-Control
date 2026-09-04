"""Windows window management (pygetwindow + ctypes fallback).

Implements OS-level window discovery and lifecycle: focus Chrome, minimize
VS Code, close a dialog - without any application-specific integration.
"""

from __future__ import annotations

import sys
from typing import Any

from universal_computer.backends.base import Cap, WindowManagementBackend
from universal_computer.backends.screenshot import ensure_dpi_awareness
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.models.window import WindowInfo, WindowState

logger = get_logger("backends.windows_window")


class WindowsWindowBackend(WindowManagementBackend):
    """Window discovery/management on Windows 10/11 (priority 80)."""

    name = "windows-window"
    platform = "windows"
    priority = 80

    def __init__(self) -> None:
        super().__init__()
        ensure_dpi_awareness()
        self._gw: Any | None = None

    def _probe(self) -> bool:
        if sys.platform != "win32":
            self._probe_error = "not Windows"
            return False
        try:
            import pygetwindow as gw  # noqa: PLC0415

            gw.getAllTitles()
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        self._gw = gw
        return True

    def capabilities(self) -> set[str]:
        return {Cap.WINDOWS}

    def _require(self) -> Any:
        if self._gw is None and not self.is_available():
            raise RuntimeError("pygetwindow unavailable")
        return self._gw

    # -- helpers ----------------------------------------------------------------
    @staticmethod
    def _process_name(pid: int) -> str | None:
        try:
            import psutil  # noqa: PLC0415

            return psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            return None

    def _window_info(self, win: Any) -> WindowInfo | None:
        try:
            title = win.title or ""
            if not title.strip():
                return None
            hwnd = int(getattr(win, "_hWnd", 0) or 0)
            pid = 0
            if hwnd:
                try:
                    import ctypes  # noqa: PLC0415

                    pid_val = ctypes.wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
                    pid = int(pid_val.value)
                except Exception:  # noqa: BLE001
                    pid = 0
            state = WindowState.NORMAL
            try:
                if win.isMinimized:
                    state = WindowState.MINIMIZED
                elif win.isMaximized:
                    state = WindowState.MAXIMIZED
            except Exception:  # noqa: BLE001
                pass
            bbox = None
            try:
                left, top, right, bottom = win.box
                bbox = BoundingBox.from_ltrb(int(left), int(top), int(right), int(bottom))
            except Exception:  # noqa: BLE001
                pass
            active = False
            try:
                import ctypes  # noqa: PLC0415

                active = hwnd == int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                pass
            return WindowInfo(
                title=title,
                application=self._process_name(pid) if pid else None,
                process_id=pid or None,
                handle=f"{hwnd:x}" if hwnd else None,
                bbox=bbox,
                state=state,
                is_active=active,
            )
        except Exception:  # noqa: BLE001 - window vanished mid-enumeration
            return None

    def _find(self, window: WindowInfo) -> Any | None:
        gw = self._require()
        handle = window.handle
        for win in gw.getAllWindows():
            if handle and f"{int(getattr(win, '_hWnd', 0)):x}" == handle:
                return win
            if window.title and win.title == window.title:
                return win
        return None

    # -- WindowManagementBackend API ----------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        gw = self._require()
        infos: list[WindowInfo] = []
        for win in gw.getAllWindows():
            info = self._window_info(win)
            if info is not None:
                infos.append(info)
        return infos

    def get_active_window(self) -> WindowInfo | None:
        try:
            import ctypes  # noqa: PLC0415

            hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            if not hwnd:
                return None
            for win in self._require().getAllWindows():
                if int(getattr(win, "_hWnd", 0)) == hwnd:
                    return self._window_info(win)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_active_window failed: %s", exc)
        return None

    def focus_window(self, window: WindowInfo) -> bool:
        win = self._find(window)
        if win is None:
            return False
        try:
            try:
                win.restore()
            except Exception:  # noqa: BLE001
                pass
            win.activate()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("focus_window failed: %s", exc)
            return False

    def minimize_window(self, window: WindowInfo) -> bool:
        win = self._find(window)
        if win is None:
            return False
        try:
            win.minimize()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("minimize_window failed: %s", exc)
            return False

    def maximize_window(self, window: WindowInfo) -> bool:
        win = self._find(window)
        if win is None:
            return False
        try:
            win.maximize()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("maximize_window failed: %s", exc)
            return False

    def restore_window(self, window: WindowInfo) -> bool:
        win = self._find(window)
        if win is None:
            return False
        try:
            win.restore()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("restore_window failed: %s", exc)
            return False

    def close_window(self, window: WindowInfo) -> bool:
        win = self._find(window)
        if win is None:
            return False
        try:
            win.close()  # WM_CLOSE: graceful close, modal prompts stay visible
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("close_window failed: %s", exc)
            return False

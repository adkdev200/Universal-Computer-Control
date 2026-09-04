"""PyAutoGUI backend: the universal physical input fallback.

Implements mouse, keyboard, cursor and (secondary) screenshot capabilities on
both Windows and Linux/X11. The import is performed lazily inside the probe:
on headless machines or without the optional dependency this backend simply
reports itself unavailable and the rest of the engine keeps working.
"""

from __future__ import annotations

import shutil
from typing import Any

from PIL.Image import Image

from universal_computer.backends.base import Cap, CursorBackend, InputBackend, ScreenshotBackend
from universal_computer.config import InputConfig
from universal_computer.core.errors import ActionFailedError, InputNotSupportedError
from universal_computer.logging import get_logger
from universal_computer.models.observation import MonitorInfo, ScreenInfo

logger = get_logger("backends.pyautogui")

#: keys accepted by most layouts; alias -> canonical pyautogui key name
KEY_ALIASES = {
    "return": "enter",
    "esc": "esc",
    "control": "ctrl",
    "option": "alt",
    "cmd": "winleft",
    "win": "winleft",
    "super": "winleft",
    "meta": "winleft",
    "del": "delete",
    "ins": "insert",
}


class PyAutoGUIBackend(CursorBackend, ScreenshotBackend, InputBackend):
    """Cross-platform mouse/keyboard/screenshot backend (priority 10)."""

    name = "pyautogui"
    platform = "any"
    priority = 10

    def __init__(self, config: InputConfig | None = None) -> None:
        super().__init__()
        self.config = config or InputConfig()
        self._pyautogui: Any = None

    # -- availability -----------------------------------------------------------
    def _probe(self) -> bool:
        try:
            import pyautogui  # noqa: PLC0415
        except Exception as exc:  # ImportError, DISPLAY errors, etc.
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            pyautogui.size()  # requires a live display
        except Exception as exc:
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        self._pyautogui = pyautogui
        pyautogui.FAILSAFE = self.config.failsafe
        pyautogui.PAUSE = self.config.pause_s
        return True

    def _health_details(self) -> str:
        if self._pyautogui is None:
            return ""
        try:
            size = self._pyautogui.size()
            return f"screen={size.width}x{size.height}"
        except Exception:  # noqa: BLE001
            return ""

    def capabilities(self) -> set[str]:
        return {Cap.INPUT, Cap.SCREENSHOT, Cap.CURSOR}

    def _require(self) -> Any:
        if self._pyautogui is None and not self.is_available():
            raise ActionFailedError("pyautogui backend is not available on this system")
        return self._pyautogui

    # -- mouse --------------------------------------------------------------------
    def click(self, x: int, y: int, button: str = "left") -> None:
        self._require().click(x=int(x), y=int(y), button=button)

    def double_click(self, x: int, y: int, button: str = "left") -> None:
        self._require().click(x=int(x), y=int(y), clicks=2, button=button, interval=0.08)

    def right_click(self, x: int, y: int) -> None:
        self._require().click(x=int(x), y=int(y), button="right")

    def move(self, x: int, y: int, duration_s: float | None = None) -> None:
        duration = self.config.move_duration_s if duration_s is None else duration_s
        self._require().moveTo(int(x), int(y), duration=max(0.0, duration))

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_s: float | None = None,
    ) -> None:
        duration = self.config.drag_duration_s if duration_s is None else duration_s
        pag = self._require()
        pag.moveTo(int(start[0]), int(start[1]), duration=min(0.1, duration))
        pag.dragTo(
            int(end[0]),
            int(end[1]),
            duration=max(0.05, duration),
            button="left",
        )

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        kwargs: dict[str, int] = {}
        if x is not None:
            kwargs["x"] = int(x)
        if y is not None:
            kwargs["y"] = int(y)
        self._require().scroll(int(amount), **kwargs)

    # -- keyboard ------------------------------------------------------------------
    def type_text(self, text: str, interval_s: float = 0.0) -> None:
        interval = interval_s if interval_s else self.config.type_interval_s
        # pyautogui.write() only covers ASCII-range characters reliably.
        if any(ord(ch) > 126 for ch in text):
            raise InputNotSupportedError(
                "text contains non-ASCII characters; use the clipboard strategy"
            )
        self._require().write(text, interval=interval)

    def press(self, key: str) -> None:
        self._require().press(KEY_ALIASES.get(key.strip().lower(), key))

    def hotkey(self, *keys: str) -> None:
        normalized = [KEY_ALIASES.get(k.strip().lower(), k.strip()) for k in keys if k.strip()]
        if not normalized:
            raise ActionFailedError("hotkey requires at least one key")
        self._require().hotkey(*normalized)

    # -- cursor / screenshot -------------------------------------------------------
    def get_cursor_position(self) -> tuple[int, int] | None:
        try:
            pos = self._require().position()
            return (int(pos.x), int(pos.y))
        except Exception:  # noqa: BLE001
            return None

    def take_screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image:
        kwargs: dict[str, Any] = {}
        if region is not None:
            left, top, width, height = region
            kwargs["region"] = (left, top, max(1, width), max(1, height))
        shot = self._require().screenshot(**kwargs)
        if shot.size[0] < 2 and shot.size[1] < 2:
            raise ActionFailedError("screenshot capture returned an empty image")
        return shot

    def get_screen_info(self) -> ScreenInfo:
        from universal_computer.backends.screenshot import (  # noqa: PLC0415
            enumerate_monitors,
            get_virtual_screen_bounds,
        )

        pag = self._require()
        try:
            size = pag.size()
            width, height = int(size.width), int(size.height)
        except Exception:  # noqa: BLE001
            width, height = 0, 0
        virtual_x, virtual_y, v_width, v_height = get_virtual_screen_bounds()
        if v_width and v_height:
            width, height = v_width, v_height
        monitors = enumerate_monitors() or [
            MonitorInfo(index=0, x=virtual_x, y=virtual_y, width=width, height=height, primary=True)
        ]
        return ScreenInfo(
            width=width,
            height=height,
            virtual_x=virtual_x,
            virtual_y=virtual_y,
            monitors=monitors,
        )

    def list_monitors(self) -> list[MonitorInfo]:
        from universal_computer.backends.screenshot import enumerate_monitors  # noqa: PLC0415

        return enumerate_monitors()

    @staticmethod
    def which(program: str) -> str | None:
        """Convenience wrapper used by launcher helpers."""
        return shutil.which(program)

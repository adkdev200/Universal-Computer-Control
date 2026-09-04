"""Backend base classes and capability contracts.

Every platform-specific implementation inherits from :class:`Backend` and
advertises:

- ``name`` / ``platform`` / ``priority`` (higher wins when selecting),
- ``capabilities()``: the set of capability strings it provides,
- ``is_available()``: cheap, cached availability probe,
- ``health_check()``: deeper health report (``BackendHealth``).

Capability strings (see :class:`Cap`) are the *only* vocabulary the engine
uses; it never imports a concrete backend directly. Platform-specific
implementations are lazily probed so importing this package never requires
OS-specific libraries.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from PIL.Image import Image
from pydantic import BaseModel

from universal_computer.models.element import UIElement
from universal_computer.models.observation import MonitorInfo, ScreenInfo
from universal_computer.models.window import WindowInfo


class BackendStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    UNKNOWN = "unknown"


class BackendHealth(BaseModel):
    """Health report for a single backend (``computer.backend_status``)."""

    backend: str
    platform: str
    status: BackendStatus = BackendStatus.UNKNOWN
    available: bool = False
    healthy: bool = False
    details: str = ""
    error: str | None = None


class Cap:
    """Capability strings used with :meth:`BackendManager.select`."""

    INPUT = "input"
    SCREENSHOT = "screenshot"
    CURSOR = "cursor"
    ACCESSIBILITY = "accessibility"
    UI_TREE = "ui_tree"
    WINDOWS = "windows"
    CLIPBOARD = "clipboard"
    LAUNCH = "launch"


class Backend(ABC):
    """Base class for all computer-control backends."""

    name: str = "backend"
    platform: str = "any"  # "windows" | "linux" | "macos" | "any"
    priority: int = 10  # higher wins

    #: consecutive failures after which a backend is temporarily down-ranked
    FAILURE_THRESHOLD = 3
    FAILURE_COOLDOWN_S = 30.0

    def __init__(self) -> None:
        self._available: bool | None = None
        self._failure_count = 0
        self._disabled_until = 0.0

    # -- identity ------------------------------------------------------------
    def capabilities(self) -> set[str]:
        return set()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "priority": self.priority,
            "capabilities": sorted(self.capabilities()),
            "available": self.is_available(),
        }

    # -- availability ----------------------------------------------------------
    def is_available(self) -> bool:
        """Cached availability probe (override :meth:`_probe`)."""
        if self._available is None:
            try:
                self._available = bool(self._probe())
            except Exception as exc:  # noqa: BLE001 - any probe error = unavailable
                self._available = False
                self._probe_error = f"{type(exc).__name__}: {exc}"
        return self._available

    def _probe(self) -> bool:
        """Cheap availability check (imports, imports of OS libs, tiny call)."""
        return True

    def invalidate_probe(self) -> None:
        """Forget the cached availability result (e.g. after display changes)."""
        self._available = None

    # -- health ----------------------------------------------------------------
    def health_check(self) -> BackendHealth:
        available = self.is_available()
        status = BackendStatus.AVAILABLE if available else BackendStatus.UNAVAILABLE
        if self.temporarily_disabled:
            status = BackendStatus.DEGRADED
        error = getattr(self, "_probe_error", None) if not available else None
        return BackendHealth(
            backend=self.name,
            platform=self.platform,
            status=status,
            available=available,
            healthy=available and not self.temporarily_disabled,
            details=self._health_details(),
            error=error,
        )

    def _health_details(self) -> str:
        return ""

    # -- failure tracking --------------------------------------------------------
    @property
    def temporarily_disabled(self) -> bool:
        return time.monotonic() < self._disabled_until

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.FAILURE_THRESHOLD:
            self._disabled_until = time.monotonic() + self.FAILURE_COOLDOWN_S
            self._failure_count = 0

    def record_success(self) -> None:
        self._failure_count = 0
        self._disabled_until = 0.0


# ---------------------------------------------------------------------------
# Capability contracts. Backends implement one or more of these ABCs.
# ---------------------------------------------------------------------------


class InputBackend(Backend):
    """Physical input: mouse, keyboard."""

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left") -> None: ...

    @abstractmethod
    def double_click(self, x: int, y: int, button: str = "left") -> None: ...

    @abstractmethod
    def right_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    def move(self, x: int, y: int, duration_s: float | None = None) -> None: ...

    @abstractmethod
    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_s: float | None = None,
    ) -> None: ...

    @abstractmethod
    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None: ...

    @abstractmethod
    def type_text(self, text: str, interval_s: float = 0.0) -> None: ...

    @abstractmethod
    def press(self, key: str) -> None: ...

    @abstractmethod
    def hotkey(self, *keys: str) -> None: ...


class ScreenshotBackend(Backend):
    """Screen capture and geometry."""

    @abstractmethod
    def take_screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image: ...

    @abstractmethod
    def get_screen_info(self) -> ScreenInfo: ...

    @abstractmethod
    def list_monitors(self) -> list[MonitorInfo]: ...


class CursorBackend(Backend):
    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int] | None: ...


class AccessibilityBackend(Backend):
    """Semantic UI access: element trees, roles, names, semantic actions."""

    @abstractmethod
    def get_window_elements(
        self, window: WindowInfo | None = None, max_depth: int = 8
    ) -> list[UIElement]: ...

    @abstractmethod
    def get_ui_tree(self, window: WindowInfo | None = None, max_depth: int = 8) -> dict | None: ...

    @abstractmethod
    def get_focused_element(self) -> UIElement | None: ...

    @abstractmethod
    def invoke_element(self, element: UIElement) -> bool: ...

    def focus_element(self, element: UIElement) -> bool:  # optional capability
        return False


class WindowManagementBackend(Backend):
    """OS window discovery and lifecycle management."""

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]: ...

    @abstractmethod
    def get_active_window(self) -> WindowInfo | None: ...

    @abstractmethod
    def focus_window(self, window: WindowInfo) -> bool: ...

    @abstractmethod
    def minimize_window(self, window: WindowInfo) -> bool: ...

    @abstractmethod
    def maximize_window(self, window: WindowInfo) -> bool: ...

    @abstractmethod
    def restore_window(self, window: WindowInfo) -> bool: ...

    @abstractmethod
    def close_window(self, window: WindowInfo) -> bool: ...


class ClipboardBackend(Backend):
    @abstractmethod
    def get_text(self) -> str | None: ...

    @abstractmethod
    def set_text(self, text: str) -> bool: ...


class ApplicationBackend(Backend):
    """Application launching (never command execution)."""

    @abstractmethod
    def launch(self, command: str) -> int | None:
        """Launch an application; returns its PID when known."""

"""Window model used for OS window discovery and management."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from universal_computer.models.element import BoundingBox


class WindowState(str, Enum):
    NORMAL = "normal"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


class WindowInfo(BaseModel):
    """A top-level OS window discovered by a window-management backend."""

    title: str
    application: str | None = None
    process_id: int | None = None
    # Cross-platform handle: hex string on Windows, hex window id on X11.
    handle: str | None = None
    bbox: BoundingBox | None = None
    state: WindowState = WindowState.UNKNOWN
    is_active: bool = False
    monitor_index: int | None = None

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match against title and application."""
        q = query.strip().casefold()
        if not q:
            return False
        if q in self.title.casefold():
            return True
        return bool(self.application and q in self.application.casefold())

"""Coordinate transformation abstraction (DPI / multi-monitor).

Canonical space everywhere in the engine: **virtual-desktop pixels** as seen
in screenshots. On Windows the process opts into per-monitor DPI awareness so
screenshot pixels == OS pixels. Some accessibility sources may still report
*logical* coordinates when DPI awareness could not be established; the
transformer converts those into the canonical space using per-monitor scale
factors. The default implementation is the identity transform.
"""

from __future__ import annotations

from universal_computer.models.observation import MonitorInfo, ScreenInfo


class CoordinateTransformer:
    """Converts between logical UI coordinates and virtual-desktop pixels."""

    def __init__(self, screen_info: ScreenInfo | None = None) -> None:
        self.screen_info = screen_info

    def to_native(self, x: float, y: float) -> tuple[int, int]:
        """Logical UI coords -> virtual-desktop pixel coords (identity)."""
        return int(round(x)), int(round(y))

    def from_native(self, x: float, y: float) -> tuple[int, int]:
        """Virtual-desktop pixel coords -> logical UI coords (identity)."""
        return int(round(x)), int(round(y))

    def _monitor_at(self, x: int, y: int) -> MonitorInfo | None:
        if not self.screen_info:
            return None
        for monitor in self.screen_info.monitors:
            if (
                monitor.x <= x < monitor.x + monitor.width
                and monitor.y <= y < monitor.y + monitor.height
            ):
                return monitor
        return None


class ScaledCoordinateTransformer(CoordinateTransformer):
    """Applies per-monitor scale factors (used when sources report logical coords)."""

    def to_native(self, x: float, y: float) -> tuple[int, int]:
        monitor = self._monitor_at(int(x), int(y))
        scale = monitor.scale_factor if monitor is not None and monitor.scale_factor else 1.0
        return int(round(x * scale)), int(round(y * scale))

    def from_native(self, x: float, y: float) -> tuple[int, int]:
        monitor = self._monitor_at(int(x), int(y))
        scale = monitor.scale_factor if monitor is not None and monitor.scale_factor else 1.0
        return int(round(x / scale)), int(round(y / scale))

"""Pydantic models shared across the engine, backends and MCP tools."""

from universal_computer.models.action import (
    ActionResult,
    ActionType,
    AttemptRecord,
    ResolvedTarget,
    VerificationResult,
)
from universal_computer.models.element import (
    BoundingBox,
    ElementSource,
    UIElement,
    normalize_role,
)
from universal_computer.models.observation import (
    ActiveWindowInfo,
    CursorInfo,
    MonitorInfo,
    Observation,
    ObservationLevel,
    OCRResult,
    ScreenInfo,
    TextMatch,
    parse_level,
)
from universal_computer.models.window import WindowInfo, WindowState

__all__ = [
    "ActionResult",
    "ActionType",
    "ActiveWindowInfo",
    "AttemptRecord",
    "BoundingBox",
    "CursorInfo",
    "ElementSource",
    "MonitorInfo",
    "OCRResult",
    "Observation",
    "ObservationLevel",
    "ResolvedTarget",
    "ScreenInfo",
    "TextMatch",
    "UIElement",
    "VerificationResult",
    "WindowInfo",
    "WindowState",
    "normalize_role",
    "parse_level",
]

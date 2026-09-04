"""Unified element model shared by every backend and observation."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class ElementSource(str, Enum):
    """Where a UI element was discovered."""

    UIA = "uia"              # Windows UI Automation (pywinauto)
    ATSPI = "atspi"          # Linux AT-SPI accessibility
    OCR = "ocr"              # text found by an OCR engine
    OPENCV = "opencv"        # template matching
    VISION = "vision"        # vision-language model
    SCREENSHOT = "screenshot"


#: Roles considered clickable when a backend could not determine it directly.
CLICKABLE_ROLES = frozenset(
    {
        "button",
        "link",
        "menu item",
        "check box",
        "radio button",
        "tab item",
        "list item",
        "tree item",
        "combo box",
        "menu",
        "split button",
    }
)

#: Roles that typically expose a semantic invoke/action on accessibility APIs.
INVOKEABLE_ROLES = frozenset(
    {
        "button",
        "link",
        "menu item",
        "check box",
        "radio button",
        "tab item",
        "list item",
        "tree item",
        "combo box",
        "menu",
    }
)

_ROLE_ALIASES: dict[str, str] = {
    # Windows UIA control types (concatenated camel-case forms)
    "pushbutton": "button",
    "splitpushbutton": "split button",
    "radiobutton": "radio button",
    "checkbox": "check box",
    "combobox": "combo box",
    "hyperlink": "link",
    "listitem": "list item",
    "menuitem": "menu item",
    "menubar": "menu bar",
    "tabitem": "tab item",
    "tabpanel": "tab panel",
    "treeitem": "tree item",
    "progressbar": "progress bar",
    "scrollbar": "scroll bar",
    "toolbar": "tool bar",
    "statusbar": "status bar",
    "titlebar": "title bar",
    "statictext": "text",
    # Windows UIA control types (spaced forms)
    "push button": "button",
    "split push button": "split button",
    "button": "button",
    "check box": "check box",
    "radio button": "radio button",
    "combo box": "combo box",
    "dropdown": "combo box",
    "edit": "text field",
    "document": "document",
    "link": "link",
    "list": "list",
    "list item": "list item",
    "listbox": "list",
    "menu": "menu",
    "menu item": "menu item",
    "pane": "pane",
    "client": "pane",
    "group": "group",
    "tab": "tab",
    "tab item": "tab item",
    "tab panel": "tab panel",
    "table": "table",
    "tree": "tree",
    "tree item": "tree item",
    "slider": "slider",
    "spinner": "spinner",
    "progress bar": "progress bar",
    "scroll bar": "scroll bar",
    "tool bar": "tool bar",
    "menu bar": "menu bar",
    "status bar": "status bar",
    "title bar": "title bar",
    "window": "window",
    "dialog": "dialog",
    "image": "image",
    "canvas": "canvas",
    "label": "text",
    "text": "text",
    "heading": "text",
    # AT-SPI role names
    "toggle button": "button",
    "page tab": "tab item",
    "page tab list": "tab",
    "entry": "text field",
    "frame": "window",
    "root pane": "pane",
    "layered pane": "pane",
    "scroll pane": "pane",
    "split pane": "pane",
    "icon": "image",
    "application": "application",
    "alert": "dialog",
    "notification": "dialog",
}


def normalize_role(raw: str | None) -> str:
    """Normalize a backend-specific role name into the canonical vocabulary."""
    if not raw:
        return "unknown"
    key = str(raw).strip().lower().replace("_", " ")
    return _ROLE_ALIASES.get(key, key)


class BoundingBox(BaseModel):
    """Axis-aligned rectangle in virtual-desktop pixel coordinates.

    Stored internally as origin + size; serialized as ``[left, top, right,
    bottom]`` (matching the public observation schema).
    """

    model_config = ConfigDict(validate_assignment=True)

    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        # Accept a 4-sequence [left, top, right, bottom].
        if isinstance(data, (list, tuple)) and len(data) == 4:
            left, top, right, bottom = (int(v) for v in data)
            return {
                "x": left,
                "y": top,
                "width": max(0, right - left),
                "height": max(0, bottom - top),
            }
        return data

    @model_serializer(mode="plain")
    def _serialize(self) -> list[int]:
        return [self.x, self.y, self.right, self.bottom]

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> BoundingBox:
        return cls(x=left, y=top, width=max(0, right - left), height=max(0, bottom - top))

    @classmethod
    def from_xywh(cls, x: int, y: int, width: int, height: int) -> BoundingBox:
        return cls(x=x, y=y, width=max(0, width), height=max(0, height))

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersects(self, other: BoundingBox) -> bool:
        return not (
            other.left >= self.right
            or other.right <= self.left
            or other.top >= self.bottom
            or other.bottom <= self.top
        )


class UIElement(BaseModel):
    """A single UI element normalized across all backends.

    ``id`` is stable within one observation cycle (``element_1``, ``element_2``,
    ...). Elements discovered by accessibility backends may carry semantic
    metadata (``can_invoke``, backend name, native handle) that lets the engine
    prefer semantic actions over coordinate clicks.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    role: str = "unknown"
    name: str | None = None
    text: str | None = None
    bbox: BoundingBox | None = None
    enabled: bool | None = None
    visible: bool | None = None
    clickable: bool | None = None
    source: ElementSource = ElementSource.SCREENSHOT
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("role")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_role(value)

    def searchable_text(self) -> str:
        """Combined name/text used for matching targets against elements."""
        parts = [self.name or "", self.text or ""]
        return " ".join(p.strip() for p in parts if p and p.strip())

    def is_clickable(self) -> bool:
        if self.clickable is not None:
            return self.clickable
        return self.role in CLICKABLE_ROLES

    def can_invoke_semantically(self) -> bool:
        # An explicit backend-provided flag always wins over the role heuristic.
        explicit = self.metadata.get("can_invoke")
        if explicit is not None:
            return bool(explicit)
        return (
            self.source in (ElementSource.UIA, ElementSource.ATSPI)
            and self.role in INVOKEABLE_ROLES
        )

    def center(self) -> tuple[int, int] | None:
        if self.bbox is None:
            return None
        return self.bbox.center()

"""Observation model: a structured snapshot of the current computer state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from universal_computer.models.element import BoundingBox, ElementSource, UIElement
from universal_computer.models.window import WindowInfo


class ObservationLevel(str, Enum):
    """How much information an observation should gather.

    - ``minimal``: screen/monitor info, active window, cursor - no screenshots.
    - ``normal``: minimal + screenshot (persisted) + OCR + accessibility elements.
    - ``full``: normal + focused-element details and any extra configured
      vision providers (deeper accessibility tree, etc.).
    """

    MINIMAL = "minimal"
    NORMAL = "normal"
    FULL = "full"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK: dict[ObservationLevel, int] = {
    ObservationLevel.MINIMAL: 0,
    ObservationLevel.NORMAL: 1,
    ObservationLevel.FULL: 2,
}


def parse_level(level: str | ObservationLevel) -> ObservationLevel:
    if isinstance(level, ObservationLevel):
        return level
    try:
        return ObservationLevel(str(level).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"Invalid observation level {level!r}; expected minimal|normal|full"
        ) from exc


class MonitorInfo(BaseModel):
    index: int = 0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    primary: bool = False
    scale_factor: float = 1.0
    name: str | None = None


class ScreenInfo(BaseModel):
    """Virtual-desktop geometry (all monitors)."""

    width: int = 0
    height: int = 0
    virtual_x: int = 0
    virtual_y: int = 0
    scale_factor: float = 1.0
    monitors: list[MonitorInfo] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class CursorInfo(BaseModel):
    x: int
    y: int


class ActiveWindowInfo(BaseModel):
    title: str
    application: str | None = None
    process_id: int | None = None
    window_handle: str | None = None
    bbox: BoundingBox | None = None


class OCRResult(BaseModel):
    """A piece of text found on screen by an OCR provider."""

    text: str
    bbox: BoundingBox
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: ElementSource = ElementSource.OCR


class TextMatch(BaseModel):
    """Result of ``computer.find_text`` (or an internal fuzzy text search)."""

    text: str
    bbox: BoundingBox
    score: float = 0.0
    confidence: float = 0.0
    source: ElementSource = ElementSource.OCR
    element_id: str | None = None


class Observation(BaseModel):
    """Structured snapshot of the computer state produced by ``computer.observe``."""

    id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    level: ObservationLevel = ObservationLevel.NORMAL
    screen: ScreenInfo | None = None
    active_window: ActiveWindowInfo | None = None
    cursor: CursorInfo | None = None
    elements: list[UIElement] = Field(default_factory=list)
    ocr: list[OCRResult] = Field(default_factory=list)
    focused_element: UIElement | None = None
    screenshot_path: str | None = None
    notes: list[str] = Field(default_factory=list)

    def element_by_id(self, element_id: str) -> UIElement | None:
        for element in self.elements:
            if element.id == element_id:
                return element
        return None

    def to_dict(self) -> dict:
        """JSON-safe dict (bboxes as [l, t, r, b], datetime as ISO string)."""
        return self.model_dump(mode="json")

    @classmethod
    def from_window_info(cls, window: WindowInfo, obs_id: str) -> Observation:
        return cls(
            id=obs_id,
            level=ObservationLevel.MINIMAL,
            active_window=ActiveWindowInfo(
                title=window.title,
                application=window.application,
                process_id=window.process_id,
                window_handle=window.handle,
                bbox=window.bbox,
            ),
        )

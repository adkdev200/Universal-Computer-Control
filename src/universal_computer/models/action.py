"""Action models: resolved targets, attempts, verification and results."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from universal_computer.models.element import UIElement


class ActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MOVE = "move"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE = "type"
    PRESS = "press"
    HOTKEY = "hotkey"
    INVOKE = "invoke"
    FOCUS_ELEMENT = "focus_element"
    WINDOW_OP = "window_op"
    LAUNCH = "launch"
    RUN_COMMAND = "run_command"
    CLIPBOARD = "clipboard"
    WAIT = "wait"


class ResolvedTarget(BaseModel):
    """The outcome of resolving a user-facing target into something actionable.

    ``kind`` is one of: ``coordinates`` (explicit x/y), ``element`` (matched a
    UI element), ``text`` (matched OCR text), ``template`` (matched an image
    template), ``vision`` (resolved by a vision-language model), ``none``.
    """

    kind: str = "none"
    element: UIElement | None = None
    coordinates: tuple[int, int] | None = None
    method: str = "none"
    confidence: float = 0.0
    description: str = ""
    semantic_available: bool = False
    # Lower-priority alternatives (coords, method, confidence) used by the
    # recovery engine when the primary attempt misses.
    alternatives: list[tuple[tuple[int, int], str, float]] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    """A single execution attempt recorded by the recovery engine."""

    method: str
    backend: str | None = None
    detail: str = ""
    success: bool = False
    skipped: bool = False
    error: str | None = None
    duration_ms: float = 0.0


class VerificationResult(BaseModel):
    """Outcome of comparing the state before and after an action."""

    performed: bool = False
    changed: bool | None = None
    similarity: float | None = None
    success: bool | None = None
    details: str | None = None
    before: str | None = None
    after: str | None = None


class ActionResult(BaseModel):
    """Full result of an executed action, including attempts and verification."""

    success: bool
    action: str
    target: str | None = None
    method: str = "none"
    backend: str | None = None
    coordinates: tuple[int, int] | None = None
    confidence: float = 0.0
    duration_ms: float = 0.0
    error: str | None = None
    verification: VerificationResult | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    element: UIElement | None = None
    observation_id_before: str | None = None
    observation_id_after: str | None = None

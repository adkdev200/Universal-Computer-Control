"""Reusable mock backends for unit tests (no OS dependencies at all)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

from universal_computer.backends.base import (
    AccessibilityBackend,
    Cap,
    ClipboardBackend,
    CursorBackend,
    InputBackend,
    ScreenshotBackend,
    WindowManagementBackend,
)
from universal_computer.core.errors import ActionFailedError
from universal_computer.models.element import BoundingBox, ElementSource, UIElement
from universal_computer.models.observation import MonitorInfo, OCRResult, ScreenInfo
from universal_computer.models.window import WindowInfo, WindowState

SCREEN_W, SCREEN_H = 1280, 800


class MockInputBackend(CursorBackend, InputBackend):
    """Records all physical actions; can simulate failures."""

    name = "mock-input"
    platform = "any"
    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self.clicks: list[tuple[int, int, str]] = []
        self.double_clicks: list[tuple[int, int]] = []
        self.right_clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.drags: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self.scrolls: list[tuple[int, int | None, int | None]] = []
        self.typed: list[str] = []
        self.pressed: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.fail_next_clicks = 0
        self.cursor = (10, 10)
        self.on_click: list[Callable[[int, int, str], None]] = []

    def capabilities(self) -> set[str]:
        return {Cap.INPUT, Cap.CURSOR}

    def _maybe_fail(self) -> None:
        if self.fail_next_clicks > 0:
            self.fail_next_clicks -= 1
            raise ActionFailedError("simulated click failure")

    def click(self, x: int, y: int, button: str = "left") -> None:
        self._maybe_fail()
        self.clicks.append((int(x), int(y), button))
        for callback in self.on_click:
            callback(int(x), int(y), button)

    def double_click(self, x: int, y: int, button: str = "left") -> None:
        self._maybe_fail()
        self.double_clicks.append((int(x), int(y)))

    def right_click(self, x: int, y: int) -> None:
        self._maybe_fail()
        self.right_clicks.append((int(x), int(y)))

    def move(self, x: int, y: int, duration_s: float | None = None) -> None:
        self.moves.append((int(x), int(y)))
        self.cursor = (int(x), int(y))

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_s: float | None = None) -> None:
        self._maybe_fail()
        self.drags.append((start, end))

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        self.scrolls.append((amount, x, y))

    def type_text(self, text: str, interval_s: float = 0.0) -> None:
        if any(ord(ch) > 126 for ch in text):
            raise ActionFailedError("non-ascii typing unsupported by mock")
        self.typed.append(text)

    def press(self, key: str) -> None:
        self.pressed.append(key)

    def hotkey(self, *keys: str) -> None:
        self.hotkeys.append(tuple(keys))

    def get_cursor_position(self) -> tuple[int, int] | None:
        return self.cursor


class MockScreenshotBackend(ScreenshotBackend):
    """Renders a simple synthetic screen with optional per-call drawing."""

    name = "mock-screen"
    platform = "any"
    priority = 50

    def __init__(self) -> None:
        super().__init__()
        self.drawers: list[Callable[[ImageDraw.ImageDraw], None]] = []
        self.capture_count = 0

    def capabilities(self) -> set[str]:
        return {Cap.SCREENSHOT}

    def take_screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> Image.Image:
        self.capture_count += 1
        image = Image.new("RGB", (SCREEN_W, SCREEN_H), color=(40, 44, 52))
        draw = ImageDraw.Draw(image)
        for drawer in self.drawers:
            drawer(draw)
        if region is not None:
            left, top, right, bottom = region
            return image.crop((left, top, right, bottom))
        return image

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(
            width=SCREEN_W,
            height=SCREEN_H,
            monitors=[MonitorInfo(index=0, width=SCREEN_W, height=SCREEN_H, primary=True)],
        )

    def list_monitors(self) -> list[MonitorInfo]:
        return [MonitorInfo(index=0, width=SCREEN_W, height=SCREEN_H, primary=True)]


class MockAccessibilityBackend(AccessibilityBackend):
    """Serves a fixed element list; records semantic invocations."""

    name = "mock-a11y"
    platform = "any"
    priority = 50

    def __init__(self, elements: list[UIElement] | None = None) -> None:
        super().__init__()
        self.elements = elements or []
        self.invoked: list[str] = []
        self.invoke_should_fail = False
        self.focused: UIElement | None = None

    def capabilities(self) -> set[str]:
        return {Cap.ACCESSIBILITY, Cap.UI_TREE}

    def get_window_elements(
        self, window: WindowInfo | None = None, max_depth: int = 8
    ) -> list[UIElement]:
        return list(self.elements)

    def get_ui_tree(self, window: WindowInfo | None = None, max_depth: int = 8) -> dict | None:
        return {
            "role": "window",
            "name": "Mock App",
            "children": [
                {
                    "role": element.role,
                    "name": element.name,
                    "children": [],
                }
                for element in self.elements
            ],
        }

    def get_focused_element(self) -> UIElement | None:
        return self.focused

    def invoke_element(self, element: UIElement) -> bool:
        if self.invoke_should_fail:
            return False
        self.invoked.append(element.name or element.id)
        for callback in element.metadata.get("on_invoke", []) if isinstance(element.metadata.get("on_invoke"), list) else []:
            callback()
        return True


class MockWindowBackend(WindowManagementBackend):
    """In-memory window list with working lifecycle ops."""

    name = "mock-windows"
    platform = "any"
    priority = 50

    def __init__(self, windows: list[WindowInfo] | None = None) -> None:
        super().__init__()
        self.windows = windows or []
        self.ops: list[tuple[str, str]] = []

    def capabilities(self) -> set[str]:
        return {Cap.WINDOWS}

    @staticmethod
    def make_window(title: str, application: str = "app", active: bool = False) -> WindowInfo:
        return WindowInfo(
            title=title,
            application=application,
            process_id=1234,
            handle=title.lower().replace(" ", "-"),
            bbox=BoundingBox(x=0, y=0, width=800, height=600),
            state=WindowState.NORMAL,
            is_active=active,
        )

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def get_active_window(self) -> WindowInfo | None:
        for window in self.windows:
            if window.is_active:
                return window
        return self.windows[0] if self.windows else None

    def _find(self, window: WindowInfo) -> WindowInfo | None:
        for candidate in self.windows:
            if window.handle and candidate.handle == window.handle:
                return candidate
            if candidate.title == window.title:
                return candidate
        return None

    def focus_window(self, window: WindowInfo) -> bool:
        target = self._find(window)
        if target is None:
            return False
        for other in self.windows:
            other.is_active = False
        target.is_active = True
        self.ops.append(("focus", target.title))
        return True

    def minimize_window(self, window: WindowInfo) -> bool:
        target = self._find(window)
        if target is None:
            return False
        target.state = WindowState.MINIMIZED
        self.ops.append(("minimize", target.title))
        return True

    def maximize_window(self, window: WindowInfo) -> bool:
        target = self._find(window)
        if target is None:
            return False
        target.state = WindowState.MAXIMIZED
        self.ops.append(("maximize", target.title))
        return True

    def restore_window(self, window: WindowInfo) -> bool:
        target = self._find(window)
        if target is None:
            return False
        target.state = WindowState.NORMAL
        self.ops.append(("restore", target.title))
        return True

    def close_window(self, window: WindowInfo) -> bool:
        target = self._find(window)
        if target is None:
            return False
        self.windows.remove(target)
        self.ops.append(("close", target.title))
        return True


class MockClipboardBackend(ClipboardBackend):
    name = "mock-clipboard"
    platform = "any"
    priority = 50

    def __init__(self) -> None:
        super().__init__()
        self._text: str | None = None
        self.set_count = 0

    def capabilities(self) -> set[str]:
        return {Cap.CLIPBOARD}

    def get_text(self) -> str | None:
        return self._text

    def set_text(self, text: str) -> bool:
        self._text = text
        self.set_count += 1
        return True


def make_element(
    name: str,
    role: str = "button",
    bbox: tuple[int, int, int, int] | None = None,
    can_invoke: bool = False,
    element_id: str | None = None,
    source: ElementSource = ElementSource.UIA,
    **metadata: Any,
) -> UIElement:
    box = BoundingBox.model_validate(list(bbox)) if bbox else BoundingBox(x=0, y=0, width=10, height=10)
    meta = {"backend": "mock-a11y", "can_invoke": can_invoke}
    meta.update(metadata)
    return UIElement(
        id=element_id or f"element_{name}",
        role=role,
        name=name,
        text=name,
        bbox=box,
        source=source,
        confidence=0.99,
        metadata=meta,
    )


class FakeOCRProvider:
    """Deterministic OCR provider returning canned results."""

    name = "fake-ocr"

    def __init__(self, results: list[OCRResult]) -> None:
        self.results = results
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def detect_text(self, image: Image.Image, region: BoundingBox | None = None) -> list[OCRResult]:
        self.calls += 1
        return list(self.results)


class FakeVLM:
    """Deterministic VLM returning a fixed bbox for any description."""

    name = "fake-vlm"

    def __init__(self, bbox: tuple[int, int, int, int] | None = None) -> None:
        self.bbox = bbox
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return self.bbox is not None

    async def locate_element(self, screenshot: Any, description: str):
        from universal_computer.vision.base import VisionResult

        self.calls.append(description)
        if self.bbox is None:
            return VisionResult(found=False, method="vlm", details="no bbox")
        return VisionResult(
            found=True,
            bbox=BoundingBox.model_validate(list(self.bbox)),
            confidence=0.9,
            method="vlm",
        )

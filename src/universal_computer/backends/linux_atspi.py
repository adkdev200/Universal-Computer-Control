"""Linux accessibility backend (AT-SPI2 via pyatspi).

Discovers applications, frames and controls with roles, names, text, states,
bounding boxes and semantic actions. AT-SPI is frequently missing (Wayland
sessions without accessibility enabled, minimal servers, headless machines):
every failure degrades to "no elements" and the engine falls back to
OCR/vision/coordinates instead of crashing.
"""

from __future__ import annotations

from typing import Any

from universal_computer.backends.base import AccessibilityBackend, Cap
from universal_computer.logging import get_logger
from universal_computer.models.element import (
    BoundingBox,
    ElementSource,
    UIElement,
    normalize_role,
)
from universal_computer.models.window import WindowInfo

logger = get_logger("backends.linux_atspi")

_MAX_TREE_NODES = 800

_ACTIONABLE_ROLES = {
    "push button",
    "button",
    "toggle button",
    "check box",
    "radio button",
    "menu item",
    "list item",
    "page tab",
    "tree item",
    "link",
    "combo box",
}


class LinuxATSPIBackend(AccessibilityBackend):
    """AT-SPI2 accessibility backend for X11/Wayland (priority 80)."""

    name = "atspi"
    platform = "linux"
    priority = 80

    def __init__(self) -> None:
        super().__init__()
        self._pyatspi: Any | None = None

    def _probe(self) -> bool:
        import sys  # noqa: PLC0415

        if sys.platform != "linux":
            self._probe_error = "not Linux"
            return False
        try:
            import pyatspi  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = (
                f"pyatspi import failed ({type(exc).__name__}: {exc}); "
                "install python3-pyatspi / gir1.2-atspi-2.0 and at-spi2-core"
            )
            return False
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            # Force a cheap round-trip through the accessibility bus.
            _ = desktop.childCount
        except Exception as exc:  # noqa: BLE001
            self._probe_error = (
                f"AT-SPI bus unavailable ({type(exc).__name__}: {exc}); on GNOME/X11 run "
                "'gsettings set org.gnome.desktop.interface toolkit-accessibility true'"
            )
            return False
        self._pyatspi = pyatspi
        return True

    def capabilities(self) -> set[str]:
        return {Cap.ACCESSIBILITY, Cap.UI_TREE}

    def _require(self) -> Any:
        if self._pyatspi is None and not self.is_available():
            raise RuntimeError("AT-SPI unavailable")
        return self._pyatspi

    # -- conversion helpers --------------------------------------------------------
    def _make_element(self, obj: Any, seq: list[int]) -> UIElement | None:
        try:
            role = normalize_role(obj.getRoleName())
            name = (obj.name or "").strip() or None
            bbox: BoundingBox | None = None
            try:
                component = obj.queryComponent()
                extents = component.getExtents(0)  # 0 == DESKTOP_COORDS
                bbox = BoundingBox.from_xywh(
                    int(extents.x), int(extents.y), int(extents.width), int(extents.height)
                )
            except Exception:  # noqa: BLE001
                bbox = None
            states = obj.getState()
            enabled = True
            visible = True
            try:
                enabled = states.contains(self._pyatspi.STATE_ENABLED)
                visible = states.contains(self._pyatspi.STATE_SHOWING)
            except Exception:  # noqa: BLE001
                pass
            text = None
            try:
                text_iface = obj.queryText()
                length = min(int(text_iface.characterCount or 0), 512)
                if length > 0:
                    text = (text_iface.getText(0, length) or "").strip() or None
            except Exception:  # noqa: BLE001
                text = None
            actionable = role in _ACTIONABLE_ROLES
            seq[0] += 1
            return UIElement(
                id=f"element_{seq[0]}",
                role=role,
                name=name,
                text=text,
                bbox=bbox if bbox and bbox.width > 0 else None,
                enabled=enabled,
                visible=visible,
                clickable=actionable,
                source=ElementSource.ATSPI,
                confidence=0.99,
                metadata={"backend": self.name, "can_invoke": actionable, "path": obj.getPath() if hasattr(obj, "getPath") else None},
            )
        except Exception:  # noqa: BLE001 - dead objects / race with the app
            return None

    def _descendants(self, root: Any, max_depth: int) -> list[Any]:
        nodes: list[Any] = []
        try:
            children = [root.getChildAtIndex(i) for i in range(root.childCount)]
        except Exception:  # noqa: BLE001
            return nodes
        current = children
        depth = max_depth
        while current and depth > 0 and len(nodes) < _MAX_TREE_NODES:
            next_level: list[Any] = []
            for obj in current:
                if obj is None:
                    continue
                nodes.append(obj)
                if len(nodes) >= _MAX_TREE_NODES:
                    break
                try:
                    next_level.extend(
                        obj.getChildAtIndex(i) for i in range(obj.childCount)
                    )
                except Exception:  # noqa: BLE001
                    continue
            current = next_level
            depth -= 1
        return nodes

    def _find_target_root(self, window: WindowInfo | None) -> Any:
        pyatspi = self._require()
        desktop = pyatspi.Registry.getDesktop(0)
        if window is None:
            return desktop
        needle = window.title.casefold()
        for i in range(desktop.childCount):
            app = desktop.getChildAtIndex(i)
            if app is None:
                continue
            for j in range(app.childCount):
                frame = app.getChildAtIndex(j)
                if frame is None:
                    continue
                try:
                    if needle and needle in (frame.name or "").casefold():
                        return frame
                except Exception:  # noqa: BLE001
                    continue
        return desktop

    # -- AccessibilityBackend API ------------------------------------------------
    def get_window_elements(
        self, window: WindowInfo | None = None, max_depth: int = 8
    ) -> list[UIElement]:
        try:
            root = self._find_target_root(window)
            seq = [0]
            elements: list[UIElement] = []
            for obj in self._descendants(root, max_depth):
                element = self._make_element(obj, seq)
                if element is not None:
                    elements.append(element)
            return elements
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_window_elements failed: %s", exc)
            return []

    def get_ui_tree(self, window: WindowInfo | None = None, max_depth: int = 8) -> dict | None:
        try:
            root = self._find_target_root(window)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_ui_tree failed: %s", exc)
            return None

        def node(obj: Any, depth: int, budget: list[int]) -> dict | None:
            if obj is None or budget[0] <= 0:
                return None
            budget[0] -= 1
            element = self._make_element(obj, [0])
            if element is None:
                return None
            children: list[dict] = []
            if depth > 1:
                try:
                    for i in range(obj.childCount):
                        child = obj.getChildAtIndex(i)
                        child_node = node(child, depth - 1, budget)
                        if child_node is not None:
                            children.append(child_node)
                except Exception:  # noqa: BLE001
                    pass
            return {
                "role": element.role,
                "name": element.name,
                "bbox": element.bbox.model_dump(mode="json") if element.bbox else None,
                "enabled": element.enabled,
                "children": children,
            }

        return node(root, max_depth, [_MAX_TREE_NODES])

    def get_focused_element(self) -> UIElement | None:
        try:
            pyatspi = self._require()
            desktop = pyatspi.Registry.getDesktop(0)
            state = pyatspi.STATE_FOCUSED
            for obj in self._descendants(desktop, 10):
                try:
                    if obj.getState().contains(state):
                        element = self._make_element(obj, [0])
                        if element is not None:
                            element.metadata["focused"] = True
                            return element
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_focused_element failed: %s", exc)
        return None

    def invoke_element(self, element: UIElement) -> bool:
        """Invoke an object's first actionable AT-SPI action ('click', 'press'...)."""
        path = element.metadata.get("path")
        try:
            obj = self._resolve_path(path)
            if obj is None:
                return False
            action = obj.queryAction()
            if action.nActions <= 0:
                return False
            action.doAction(0)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("invoke_element failed: %s", exc)
            return False

    def _resolve_path(self, path: Any) -> Any | None:
        if not path:
            return None
        try:
            pyatspi = self._require()
            desktop = pyatspi.Registry.getDesktop(0)
            obj = desktop
            for index in path:
                obj = obj.getChildAtIndex(int(index))
                if obj is None:
                    return None
            return obj
        except Exception:  # noqa: BLE001
            return None

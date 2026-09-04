"""Windows UI Automation backend (pywinauto, ``backend="uia"``).

Provides the accessibility capability on Windows: element discovery with
roles/names/bounding boxes, UI-tree retrieval, focused-element detection and
semantic ``Invoke`` actions. Every method is defensively wrapped - a single
failing application (or a missing UIA tree) never breaks the engine; the
caller then falls back to OCR/vision/coordinates.
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

logger = get_logger("backends.windows_uia")

_MAX_TREE_NODES = 800

_INVOKEABLE_UIA = {
    "button",
    "split button",
    "link",
    "menu item",
    "list item",
    "tab item",
    "check box",
    "radio button",
    "tree item",
    "combo box",
    "menu",
}

_ROLE_CLICKABLE = {
    "button",
    "split button",
    "link",
    "menu item",
    "check box",
    "radio button",
    "tab item",
    "list item",
    "tree item",
    "combo box",
}


def _import_pywinauto() -> Any:
    from pywinauto import Desktop  # noqa: PLC0415

    return Desktop


class WindowsUIABackend(AccessibilityBackend):
    """Windows UI Automation accessibility backend (priority 80)."""

    name = "uia"
    platform = "windows"
    priority = 80

    def __init__(self) -> None:
        super().__init__()
        from universal_computer.backends.screenshot import ensure_dpi_awareness  # noqa: PLC0415

        ensure_dpi_awareness()
        self._desktop_cls: Any | None = None

    def _probe(self) -> bool:
        import sys  # noqa: PLC0415

        if sys.platform != "win32":
            self._probe_error = "not Windows"
            return False
        try:
            Desktop = _import_pywinauto()
            desktop = Desktop(backend="uia")
            # Cheap probe: list top-level windows (also warms up COM).
            desktop.windows()
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        self._desktop_cls = Desktop
        return True

    def capabilities(self) -> set[str]:
        return {Cap.ACCESSIBILITY, Cap.UI_TREE}

    # -- helpers ---------------------------------------------------------------
    def _desktop(self) -> Any:
        if self._desktop_cls is None and not self.is_available():
            raise RuntimeError("UI Automation backend unavailable")
        return self._desktop_cls(backend="uia")

    def _window_spec(self, window: WindowInfo | None) -> Any:
        desktop = self._desktop()
        if window is not None and window.handle:
            try:
                return desktop.window(handle=int(window.handle, 16) if isinstance(window.handle, str) else window.handle)
            except Exception:  # noqa: BLE001
                logger.debug("handle lookup failed for %r", window.handle)
        if window is not None and window.title:
            return desktop.window(title_re=f".*{window.title[:80]}.*")
        try:
            handle = self._foreground_handle()
            if handle:
                return desktop.window(handle=handle)
        except Exception:  # noqa: BLE001
            pass
        return desktop.top_window()

    @staticmethod
    def _foreground_handle() -> int | None:
        try:
            import ctypes  # noqa: PLC0415

            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            return None

    def _element_from_control(self, control: Any, seq: list[int]) -> UIElement | None:
        """Convert a pywinauto wrapper into a normalized UIElement."""
        try:
            info = control.element_info
            control_type = (info.control_type or "unknown").strip().lower()
            role = normalize_role(control_type)
            rect = control.rectangle()
            bbox = BoundingBox.from_ltrb(
                int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
            )
            seq[0] += 1
            metadata: dict[str, Any] = {
                "backend": self.name,
                "control_type": control_type,
                "can_invoke": control_type in _INVOKEABLE_UIA,
            }
            try:
                auto_id = info.auto_id
                if auto_id:
                    metadata["automation_id"] = auto_id
            except Exception:  # noqa: BLE001
                pass
            try:
                if info.handle:
                    metadata["handle"] = int(info.handle)
            except Exception:  # noqa: BLE001
                pass
            return UIElement(
                id=f"element_{seq[0]}",
                role=role,
                name=info.name or None,
                text=info.name or None,
                bbox=bbox if bbox.width > 0 and bbox.height > 0 else None,
                enabled=bool(info.enabled) if info.enabled is not None else None,
                visible=None,
                clickable=control_type in _ROLE_CLICKABLE,
                source=ElementSource.UIA,
                confidence=0.99,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 - dead/stale controls happen constantly
            return None

    def _walk(self, root: Any, max_depth: int, seq: list[int], budget: list[int]) -> list[UIElement]:
        """BFS through the control tree with depth and node budgets."""
        elements: list[UIElement] = []
        try:
            controls = root.children()
        except Exception:  # noqa: BLE001
            return elements
        next_level: list[Any] = []
        for control in controls:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            element = self._element_from_control(control, seq)
            if element is not None:
                elements.append(element)
            if max_depth > 1:
                next_level.append(control)
        if max_depth > 1:
            for control in next_level:
                if budget[0] <= 0:
                    break
                elements.extend(self._walk(control, max_depth - 1, seq, budget))
        return elements

    # -- AccessibilityBackend API ----------------------------------------------
    def get_window_elements(
        self, window: WindowInfo | None = None, max_depth: int = 8
    ) -> list[UIElement]:
        try:
            spec = self._window_spec(window)
            seq = [0]
            return self._walk(spec, max_depth=min(max_depth, 6), seq=seq, budget=[_MAX_TREE_NODES])
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_window_elements failed: %s", exc)
            return []

    def get_ui_tree(self, window: WindowInfo | None = None, max_depth: int = 8) -> dict | None:
        """Nested tree representation with role/name/bbox per node."""

        def node(control: Any, depth: int, budget: list[int]) -> dict | None:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            element = self._element_from_control(control, [0])
            if element is None:
                return None
            children: list[dict] = []
            if depth > 1:
                try:
                    for child in control.children():
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

        try:
            spec = self._window_spec(window)
            return node(spec.wrapper_object(), max_depth, [_MAX_TREE_NODES])
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_ui_tree failed: %s", exc)
            return None

    def get_focused_element(self) -> UIElement | None:
        try:
            import ctypes  # noqa: PLC0415

            class GUITHREADINFO(ctypes.Structure):  # pragma: no cover - Windows only
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("flags", ctypes.c_ulong),
                    ("hwndActive", ctypes.c_void_p),
                    ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p),
                    ("rcCaret", ctypes.c_void_p * 4),
                ]

            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            user32 = ctypes.windll.user32
            thread_id = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gti)) and gti.hwndFocus:
                wrapper = self._desktop_cls(backend="uia").window(handle=gti.hwndFocus)
                element = self._element_from_control(wrapper, [0])
                if element is not None:
                    element.metadata["focused"] = True
                return element
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_focused_element failed: %s", exc)
        return None

    def invoke_element(self, element: UIElement) -> bool:
        """Invoke a control via its Invoke pattern (semantic, no mouse)."""
        handle = element.metadata.get("handle")
        try:
            desktop = self._desktop()
            if handle:
                wrapper = desktop.window(handle=int(handle))
            else:
                raise RuntimeError("no handle on element")
            try:
                wrapper.invoke()
                return True
            except Exception:  # noqa: BLE001 - pattern missing: fall back to focus
                return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("invoke_element failed for %s: %s", element.id, exc)
            return False

    def focus_element(self, element: UIElement) -> bool:
        handle = element.metadata.get("handle")
        try:
            if not handle:
                return False
            desktop = self._desktop()
            desktop.window(handle=int(handle)).set_focus()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("focus_element failed: %s", exc)
            return False

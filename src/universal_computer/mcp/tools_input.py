"""MCP tools: mouse and keyboard.

Targets are flexible (design §15): a text query ("Continue"), an element id
("element_17"), coordinates ({"x": 500, "y": 300}), or a natural-language
description when a vision model is configured. Resolution order follows the
interaction hierarchy automatically.
"""

from __future__ import annotations

from typing import Any

from universal_computer.core.engine import ComputerControlEngine
from universal_computer.mcp.tools_observation import error_payload


def _target(value: str | dict[str, Any] | list[Any]) -> str | dict[str, Any] | list[Any]:
    """Normalize FastMCP's JSON types into resolver-friendly targets."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and len(value) == 2:
        return {"x": value[0], "y": value[1]}
    return str(value)


def register_input_tools(app: Any, engine: ComputerControlEngine) -> None:
    @app.tool(
        name="computer.click",
        description=(
            "Click a UI element. Target can be: visible text ('Continue'), an "
            "element id from computer.observe ('element_17'), or coordinates "
            "({'x': 500, 'y': 300}). The engine resolves it via accessibility "
            "first, then OCR, then vision - and verifies the result."
        ),
    )
    async def click(target: str | dict[str, Any], button: str = "left", verify: bool | None = None) -> dict:
        try:
            result = await engine.click(_target(target), button=button, verify=verify)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.double_click",
        description="Double-click a UI element (same flexible target rules as computer.click).",
    )
    async def double_click(target: str | dict[str, Any], verify: bool | None = None) -> dict:
        try:
            result = await engine.double_click(_target(target), verify=verify)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.right_click",
        description="Right-click a UI element (same flexible target rules as computer.click).",
    )
    async def right_click(target: str | dict[str, Any], verify: bool | None = None) -> dict:
        try:
            result = await engine.right_click(_target(target), verify=verify)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.move_mouse",
        description="Move the mouse cursor to a target (text, element id or coordinates).",
    )
    async def move_mouse(target: str | dict[str, Any]) -> dict:
        try:
            result = await engine.move_mouse(_target(target))
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.drag",
        description="Drag from one target to another (text, element ids or coordinates).",
    )
    async def drag(start: str | dict[str, Any], end: str | dict[str, Any], duration_s: float | None = None) -> dict:
        try:
            result = await engine.drag(_target(start), _target(end), duration_s=duration_s)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.scroll",
        description=(
            "Scroll the mouse wheel. Positive amount scrolls up, negative down. "
            "Optionally at specific coordinates (x, y)."
        ),
    )
    async def scroll(amount: int = 5, x: int | None = None, y: int | None = None) -> dict:
        try:
            result = await engine.scroll(int(amount), x=x, y=y)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.type",
        description=(
            "Type text at the currently focused input. Handles Unicode and "
            "multiline text (clipboard fallback used automatically). The typed "
            "content is never logged."
        ),
    )
    async def type_text(text: str, verify: bool | None = None) -> dict:
        try:
            result = await engine.type(text, verify=verify)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.press",
        description="Press a keyboard key ('enter', 'tab', 'escape', 'f5', ...).",
    )
    async def press(key: str) -> dict:
        try:
            result = await engine.press(key)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.hotkey",
        description=(
            "Press a key combination. Pass a string like 'ctrl+s' or a list "
            "like ['ctrl', 'shift', 't']."
        ),
    )
    async def hotkey(keys: str | list[str]) -> dict:
        try:
            result = await engine.hotkey(keys)
            return {"ok": True, **result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

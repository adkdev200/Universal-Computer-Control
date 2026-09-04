"""MCP tools: observation and search.

Each handler returns a JSON-serializable dict. Errors are converted into
``{"ok": false, "error": {...}}`` payloads so one failing capability never
crashes the MCP session.
"""

from __future__ import annotations

import logging
from typing import Any

from universal_computer.core.engine import ComputerControlEngine
from universal_computer.core.errors import UniversalComputerError
from universal_computer.logging import redact_text

logger = logging.getLogger("universal_computer.mcp")


def error_payload(exc: Exception) -> dict:
    message = redact_text(str(exc))
    if isinstance(exc, UniversalComputerError):
        return {"ok": False, "error": {"type": type(exc).__name__, "message": message}}
    logger.exception("unexpected tool error")
    return {
        "ok": False,
        "error": {"type": type(exc).__name__, "message": message or "unexpected error"},
    }


def _register(app: Any, engine: ComputerControlEngine) -> None:
    @app.tool(
        name="computer.observe",
        description=(
            "Observe the current computer state: screen geometry, active window, "
            "cursor, OCR'd on-screen text and accessibility UI elements. Levels: "
            "'minimal' (fast), 'normal' (screenshot+OCR+accessibility), 'full' "
            "(adds focused element details). This is the primary tool for "
            "understanding the screen before acting."
        ),
    )
    async def observe(level: str = "normal") -> dict:
        try:
            observation = await engine.observe(level)
            payload = observation.to_dict()
            return {"ok": True, **payload}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.screenshot",
        description=(
            "Take a screenshot of the whole virtual desktop, a specific monitor "
            "or a [left, top, right, bottom] region. Saves it to the persistence "
            "directory and returns the path."
        ),
    )
    async def screenshot(
        monitor: int | None = None,
        region: list[int] | None = None,
        return_image: bool = False,
    ) -> dict:
        try:
            region_tuple = None
            if region is not None:
                if len(region) != 4:
                    return {"ok": False, "error": {"type": "ValueError", "message": "region must be [left, top, right, bottom]"}}
                region_tuple = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
            result = await engine.screenshot(monitor=monitor, region=region_tuple, return_image=return_image)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.get_ui_tree",
        description=(
            "Get the accessibility tree of the active (or a named) window as a "
            "nested structure of roles, names and bounding boxes. Uses Windows "
            "UI Automation or Linux AT-SPI; returns an explanatory error when "
            "no accessibility backend is available."
        ),
    )
    async def get_ui_tree(max_depth: int = 8, window: str | None = None) -> dict:
        try:
            result = await engine.get_ui_tree(max_depth=max_depth, window=window)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.get_active_window",
        description="Get the title, application and process of the currently focused window.",
    )
    async def get_active_window() -> dict:
        try:
            result = await engine.get_active_window()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.find_text",
        description=(
            "Find text anywhere on screen (OCR + accessibility) with fuzzy "
            "matching. Returns matches with bounding boxes so you can click "
            "them. Tolerates case, arrows and small typos ('Continue' matches "
            "'Continue →')."
        ),
    )
    async def find_text(query: str, threshold: float = 0.55, limit: int = 10) -> dict:
        try:
            result = await engine.find_text(query, threshold=threshold, limit=limit)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.find_visual",
        description=(
            "Find a UI element visually: by image template name (from the "
            "configured template directory) or by natural-language description "
            "via the optional vision model. Returns a bounding box."
        ),
    )
    async def find_visual(
        description: str = "", template: str | None = None, threshold: float | None = None
    ) -> dict:
        try:
            result = await engine.find_visual(
                description=description, template=template, threshold=threshold
            )
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.wait_for",
        description=(
            "Wait until something changes on screen. Expectations: "
            "'any_change', 'window' (value = window title substring), "
            "'text' (value = OCR text substring). Use after actions to await "
            "UI reactions instead of blind sleeps."
        ),
    )
    async def wait_for(
        expectation: str = "any_change", value: str | None = None, timeout_s: float = 10.0
    ) -> dict:
        try:
            result = await engine.wait_for(expectation, value=value, timeout_s=timeout_s)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)


def register_observation_tools(app: Any, engine: ComputerControlEngine) -> None:
    _register(app, engine)

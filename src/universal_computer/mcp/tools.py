"""MCP tool registration aggregator.

The LLM sees a small, stable, capability-oriented API (design §31): all tools
are prefixed ``computer.`` and no backend name (uia/ocr/pyautogui/playwright)
ever leaks into the API surface.
"""

from __future__ import annotations

from typing import Any

from universal_computer.core.engine import ComputerControlEngine
from universal_computer.mcp.tools_input import register_input_tools
from universal_computer.mcp.tools_observation import register_observation_tools
from universal_computer.mcp.tools_system import register_system_tools


def register_all_tools(app: Any, engine: ComputerControlEngine) -> None:
    register_observation_tools(app, engine)
    register_input_tools(app, engine)
    register_system_tools(app, engine)


TOOL_NAMES = [
    # observation
    "computer.observe",
    "computer.screenshot",
    "computer.get_ui_tree",
    "computer.get_active_window",
    "computer.find_text",
    "computer.find_visual",
    "computer.wait_for",
    # mouse
    "computer.click",
    "computer.double_click",
    "computer.right_click",
    "computer.move_mouse",
    "computer.drag",
    "computer.scroll",
    # keyboard
    "computer.type",
    "computer.press",
    "computer.hotkey",
    # windows
    "computer.list_windows",
    "computer.focus_window",
    "computer.minimize_window",
    "computer.maximize_window",
    "computer.restore_window",
    "computer.close_window",
    # clipboard
    "computer.get_clipboard",
    "computer.set_clipboard",
    # applications / system
    "computer.launch_application",
    "computer.run_command",
    # administration
    "computer.backend_status",
    "computer.emergency_stop",
    "computer.reset_emergency_stop",
]

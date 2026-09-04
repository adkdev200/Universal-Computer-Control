"""MCP tools: windows, clipboard, applications, administration."""

from __future__ import annotations

from typing import Any

from universal_computer.core.engine import ComputerControlEngine
from universal_computer.mcp.tools_observation import error_payload


def register_system_tools(app: Any, engine: ComputerControlEngine) -> None:
    @app.tool(
        name="computer.list_windows",
        description="List all top-level OS windows with title, application, pid and state.",
    )
    async def list_windows() -> dict:
        try:
            result = await engine.list_windows()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.focus_window",
        description=(
            "Focus a real OS window by (partial) title or application name, "
            "e.g. 'Chrome'. Works without any application-specific integration."
        ),
    )
    async def focus_window(window: str) -> dict:
        try:
            result = await engine.focus_window(window)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(name="computer.minimize_window", description="Minimize a window by title/application.")
    async def minimize_window(window: str) -> dict:
        try:
            result = await engine.minimize_window(window)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(name="computer.maximize_window", description="Maximize a window by title/application.")
    async def maximize_window(window: str) -> dict:
        try:
            result = await engine.maximize_window(window)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(name="computer.restore_window", description="Restore a minimized/maximized window.")
    async def restore_window(window: str) -> dict:
        try:
            result = await engine.restore_window(window)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.close_window",
        description=(
            "Close a window gracefully (WM_CLOSE). Requires confirm=true; modal "
            "confirmation dialogs remain visible for the agent to handle."
        ),
    )
    async def close_window(window: str, confirm: bool = False) -> dict:
        try:
            result = await engine.close_window(window, confirm=confirm)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(name="computer.get_clipboard", description="Read the clipboard text.")
    async def get_clipboard() -> dict:
        try:
            result = await engine.get_clipboard()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.set_clipboard",
        description="Write text to the clipboard (useful as a typing fallback).",
    )
    async def set_clipboard(text: str) -> dict:
        try:
            result = await engine.set_clipboard(text)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.launch_application",
        description=(
            "Launch a GUI application ('chrome', 'code', a .desktop entry, or a "
            "full path). Subject to security.allowed_applications."
        ),
    )
    async def launch_application(command: str) -> dict:
        try:
            result = await engine.launch_application(command)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.run_command",
        description=(
            "Run a shell command (security.allowlist applies; see "
            "computer.backend_status). Requires confirm=true. Output is "
            "truncated and credential-redacted."
        ),
    )
    async def run_command(command: str, timeout_s: float | None = None, confirm: bool = False) -> dict:
        try:
            result = await engine.run_command(command, timeout_s=timeout_s, confirm=confirm)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.backend_status",
        description=(
            "Report health/availability of every backend (input, screenshot, "
            "accessibility, window management, clipboard, OCR, vision) plus "
            "security settings and emergency-stop state."
        ),
    )
    async def backend_status() -> dict:
        try:
            result = await engine.backend_status()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.emergency_stop",
        description=(
            "EMERGENCY STOP: immediately halt all automation (checked before "
            "every action and attempt). Persists until reset_emergency_stop."
        ),
    )
    async def emergency_stop() -> dict:
        try:
            result = await engine.emergency_stop()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

    @app.tool(
        name="computer.reset_emergency_stop",
        description="Clear a previously triggered emergency stop.",
    )
    async def reset_emergency_stop() -> dict:
        try:
            result = await engine.reset_emergency_stop()
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)

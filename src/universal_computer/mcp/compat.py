"""Compatibility layer for the ``mcp`` SDK (v1 FastMCP and v2 MCPServer).

Both generations expose the same decorator-style API surface we need
(``tool`` decorator, ``run()``, ``list_tools()``, ``call_tool()``); only the
import path changed in v2.
"""

from __future__ import annotations

from typing import Any

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]

    SDK_VERSION = 1
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[no-redef]

    SDK_VERSION = 2

__all__ = ["FastMCP", "SDK_VERSION", "extract_result"]


def extract_result(result: Any) -> dict:
    """Normalize a ``call_tool`` result into a python dict.

    v1 returns ``list[TextContent]``; v2 returns a ``CallToolResult`` whose
    content holds ``TextContent`` items carrying our JSON payloads.
    """
    if isinstance(result, dict):
        return result
    content = getattr(result, "content", result)
    for item in content or []:
        text = getattr(item, "text", None)
        if text:
            import json  # noqa: PLC0415

            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return {"ok": False, "error": {"type": "EmptyResult", "message": "tool returned no payload"}}

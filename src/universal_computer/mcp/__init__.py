"""MCP wiring for the Universal Computer Control server."""

from universal_computer.mcp.compat import SDK_VERSION, FastMCP  # noqa: F401
from universal_computer.mcp.tools import TOOL_NAMES, register_all_tools

__all__ = ["FastMCP", "SDK_VERSION", "TOOL_NAMES", "register_all_tools"]

#!/usr/bin/env python3
"""Minimal MCP client demo (design §44).

Spawns the Universal Computer Control MCP server over stdio, performs the
MCP handshake, lists tools and calls a few safe ones. Run from the project
root::

    python examples/mcp_client_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "universal_computer"],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "UCC_LOGGING__CONSOLE": "false"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to Universal Computer Control MCP")

            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} tools available:")
            for tool in tools.tools:
                print(f"  {tool.name}")

            for name, args in (
                ("computer.backend_status", {}),
                ("computer.observe", {"level": "minimal"}),
                ("computer.list_windows", {}),
            ):
                print(f"\n--- {name} ---")
                result = await session.call_tool(name, args)
                for item in result.content:
                    if getattr(item, "text", None):
                        try:
                            print(json.dumps(json.loads(item.text), indent=1)[:1200])
                        except json.JSONDecodeError:
                            print(item.text[:400])


if __name__ == "__main__":
    asyncio.run(main())

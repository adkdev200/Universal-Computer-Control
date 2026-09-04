"""End-to-end integration test: real MCP server over stdio, real client.

Starts ``python -m universal_computer`` as a subprocess and talks MCP to it —
proving the server boots, handshakes, lists tools and executes a tool without
any display or browser-specific MCP present (design §29, §44).
"""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"

pytestmark = pytest.mark.timeout(90)


def _server_env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["UCC_HOME"] = str(tmp_path / "it-home")
    env["UCC_LOGGING__CONSOLE"] = "false"
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env


async def _with_session(tmp_path: Path, body):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "universal_computer"],
        env=_server_env(tmp_path),
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await body(session)


class TestStdioServer:
    async def test_initialize_and_list_tools(self, tmp_path):
        async def check(session: ClientSession):
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "computer.observe" in names
            assert "computer.click" in names
            assert len(names) >= 25

        await _with_session(tmp_path, check)

    async def test_backend_status_tool(self, tmp_path):
        async def check(session: ClientSession):
            result = await session.call_tool("computer.backend_status", {})
            payload = json.loads(result.content[0].text)
            assert payload["ok"] is True
            assert "backends" in payload

        await _with_session(tmp_path, check)

    async def test_observe_in_degraded_mode(self, tmp_path):
        async def check(session: ClientSession):
            result = await session.call_tool("computer.observe", {"level": "minimal"})
            payload = json.loads(result.content[0].text)
            # Headless CI has no display: the tool must answer honestly,
            # not crash the server.
            assert "ok" in payload

        await _with_session(tmp_path, check)

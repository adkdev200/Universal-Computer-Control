"""Unit tests for the MCP tool layer (§6, §31)."""

import pytest

from tests.conftest import build_engine
from universal_computer.mcp.compat import extract_result
from universal_computer.mcp.tools import TOOL_NAMES
from universal_computer.server import create_mcp_app


@pytest.fixture
def app(config, mocks, monkeypatch, tmp_path):
    monkeypatch.setenv("UCC_HOME", str(tmp_path / "mcp-home"))
    engine = build_engine(config, mocks)
    return create_mcp_app(engine=engine)


class TestToolSurface:
    async def test_all_tools_registered(self, app):
        tools = await app.list_tools()
        names = {t.name for t in tools}
        for expected in TOOL_NAMES:
            assert expected in names, f"missing tool {expected}"
        assert len(names) == len(TOOL_NAMES)

    async def test_tools_have_descriptions(self, app):
        tools = await app.list_tools()
        for tool in tools:
            assert tool.description, f"{tool.name} has no description"

    async def test_no_backend_names_leak_into_api(self, app):
        tools = await app.list_tools()
        forbidden = ("pyautogui", "uia", "atspi", "ocr", "playwright", "opencv")
        for tool in tools:
            for word in forbidden:
                assert word not in tool.name
                assert word not in (tool.description or "").lower() or word == "ocr"

    async def test_schemas_are_objects(self, app):
        tools = await app.list_tools()
        click = next(t for t in tools if t.name == "computer.click")
        assert click.input_schema["type"] == "object"
        assert "target" in click.input_schema["properties"]


class TestToolCalls:
    async def test_observe(self, app, mocks):
        mocks.a11y.elements = []
        result = extract_result(await app.call_tool("computer.observe", {"level": "minimal"}))
        assert result["ok"] is True
        assert result["screen"]["width"] == 1280

    async def test_click_coordinates(self, app, mocks):
        result = extract_result(
            await app.call_tool("computer.click", {"target": {"x": 10, "y": 20}})
        )
        assert result["ok"] is True
        assert result["success"] is True
        assert mocks.input.clicks == [(10, 20, "left")]

    async def test_find_text_empty(self, app, mocks):
        result = extract_result(await app.call_tool("computer.find_text", {"query": "Nope"}))
        assert result["ok"] is True
        assert result["count"] == 0

    async def test_list_windows(self, app, mocks):
        mocks.windows.windows.append(mocks.windows.make_window("Terminal"))
        result = extract_result(await app.call_tool("computer.list_windows", {}))
        assert result["ok"] is True
        assert result["count"] == 1

    async def test_backend_status_shape(self, app):
        result = extract_result(await app.call_tool("computer.backend_status", {}))
        assert result["ok"] is True
        assert "backends" in result
        assert "vision" in result

    async def test_error_payload_on_failure(self, app, mocks):
        result = extract_result(await app.call_tool("computer.focus_window", {"window": "ghost"}))
        assert result["ok"] is False
        assert result["error"]["type"] == "WindowNotFoundError"

    async def test_emergency_stop_flow(self, app):
        stopped = extract_result(await app.call_tool("computer.emergency_stop", {}))
        assert stopped["ok"] is True
        blocked = extract_result(await app.call_tool("computer.click", {"target": {"x": 1, "y": 2}}))
        assert blocked["ok"] is False
        assert blocked["error"]["type"] == "EmergencyStopError"
        reset = extract_result(await app.call_tool("computer.reset_emergency_stop", {}))
        assert reset["ok"] is True

    async def test_type_tool(self, app, mocks):
        result = extract_result(await app.call_tool("computer.type", {"text": "abc"}))
        assert result["ok"] is True
        assert result["success"] is True
        assert mocks.input.typed == ["abc"]
        # target must be redacted in the result payload
        assert "abc" not in str(result.get("target"))

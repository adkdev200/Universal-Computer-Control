"""Unit tests for window management tools (§19) and clipboard."""

import pytest

from tests.conftest import build_engine
from universal_computer.core.errors import WindowNotFoundError


@pytest.fixture
def populated(mocks):
    mocks.windows.windows = [
        mocks.windows.make_window("Google Chrome", application="chrome.exe", active=True),
        mocks.windows.make_window("Untitled - VS Code", application="code.exe"),
        mocks.windows.make_window("Files", application="nautilus"),
    ]
    return mocks


class TestWindowOps:
    async def test_list_windows(self, config, populated):
        engine = build_engine(config, populated)
        result = await engine.list_windows()
        assert result["count"] == 3
        titles = [w["title"] for w in result["windows"]]
        assert "Google Chrome" in titles

    async def test_get_active_window(self, config, populated):
        engine = build_engine(config, populated)
        result = await engine.get_active_window()
        assert result["window"]["title"] == "Google Chrome"

    async def test_focus_window_partial_title(self, config, populated):
        engine = build_engine(config, populated)
        result = await engine.focus_window("chrome")
        assert result["success"] is True
        assert ("focus", "Google Chrome") in populated.windows.ops
        active = await engine.get_active_window()
        assert active["window"]["title"] == "Google Chrome"

    async def test_focus_unknown_window(self, config, populated):
        engine = build_engine(config, populated)
        with pytest.raises(WindowNotFoundError):
            await engine.focus_window("Internet Explorer")

    async def test_minimize_maximize_restore(self, config, populated):
        engine = build_engine(config, populated)
        assert (await engine.minimize_window("vs code"))["success"] is True
        assert (await engine.maximize_window("vs code"))["success"] is True
        assert (await engine.restore_window("vs code"))["success"] is True
        ops = [op[0] for op in populated.windows.ops]
        assert ops == ["minimize", "maximize", "restore"]

    async def test_close_window_requires_confirmation(self, config, populated):
        config.security.confirmation_required = ["close_window"]
        engine = build_engine(config, populated)
        result = await engine.close_window("Files")
        assert result["success"] is False
        assert "confirm" in (result["error"] or "")
        assert len(populated.windows.windows) == 3

    async def test_close_window_with_confirmation(self, config, populated):
        config.security.confirmation_required = ["close_window"]
        engine = build_engine(config, populated)
        result = await engine.close_window("Files", confirm=True)
        assert result["success"] is True
        assert len(populated.windows.windows) == 2


class TestClipboard:
    async def test_roundtrip(self, config, mocks):
        engine = build_engine(config, mocks)
        set_result = await engine.set_clipboard("some text")
        assert set_result["success"] is True
        get_result = await engine.get_clipboard()
        assert get_result["text"] == "some text"
        assert get_result["length"] == 9

    async def test_set_clipboard_not_logged_verbatim(self, config, mocks):
        secret = "super-secret-value"
        engine = build_engine(config, mocks)
        await engine.set_clipboard(secret)
        entries = engine.recent_actions()
        clip_entries = [e for e in entries if e["action"] == "set_clipboard"]
        assert clip_entries and clip_entries[0]["length"] == len(secret)
        assert all(secret not in str(e) for e in entries)

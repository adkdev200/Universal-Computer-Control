"""Unit tests for engine actions: click/type/press with fallbacks (§11, §15, §16)."""

from tests.conftest import FakeOCRProvider, build_engine, ocr_result, save_element


class TestClick:
    async def test_click_text_prefers_semantic_invoke(self, config, mocks):
        mocks.a11y.elements = [save_element("Continue", can_invoke=True)]
        engine = build_engine(config, mocks)
        result = await engine.click("Continue")
        assert result.success is True
        assert result.method == "semantic-invoke"
        assert mocks.a11y.invoked == ["Continue"]
        assert mocks.input.clicks == []  # no physical click needed

    async def test_click_text_without_invoke_uses_coordinates(self, config, mocks):
        mocks.a11y.elements = [save_element("Continue", can_invoke=False)]
        engine = build_engine(config, mocks)
        result = await engine.click("Continue")
        assert result.success is True
        assert result.method == "coordinates"
        assert mocks.input.clicks == [(550, 420, "left")]

    async def test_click_coordinates_direct(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 500, "y": 300})
        assert result.success is True
        assert result.coordinates == (500, 300)
        assert mocks.input.clicks == [(500, 300, "left")]

    async def test_double_and_right_click(self, config, mocks):
        engine = build_engine(config, mocks)
        await engine.double_click({"x": 1, "y": 2})
        await engine.right_click({"x": 3, "y": 4})
        assert mocks.input.double_clicks == [(1, 2)]
        assert mocks.input.right_clicks == [(3, 4)]

    async def test_click_falls_back_to_alternative_coordinates(self, config, mocks):
        ocr = FakeOCRProvider(
            [
                ocr_result("Continue", (1000, 700, 1120, 740)),
                ocr_result("Continue Now", (1000, 760, 1160, 800)),
            ]
        )
        mocks.a11y.elements = []
        mocks.input.fail_next_clicks = 1  # first coordinate attempt fails
        engine = build_engine(config, mocks, ocr=ocr)
        result = await engine.click("Continue")
        assert result.success is True
        assert len(result.attempts) == 2
        assert result.attempts[0].success is False
        assert result.attempts[1].success is True
        # the failed first attempt never reached the physical backend
        assert mocks.input.clicks == [(1080, 780, "left")]

    async def test_click_unresolvable_target_raises(self, config, mocks):
        import pytest

        from universal_computer.core.errors import TargetResolutionError

        mocks.a11y.elements = []
        engine = build_engine(config, mocks)
        with pytest.raises(TargetResolutionError):
            await engine.click("nonexistent-widget")

    async def test_click_failure_records_attempts(self, config, mocks):
        mocks.input.fail_next_clicks = 99
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 1, "y": 1})
        assert result.success is False
        assert "simulated click failure" in (result.error or "")


class TestType:
    async def test_ascii_types_via_keyboard(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.type("hello world")
        assert result.success is True
        assert result.method == "keyboard"
        assert mocks.input.typed == ["hello world"]

    async def test_unicode_falls_back_to_clipboard(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.type("héllo 🌍")
        assert result.success is True
        assert result.method == "clipboard"
        assert mocks.input.typed == []  # not typed directly
        assert mocks.input.hotkeys == [("ctrl", "v")]
        assert mocks.clipboard._text == "héllo 🌍"

    async def test_multiline_ascii(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.type("line1\nline2")
        assert result.success is True
        assert mocks.input.typed == ["line1\nline2"]


class TestPressHotkeyScrollMoveDrag:
    async def test_press(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.press("enter")
        assert result.success is True
        assert mocks.input.pressed == ["enter"]

    async def test_hotkey_string(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.hotkey("ctrl+s")
        assert result.success is True
        assert mocks.input.hotkeys == [("ctrl", "s")]

    async def test_hotkey_list(self, config, mocks):
        engine = build_engine(config, mocks)
        await engine.hotkey(["ctrl", "shift", "t"])
        assert mocks.input.hotkeys == [("ctrl", "shift", "t")]

    async def test_scroll(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.scroll(-3)
        assert result["success"] is True
        assert mocks.input.scrolls == [(-3, None, None)]

    async def test_move_mouse(self, config, mocks):
        mocks.a11y.elements = [save_element("Menu")]
        engine = build_engine(config, mocks)
        result = await engine.move_mouse("Menu")
        assert result["success"] is True
        assert mocks.input.moves == [(550, 420)]

    async def test_drag(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.drag({"x": 10, "y": 10}, {"x": 200, "y": 300})
        assert result["success"] is True
        assert mocks.input.drags == [((10, 10), (200, 300))]

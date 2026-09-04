"""Unit tests for verification and recovery (§17, §18)."""

from PIL import Image

from tests.conftest import build_engine
from universal_computer.core.verifier import dhash


class TestVerifier:
    def _signature(self, engine, **overrides):
        base = {
            "window": "Chrome|chrome.exe",
            "focused": None,
            "ocr": ["continue", "cancel"],
            "hash": "abcd" * 4,
        }
        base.update(overrides)
        return base

    async def test_identical_state_unchanged(self, config, mocks):
        engine = build_engine(config, mocks)
        before = self._signature(engine)
        result = engine.verifier.compare(before, dict(before))
        assert result.performed is True
        assert result.changed is False
        assert result.similarity == pytest.approx(1.0)

    async def test_ocr_change_detected(self, config, mocks):
        engine = build_engine(config, mocks)
        before = self._signature(engine)
        after = self._signature(engine, ocr=["continue", "cancel", "new dialog"])
        result = engine.verifier.compare(before, after)
        assert result.changed is True
        assert "OCR" in (result.details or "")

    async def test_window_change_detected(self, config, mocks):
        engine = build_engine(config, mocks)
        before = self._signature(engine)
        after = self._signature(engine, window="Settings|gnome-control-center")
        result = engine.verifier.compare(before, after)
        assert result.changed is True

    async def test_missing_state_not_performed(self, config, mocks):
        engine = build_engine(config, mocks)
        result = engine.verifier.compare(None, None)
        assert result.performed is False

    def test_dhash_stable_and_discriminating(self, tmp_path):
        image_a = Image.new("L", (64, 64), 0)
        for x in range(0, 64, 4):
            for y in range(0, 64, 2):
                image_a.putpixel((x, y), 255)
        image_b = Image.new("L", (64, 64), 255)
        path_a = tmp_path / "a.png"
        path_b = tmp_path / "b.png"
        image_a.save(path_a)
        image_b.save(path_b)
        hash_a = dhash(str(path_a))
        hash_b = dhash(str(path_b))
        assert hash_a is not None
        assert hash_a == dhash(str(path_a))  # stable
        assert hash_a != hash_b


import pytest  # noqa: E402  (import after helpers is fine for readability)


class TestRecovery:
    async def test_duplicate_identical_action_suppressed(self, config, mocks):
        config.engine.duplicate_window_ms = 5000
        engine = build_engine(config, mocks)
        first = await engine.click({"x": 10, "y": 20})
        assert first.success is True
        second = await engine.click({"x": 10, "y": 20})
        assert second.success is False
        assert "duplicate" in (second.error or "")
        # only one physical click happened
        assert len(mocks.input.clicks) == 1

    async def test_duplicate_guard_allows_after_window(self, config, mocks):
        config.engine.duplicate_window_ms = 10
        engine = build_engine(config, mocks)
        await engine.click({"x": 10, "y": 20})
        import asyncio

        await asyncio.sleep(0.05)
        again = await engine.click({"x": 10, "y": 20})
        assert again.success is True
        assert len(mocks.input.clicks) == 2

    async def test_failing_backend_recorded(self, config, mocks):
        config.engine.duplicate_window_ms = 0
        mocks.input.fail_next_clicks = 3
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 5, "y": 5})
        assert result.success is False
        # mock-input recorded failures with the manager
        backend = engine.backend_manager.get("mock-input")
        assert backend._failure_count >= 1

    async def test_strict_verification_marks_unchanged_action_failed(self, config, mocks):
        config.engine.verification_mode = "strict"
        config.engine.verify_actions = True
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 42, "y": 42})
        # clicking empty screen changes nothing -> strict mode fails the action
        assert result.success is False
        assert "no observable change" in (result.error or "")
        assert result.verification is not None
        assert result.verification.changed is False

    async def test_basic_verification_reports_but_does_not_fail(self, config, mocks):
        config.engine.verification_mode = "basic"
        config.engine.verify_actions = True
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 42, "y": 42})
        assert result.success is True
        assert result.verification is not None

    async def test_click_changes_state_passes_strict_verification(self, config, mocks):
        config.engine.verification_mode = "strict"
        config.engine.verify_actions = True
        config.engine.duplicate_window_ms = 0

        # A click on (60, 30) "activates" a new window in the mock world.
        def spawn_dialog(x, y, button):
            if (x, y) == (60, 30):
                mocks.windows.windows.append(
                    mocks.windows.make_window("Dialog", application="app", active=True)
                )

        mocks.input.on_click.append(spawn_dialog)
        engine = build_engine(config, mocks)
        result = await engine.click({"x": 60, "y": 30})
        assert result.success is True
        assert result.verification.changed is True
        assert result.verification.success is True

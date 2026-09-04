"""Unit tests for the observer and element registry (§7)."""

from tests.conftest import Mocks, build_engine, save_element
from universal_computer.core.observer import ElementRegistry
from universal_computer.models.element import UIElement


class TestObserver:
    async def test_minimal_observation_has_screen_and_window(self, config, mocks):
        engine = build_engine(config, mocks)
        mocks.windows.windows.append(
            mocks.windows.make_window("VS Code", application="code", active=True)
        )
        observation = await engine.observe("minimal")
        assert observation.id.startswith("obs_")
        assert observation.screen.width == 1280
        assert observation.active_window.title == "VS Code"
        assert observation.elements == []

    async def test_normal_observation_includes_elements(self, config, mocks):
        mocks.a11y.elements = [save_element("Save"), save_element("Cancel")]
        mocks.a11y.elements[1] = save_element("Cancel")
        engine = build_engine(config, mocks)
        observation = await engine.observe("normal")
        names = [e.name for e in observation.elements]
        assert "Save" in names and "Cancel" in names

    async def test_notes_when_accessibility_missing(self, config, mocks):
        mocks.a11y.elements = []
        engine = build_engine(config, mocks)
        observation = await engine.observe("normal")
        # empty elements is fine; no crash, notes mention nothing fatal
        assert observation.elements == []

    async def test_observation_degrades_without_screenshot_backend(self, config):
        mocks = Mocks()
        engine = build_engine(config, mocks)
        engine.backend_manager.unregister("mock-screen")
        observation = await engine.observe("normal")
        assert observation.screen is None
        assert any("screenshot" in note for note in observation.notes)

    async def test_full_level_gathers_focused(self, config, mocks):
        mocks.a11y.elements = [save_element("Field")]
        mocks.a11y.focused = mocks.a11y.elements[0]
        engine = build_engine(config, mocks)
        observation = await engine.observe("full")
        assert observation.focused_element is not None
        assert observation.focused_element.name == "Field"


class TestElementRegistry:
    def test_register_and_resolve(self):
        registry = ElementRegistry(max_observations=3)
        element = UIElement(id="element_1", name="X")
        from universal_computer.models.observation import Observation

        registry.register(Observation(id="obs_1", elements=[element]))
        found = registry.resolve_element("element_1")
        assert found is not None
        assert found[0].name == "X"
        assert found[1] == "obs_1"

    def test_evicts_old_observations(self):
        registry = ElementRegistry(max_observations=2)
        from universal_computer.models.observation import Observation

        for i in range(4):
            registry.register(Observation(id=f"obs_{i}"))
        assert registry.get_observation("obs_1") is None
        assert registry.get_observation("obs_3") is not None
        assert registry.last_observation().id == "obs_3"

    def test_resolve_unknown_returns_none(self):
        registry = ElementRegistry()
        assert registry.resolve_element("element_99") is None

"""Unit tests for the unified element/observation models (design §7, §8)."""

from universal_computer.models.element import (
    BoundingBox,
    ElementSource,
    UIElement,
    normalize_role,
)
from universal_computer.models.observation import Observation, ObservationLevel
from universal_computer.models.window import WindowInfo, WindowState


class TestBoundingBox:
    def test_ltrb_serialization(self):
        box = BoundingBox(x=100, y=50, width=120, height=60)
        assert box.model_dump(mode="json") == [100, 50, 220, 110]

    def test_accepts_ltrb_list(self):
        box = BoundingBox.model_validate([1050, 700, 1170, 750])
        assert (box.x, box.y, box.width, box.height) == (1050, 700, 120, 50)

    def test_center(self):
        assert BoundingBox(x=10, y=20, width=30, height=40).center() == (25, 40)

    def test_contains(self):
        box = BoundingBox(x=0, y=0, width=10, height=10)
        assert box.contains(5, 5)
        assert not box.contains(10, 5)

    def test_intersects(self):
        a = BoundingBox(x=0, y=0, width=10, height=10)
        b = BoundingBox(x=5, y=5, width=10, height=10)
        c = BoundingBox(x=20, y=20, width=5, height=5)
        assert a.intersects(b)
        assert not a.intersects(c)


class TestRoleNormalization:
    def test_uia_roles(self):
        assert normalize_role("PushButton") == "button"
        assert normalize_role("push button") == "button"
        assert normalize_role("Edit") == "text field"
        assert normalize_role("Hyperlink") == "link"

    def test_atspi_roles(self):
        assert normalize_role("toggle button") == "button"
        assert normalize_role("page tab") == "tab item"

    def test_unknown_passthrough(self):
        assert normalize_role("weird widget") == "weird widget"
        assert normalize_role(None) == "unknown"


class TestUIElement:
    def test_searchable_text(self):
        element = UIElement(id="element_1", role="button", name="Save", text="Save file")
        assert "Save" in element.searchable_text()
        assert "file" in element.searchable_text()

    def test_semantic_invoke_flag(self):
        element = UIElement(
            id="element_1",
            role="button",
            name="OK",
            source=ElementSource.UIA,
            metadata={"can_invoke": True},
        )
        assert element.can_invoke_semantically()

    def test_clickable_from_role(self):
        element = UIElement(id="element_2", role="push button")
        assert element.is_clickable()

    def test_confidence_bounds(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UIElement(id="x", confidence=2.0)


class TestObservation:
    def test_element_by_id(self):
        element = UIElement(id="element_3", role="button", name="Go")
        observation = Observation(id="obs_1", elements=[element])
        assert observation.element_by_id("element_3") is element
        assert observation.element_by_id("nope") is None

    def test_json_roundtrip_bbox(self):
        import json

        element = UIElement(
            id="element_1", role="button", name="X", bbox=BoundingBox(x=1, y=2, width=3, height=4)
        )
        observation = Observation(id="obs_1", elements=[element], level=ObservationLevel.NORMAL)
        payload = json.loads(json.dumps(observation.to_dict()))
        assert payload["elements"][0]["bbox"] == [1, 2, 4, 6]

    def test_level_ranking(self):
        assert ObservationLevel.MINIMAL.rank < ObservationLevel.NORMAL.rank
        assert ObservationLevel.NORMAL.rank < ObservationLevel.FULL.rank


class TestWindowInfo:
    def test_matches_title_and_app(self):
        window = WindowInfo(title="Google Chrome", application="chrome.exe")
        assert window.matches("chrome")
        assert window.matches("GOOGLE")
        assert not window.matches("firefox")

    def test_state_default(self):
        window = WindowInfo(title="X")
        assert window.state == WindowState.UNKNOWN

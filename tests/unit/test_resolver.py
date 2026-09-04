"""Unit tests for target resolution (§5, §15)."""

import pytest

from tests.conftest import build_engine, ocr_result, save_element
from universal_computer.core.errors import TargetResolutionError
from universal_computer.core.resolver import parse_coordinates


class TestParseCoordinates:
    def test_dict(self):
        assert parse_coordinates({"x": 5, "y": 6}) == (5, 6)

    def test_list(self):
        assert parse_coordinates([10, 20]) == (10, 20)

    def test_tuple(self):
        assert parse_coordinates((1, 2)) == (1, 2)

    def test_garbage(self):
        assert parse_coordinates("hello") is None
        assert parse_coordinates({"a": 1}) is None
        assert parse_coordinates([1, 2, 3]) is None


class TestResolve:
    async def test_explicit_coordinates(self, config, mocks):
        engine = build_engine(config, mocks)
        resolved = await engine.resolver.resolve({"x": 100, "y": 200})
        assert resolved.kind == "coordinates"
        assert resolved.coordinates == (100, 200)
        assert resolved.confidence == 1.0

    async def test_element_id_from_registry(self, config, mocks):
        mocks.a11y.elements = [save_element("Submit")]
        engine = build_engine(config, mocks)
        observation = await engine.observe("normal")
        element_id = observation.elements[0].id
        resolved = await engine.resolver.resolve(element_id)
        assert resolved.kind == "element"
        assert resolved.element.name == "Submit"
        assert resolved.coordinates is not None

    async def test_unknown_element_id(self, config, mocks):
        engine = build_engine(config, mocks)
        with pytest.raises(TargetResolutionError):
            await engine.resolver.resolve("element_999")

    async def test_text_via_accessibility(self, config, mocks):
        mocks.a11y.elements = [save_element("Continue", can_invoke=True)]
        engine = build_engine(config, mocks)
        resolved = await engine.resolver.resolve("Continue")
        assert resolved.kind == "element"
        assert resolved.semantic_available is True
        assert resolved.method.startswith("accessibility")

    async def test_text_fuzzy_matches_arrow_variant(self, config, mocks):
        mocks.a11y.elements = [save_element("Continue →", can_invoke=False)]
        engine = build_engine(config, mocks)
        resolved = await engine.resolver.resolve("Continue")
        assert resolved.element is not None
        assert resolved.element.name == "Continue →"
        assert resolved.semantic_available is False

    async def test_text_via_ocr_when_no_accessibility(self, config, mocks):
        from tests.conftest import FakeOCRProvider

        ocr = FakeOCRProvider(
            [ocr_result("Continue", (1000, 700, 1120, 740)), ocr_result("Cancel", (1000, 750, 1080, 780))]
        )
        mocks.a11y.elements = []
        engine = build_engine(config, mocks, ocr=ocr)
        resolved = await engine.resolver.resolve("continue")
        assert resolved.kind == "text"
        assert resolved.method == "ocr"
        assert resolved.coordinates == (1060, 720)

    async def test_ocr_alternatives_present(self, config, mocks):
        from tests.conftest import FakeOCRProvider

        ocr = FakeOCRProvider(
            [
                ocr_result("Continue", (1000, 700, 1120, 740)),
                ocr_result("Continue Now", (1000, 760, 1120, 800)),
            ]
        )
        mocks.a11y.elements = []
        engine = build_engine(config, mocks, ocr=ocr)
        resolved = await engine.resolver.resolve("Continue")
        assert resolved.kind == "text"
        # alternatives exclude the primary hit but include the other OCR hit
        assert any("ocr" in alt[1] for alt in resolved.alternatives)

    async def test_vlm_fallback_when_nothing_else_matches(self, config, mocks):
        from tests.mocks.mock_backends import FakeVLM

        mocks.a11y.elements = []
        engine = build_engine(config, mocks, vlm=FakeVLM(bbox=(10, 20, 110, 60)))
        resolved = await engine.resolver.resolve("the blue submit button")
        assert resolved.kind == "vision"
        assert resolved.coordinates == (60, 40)

    async def test_no_match_raises_with_reasons(self, config, mocks):
        mocks.a11y.elements = []
        engine = build_engine(config, mocks)
        with pytest.raises(TargetResolutionError) as exc_info:
            await engine.resolver.resolve("does-not-exist")
        assert "accessibility" in str(exc_info.value)

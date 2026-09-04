"""Shared fixtures: a fully mocked engine wired through real DI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.mocks.mock_backends import (
    FakeOCRProvider,
    MockAccessibilityBackend,
    MockClipboardBackend,
    MockInputBackend,
    MockScreenshotBackend,
    MockWindowBackend,
    make_element,
)
from universal_computer.config import load_config
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.engine import ComputerControlEngine
from universal_computer.models.element import BoundingBox
from universal_computer.models.observation import OCRResult
from universal_computer.vision.base import VisionServices


@dataclass
class Mocks:
    input: MockInputBackend = field(default_factory=MockInputBackend)
    screen: MockScreenshotBackend = field(default_factory=MockScreenshotBackend)
    a11y: MockAccessibilityBackend = field(default_factory=MockAccessibilityBackend)
    windows: MockWindowBackend = field(default_factory=MockWindowBackend)
    clipboard: MockClipboardBackend = field(default_factory=MockClipboardBackend)


@pytest.fixture
def config(tmp_path: Path):
    cfg = load_config()
    cfg.persistence.home = str(tmp_path / "ucm-home")
    cfg.engine.duplicate_window_ms = 0  # disabled unless a test opts in
    cfg.engine.observation_cache_ms = 0  # always observe fresh
    cfg.engine.verify_actions = False  # opt-in per test
    cfg.security.mode = "permissive"
    return cfg


def build_engine(cfg, mocks: Mocks, ocr: FakeOCRProvider | None = None, vlm=None) -> ComputerControlEngine:
    manager = BackendManager(cfg.backends)
    manager.register(mocks.screen)
    manager.register(mocks.input)
    manager.register(mocks.a11y)
    manager.register(mocks.windows)
    manager.register(mocks.clipboard)
    vision = VisionServices(ocr=ocr, templates=None, vlm=vlm, config=cfg.vision)
    return ComputerControlEngine(cfg, backend_manager=manager, vision=vision)


@pytest.fixture
def mocks() -> Mocks:
    return Mocks()


@pytest.fixture
def engine(config, mocks) -> ComputerControlEngine:
    return build_engine(config, mocks)


def ocr_result(text: str, ltrb: tuple[int, int, int, int], confidence: float = 0.95) -> OCRResult:
    return OCRResult(text=text, bbox=BoundingBox.model_validate(list(ltrb)), confidence=confidence)


def save_element(name: str = "Save", can_invoke: bool = True):
    return make_element(
        name,
        role="button",
        bbox=(500, 400, 600, 440),
        can_invoke=can_invoke,
    )

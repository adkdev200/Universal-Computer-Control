"""Vision abstractions: OCR providers, visual locators, vision-language models.

Providers are pluggable (§12-14 of the design): the engine talks to
:class:`OCRProvider`, :class:`VisionProvider` and :class:`VisionLanguageModel`
interfaces only. First-party implementations: Tesseract/EasyOCR (OCR),
OpenCV template matching (visual) and an OpenAI-compatible HTTP VLM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from PIL.Image import Image
from pydantic import BaseModel

from universal_computer.config import OCRConfig, VisionConfig
from universal_computer.core.errors import VisionUnavailableError
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.models.observation import OCRResult

logger = get_logger("vision")


class VisionResult(BaseModel):
    """Outcome of a visual/vision-model locate operation."""

    found: bool = False
    bbox: BoundingBox | None = None
    confidence: float = 0.0
    method: str = "none"
    details: str | None = None


class OCRProvider(ABC):
    """Detects text in images and returns positioned, scored results."""

    name: str = "ocr"

    @abstractmethod
    def detect_text(self, image: Image, region: BoundingBox | None = None) -> list[OCRResult]: ...

    @abstractmethod
    def is_available(self) -> bool: ...


class VisionProvider(ABC):
    """Locates elements in an image (template matching, embeddings, ...)."""

    name: str = "vision"

    @abstractmethod
    def locate(
        self,
        image: Image,
        description: str | None = None,
        template_path: Path | None = None,
        threshold: float = 0.8,
    ) -> VisionResult: ...

    @abstractmethod
    def is_available(self) -> bool: ...


class VisionLanguageModel(ABC):
    """Optional semantic locator backed by a configured vision model."""

    name: str = "vlm"

    @abstractmethod
    async def locate_element(self, screenshot: bytes | Image | Path, description: str) -> VisionResult: ...

    @abstractmethod
    def is_available(self) -> bool: ...


class VisionServices:
    """Holds the configured providers; the engine's single vision entry point."""

    def __init__(
        self,
        ocr: OCRProvider | None,
        templates: VisionProvider | None,
        vlm: VisionLanguageModel | None,
        config: VisionConfig,
    ) -> None:
        self.ocr = ocr
        self.templates = templates
        self.vlm = vlm
        self.config = config

    @classmethod
    def from_config(cls, vision: VisionConfig, ocr: OCRConfig) -> VisionServices:
        ocr_provider = _build_ocr(vision, ocr)
        templates = _build_templates(vision)
        vlm = _build_vlm(vision)
        return cls(ocr=ocr_provider, templates=templates, vlm=vlm, config=vision)

    # -- OCR -------------------------------------------------------------------
    def ocr_available(self) -> bool:
        return self.ocr is not None and self.ocr.is_available()

    def run_ocr(self, image: Image, region: BoundingBox | None = None) -> list[OCRResult]:
        if not self.ocr_available():
            raise VisionUnavailableError(
                "no OCR provider available; install pytesseract + tesseract-ocr "
                "(pip install '.[ocr]') or configure another provider"
            )
        assert self.ocr is not None
        try:
            return self.ocr.detect_text(image, region=region)
        except Exception as exc:  # noqa: BLE001 - OCR failures degrade to []
            logger.warning("OCR provider '%s' failed: %s", self.ocr.name, exc)
            return []

    # -- templates ---------------------------------------------------------------
    def template_path_for(self, name: str) -> Path | None:
        if not self.config.template_dir:
            return None
        directory = Path(self.config.template_dir).expanduser()
        for suffix in ("", ".png", ".jpg", ".jpeg", ".bmp"):
            candidate = directory / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def templates_available(self) -> bool:
        return (
            self.templates is not None
            and self.templates.is_available()
            and bool(self.config.template_dir)
        )

    def find_template(self, image: Image, name: str, threshold: float | None = None) -> VisionResult:
        if not self.templates_available():
            raise VisionUnavailableError(
                "no template-matching provider available; set vision.template_dir and "
                "install opencv-python (pip install '.[vision]')"
            )
        assert self.templates is not None
        path = self.template_path_for(name)
        if path is None:
            return VisionResult(
                found=False, method="opencv-template", details=f"template '{name}' not found on disk"
            )
        return self.templates.locate(
            image,
            template_path=path,
            threshold=threshold if threshold is not None else self.config.match_threshold,
        )

    # -- VLM -----------------------------------------------------------------------
    def vlm_available(self) -> bool:
        return self.vlm is not None and self.vlm.is_available()

    async def locate_with_vlm(self, screenshot: bytes | Image | Path, description: str) -> VisionResult:
        if not self.vlm_available():
            raise VisionUnavailableError(
                "no vision-language model configured; enable vision.vlm in config"
            )
        assert self.vlm is not None
        return await self.vlm.locate_element(screenshot, description)

    # -- status -----------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "ocr": {
                "provider": self.ocr.name if self.ocr else None,
                "available": self.ocr_available(),
            },
            "templates": {
                "provider": self.templates.name if self.templates else None,
                "available": self.templates_available(),
                "template_dir": self.config.template_dir,
            },
            "vlm": {
                "provider": self.vlm.name if self.vlm else None,
                "available": self.vlm_available(),
                "enabled": self.config.vlm.enabled,
            },
        }


# ---------------------------------------------------------------------------
# Provider factories (guarded; each returns None when unavailable)
# ---------------------------------------------------------------------------


def _build_ocr(vision_cfg: VisionConfig, ocr_cfg: OCRConfig) -> OCRProvider | None:
    if not vision_cfg.enabled or not ocr_cfg.enabled:
        return None
    provider = ocr_cfg.provider.strip().lower()
    try:
        if provider == "tesseract":
            from universal_computer.vision.ocr import TesseractOCRProvider  # noqa: PLC0415

            candidate = TesseractOCRProvider(ocr_cfg)
            return candidate if candidate.is_available() else None
        if provider == "easyocr":
            from universal_computer.vision.ocr import EasyOCRProvider  # noqa: PLC0415

            candidate = EasyOCRProvider(ocr_cfg)
            return candidate if candidate.is_available() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not initialize OCR provider '%s': %s", provider, exc)
    return None


def _build_templates(config: VisionConfig) -> VisionProvider | None:
    if not config.enabled:
        return None
    try:
        from universal_computer.vision.opencv import OpenCVTemplateMatcher  # noqa: PLC0415

        candidate = OpenCVTemplateMatcher()
        return candidate if candidate.is_available() else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("OpenCV template matcher unavailable: %s", exc)
        return None


def _build_vlm(config: VisionConfig) -> VisionLanguageModel | None:
    if not config.vlm.enabled or not config.enabled:
        return None
    try:
        from universal_computer.vision.vlm import OpenAICompatibleVLM  # noqa: PLC0415

        candidate = OpenAICompatibleVLM(config.vlm)
        return candidate if candidate.is_available() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not initialize VLM provider: %s", exc)
        return None

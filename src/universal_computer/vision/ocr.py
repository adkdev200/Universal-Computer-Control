"""OCR providers: Tesseract (primary) and EasyOCR (optional).

Results are line-grouped for better UI-text matching and include text,
bounding box (virtual-desktop pixels) and confidence in [0, 1].
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from PIL.Image import Image

from universal_computer.config import OCRConfig
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.models.observation import OCRResult
from universal_computer.vision.base import OCRProvider

logger = get_logger("vision.ocr")


class TesseractOCRProvider(OCRProvider):
    """Tesseract OCR via pytesseract (optional dependency group ``ocr``)."""

    name = "tesseract"

    def __init__(self, config: OCRConfig | None = None) -> None:
        self.config = config or OCRConfig()
        self._pytesseract: Any | None = None

    def is_available(self) -> bool:
        if self._pytesseract is not None:
            return True
        try:
            import pytesseract  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            if self.config.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd
            pytesseract.get_tesseract_version()
        except Exception as exc:  # noqa: BLE001
            self._probe_error = (
                f"tesseract binary unavailable ({type(exc).__name__}: {exc}); "
                "install the tesseract-ocr system package"
            )
            return False
        self._pytesseract = pytesseract
        return True

    def detect_text(self, image: Image, region: BoundingBox | None = None) -> list[OCRResult]:
        if not self.is_available():
            return []
        assert self._pytesseract is not None
        work = image
        offset_x, offset_y = 0, 0
        if region is not None:
            work = image.crop((region.left, region.top, region.right, region.bottom))
            offset_x, offset_y = region.left, region.top
        try:
            data = self._pytesseract.image_to_data(
                work, lang=self.config.language, output_type=self._pytesseract.Output.DICT
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tesseract failed: %s", exc)
            return []
        return self._group_lines(data, offset_x, offset_y)

    def _group_lines(self, data: dict[str, list], offset_x: int, offset_y: int) -> list[OCRResult]:
        """Merge word-level results into line-level OCR results."""
        lines: dict[tuple, list[dict]] = defaultdict(list)
        for index, raw_text in enumerate(data.get("text", [])):
            text = (raw_text or "").strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][index])
            except (KeyError, ValueError, TypeError):
                confidence = -1.0
            if confidence < 0:
                continue
            confidence /= 100.0
            if confidence < self.config.min_confidence:
                continue
            key = (
                data.get("page_num", [0])[index],
                data.get("block_num", [0])[index],
                data.get("par_num", [0])[index],
                data.get("line_num", [0])[index],
            )
            lines[key].append(
                {
                    "text": text,
                    "conf": confidence,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": int(data["width"][index]),
                    "height": int(data["height"][index]),
                }
            )
        results: list[OCRResult] = []
        for words in lines.values():
            words.sort(key=lambda w: (w["top"], w["left"]))
            text = " ".join(w["text"] for w in words)
            left = min(w["left"] for w in words) + offset_x
            top = min(w["top"] for w in words) + offset_y
            right = max(w["left"] + w["width"] for w in words) + offset_x
            bottom = max(w["top"] + w["height"] for w in words) + offset_y
            confidence = min(1.0, sum(w["conf"] for w in words) / len(words))
            results.append(
                OCRResult(
                    text=text,
                    bbox=BoundingBox.from_ltrb(left, top, right, bottom),
                    confidence=confidence,
                )
            )
        results.sort(key=lambda r: (r.bbox.top, r.bbox.left))
        return results


class EasyOCRProvider(OCRProvider):
    """EasyOCR provider (optional dependency group ``ocr-easy``; heavy import)."""

    name = "easyocr"

    def __init__(self, config: OCRConfig | None = None) -> None:
        self.config = config or OCRConfig()
        self._reader: Any | None = None

    def is_available(self) -> bool:
        if self._reader is not None:
            return True
        try:
            import easyocr  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            language = self.config.language.split("+")[0] or "en"
            self._reader = easyocr.Reader([language], verbose=False)
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"EasyOCR init failed: {exc}"
            return False
        return True

    def detect_text(self, image: Image, region: BoundingBox | None = None) -> list[OCRResult]:
        if not self.is_available():
            return []
        assert self._reader is not None
        work = image
        offset_x, offset_y = 0, 0
        if region is not None:
            work = image.crop((region.left, region.top, region.right, region.bottom))
            offset_x, offset_y = region.left, region.top
        try:
            # result rows: [box_points, text, confidence]
            raw = self._reader.readtext(work.convert("RGB"), detail=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("easyocr failed: %s", exc)
            return []
        results: list[OCRResult] = []
        for row in raw:
            try:
                points, text, confidence = row[0], str(row[1]).strip(), float(row[2])
            except (IndexError, TypeError, ValueError):
                continue
            if not text or confidence < self.config.min_confidence:
                continue
            xs = [int(p[0]) for p in points]
            ys = [int(p[1]) for p in points]
            results.append(
                OCRResult(
                    text=text,
                    bbox=BoundingBox.from_ltrb(
                        min(xs) + offset_x, min(ys) + offset_y, max(xs) + offset_x, max(ys) + offset_y
                    ),
                    confidence=confidence,
                )
            )
        return results

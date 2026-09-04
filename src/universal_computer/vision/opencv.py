"""OpenCV template matching provider (optional dependency group ``vision``).

Locates reference images (templates) inside a screenshot using normalized
cross-correlation with a small multi-scale sweep for resilience against minor
rendering differences.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL.Image import Image

from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.vision.base import VisionResult

logger = get_logger("vision.opencv")

_SCALES = (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15)


class OpenCVTemplateMatcher:
    """Grayscale normalized template matching (TM_CCOEFF_NORMED)."""

    name = "opencv"

    def __init__(self) -> None:
        self._cv2: Any | None = None
        self._np: Any | None = None

    def is_available(self) -> bool:
        if self._cv2 is not None:
            return True
        try:
            import cv2  # noqa: PLC0415
            import numpy  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._probe_error = (
                f"{type(exc).__name__}: {exc}; install opencv-python "
                "(pip install 'universal-computer-control[vision]')"
            )
            return False
        self._cv2 = cv2
        self._np = numpy
        return True

    def locate(
        self,
        image: Image,
        description: str | None = None,
        template_path: Path | None = None,
        threshold: float = 0.8,
    ) -> VisionResult:
        if not self.is_available():
            return VisionResult(found=False, method="opencv-template", details="OpenCV unavailable")
        if template_path is None:
            return VisionResult(
                found=False, method="opencv-template", details="no template path given"
            )
        cv2, np = self._cv2, self._np
        try:
            template_bytes = Path(template_path).read_bytes()
        except OSError as exc:
            return VisionResult(found=False, method="opencv-template", details=str(exc))
        template = cv2.imdecode(np.frombuffer(template_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if template is None:
            return VisionResult(
                found=False, method="opencv-template", details=f"unreadable template {template_path}"
            )
        screen = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        best_score, best_bbox = 0.0, None
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        for scale in _SCALES:
            scaled = self._scale_template(template_gray, scale)
            if scaled is None or scaled.shape[0] >= screen.shape[0] or scaled.shape[1] >= screen.shape[1]:
                continue
            try:
                result = cv2.matchTemplate(screen, scaled, cv2.TM_CCOEFF_NORMED)
            except cv2.error as exc:  # type: ignore[attr-defined]
                logger.debug("matchTemplate error: %s", exc)
                continue
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
            if max_val > best_score:
                best_score = float(max_val)
                h, w = scaled.shape[:2]
                best_bbox = BoundingBox.from_ltrb(int(max_loc[0]), int(max_loc[1]), int(max_loc[0]) + w, int(max_loc[1]) + h)
        if best_bbox is not None and best_score >= threshold:
            return VisionResult(
                found=True,
                bbox=best_bbox,
                confidence=round(best_score, 4),
                method="opencv-template",
                details=f"template={Path(template_path).name} scale-matched",
            )
        return VisionResult(
            found=False,
            bbox=None,
            confidence=round(best_score, 4),
            method="opencv-template",
            details=f"best score {best_score:.3f} below threshold {threshold:.2f}",
        )

    def _scale_template(self, gray: Any, scale: float) -> Any | None:
        if abs(scale - 1.0) < 1e-3:
            return gray
        try:
            h, w = gray.shape[:2]
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            return self._cv2.resize(gray, (new_w, new_h), interpolation=self._cv2.INTER_AREA)
        except Exception:  # noqa: BLE001
            return None

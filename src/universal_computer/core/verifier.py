"""Action verification: compare state snapshots before/after an action.

The signature deliberately excludes the mouse cursor (it trivially moves on
every click) and includes: active window, focused element, OCR text set and a
perceptual hash (dhash) of the screenshot when available.
"""

from __future__ import annotations

from universal_computer.config import EngineConfig
from universal_computer.logging import get_logger
from universal_computer.models.action import VerificationResult
from universal_computer.models.observation import Observation

logger = get_logger("core.verifier")

_HASH_SIZE = 8


def dhash(image_path: str) -> str | None:
    """64-bit difference hash of an image, as 16 hex chars; None on failure."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:  # pragma: no cover - Pillow is a core dependency
        return None
    try:
        image = Image.open(image_path).convert("L").resize((9, 8))
        pixels = list(image.getdata())
        bits = 0
        for row in range(_HASH_SIZE):
            for col in range(_HASH_SIZE):
                left = pixels[row * 9 + col]
                right = pixels[row * 9 + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return f"{bits:016x}"
    except Exception:  # noqa: BLE001 - verification must never crash actions
        return None


class Verifier:
    """Builds state signatures and compares them."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    def signature(self, observation: Observation | None) -> dict | None:
        if observation is None:
            return None
        from universal_computer.core.matching import normalize_text  # noqa: PLC0415

        ocr_texts = sorted({normalize_text(r.text) for r in observation.ocr if r.text})[:40]
        screenshot_hash = dhash(observation.screenshot_path) if observation.screenshot_path else None
        return {
            "window": (
                f"{observation.active_window.title}|{observation.active_window.application or ''}"
                if observation.active_window
                else None
            ),
            "focused": (
                observation.focused_element.searchable_text()
                if observation.focused_element
                else None
            ),
            "ocr": ocr_texts,
            "hash": screenshot_hash,
        }

    def compare(
        self, before: dict | None, after: dict | None
    ) -> VerificationResult:
        if before is None or after is None:
            return VerificationResult(performed=False, details="missing before/after state")
        components: list[tuple[float, float]] = []  # (weight, similarity 0..1)
        details: list[str] = []

        if before["hash"] and after["hash"]:
            components.append((0.4, 1.0 if before["hash"] == after["hash"] else 0.0))
            if before["hash"] != after["hash"]:
                details.append("screen changed")
        if before["window"] is not None or after["window"] is not None:
            equal = (
                before["window"] is not None
                and after["window"] is not None
                and before["window"] == after["window"]
            )
            components.append((0.3, 1.0 if equal else 0.0))
            if not equal:
                details.append("active window changed")
        if before["focused"] is not None or after["focused"] is not None:
            equal = (
                before["focused"] is not None
                and after["focused"] is not None
                and before["focused"] == after["focused"]
            )
            components.append((0.2, 1.0 if equal else 0.0))
            if not equal:
                details.append("focused element changed")

        before_set = set(before["ocr"])
        after_set = set(after["ocr"])
        if before_set or after_set:
            union = before_set | after_set
            jaccard = len(before_set & after_set) / len(union) if union else 1.0
            components.append((0.3, jaccard))
            if jaccard < 0.98:
                details.append("OCR text changed")

        if not components:
            return VerificationResult(
                performed=False,
                details="no comparable state (no screenshot, window or OCR signals)",
            )
        total_weight = sum(w for w, _ in components)
        similarity = sum(w * s for w, s in components) / total_weight if total_weight else 0.0
        changed = similarity < 0.985
        if not details:
            details.append("no visible change")
        return VerificationResult(
            performed=True,
            changed=changed,
            similarity=round(similarity, 4),
            details="; ".join(details),
            before=self._short(before),
            after=self._short(after),
        )

    @staticmethod
    def _short(signature: dict) -> str:
        parts = []
        if signature.get("window"):
            parts.append(f"window={signature['window']}")
        if signature.get("hash"):
            parts.append(f"hash={signature['hash']}")
        ocr = signature.get("ocr") or []
        parts.append(f"ocr={len(ocr)} items")
        return " ".join(parts)

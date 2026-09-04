"""Target resolver: turns an agent-facing target into actionable coordinates.

Accepts:
- ``{"x": 500, "y": 300}`` / ``[500, 300]`` / ``(500, 300)``  -> explicit coordinates
- ``"element_17"``  -> element from the recent-observation registry
- ``"Continue"``    -> fuzzy match against accessibility elements, then OCR
  text, then image templates, then an optional vision-language model

Resolution follows the interaction hierarchy (§5): accessibility first, then
OCR, then templates, then VLM; each hit carries alternatives so the recovery
engine can fall back when an attempt misses.
"""

from __future__ import annotations

from universal_computer.config import EngineConfig
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.errors import TargetResolutionError
from universal_computer.core.matching import fuzzy_score
from universal_computer.core.observer import ElementRegistry, Observer
from universal_computer.logging import get_logger
from universal_computer.models.action import ResolvedTarget
from universal_computer.models.element import UIElement
from universal_computer.vision.base import VisionServices

logger = get_logger("core.resolver")

_ELEMENT_ID_PREFIX = "element_"


def is_element_id(target: str) -> bool:
    return isinstance(target, str) and target.startswith(_ELEMENT_ID_PREFIX)


def parse_coordinates(target: object) -> tuple[int, int] | None:
    """Extract (x, y) from dict/list/tuple forms; None otherwise."""
    if isinstance(target, dict):
        try:
            return int(target["x"]), int(target["y"])
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(target, (list, tuple)) and len(target) == 2:
        try:
            return int(target[0]), int(target[1])
        except (TypeError, ValueError):
            return None
    return None


class TargetResolver:
    """Resolves targets following the interaction hierarchy."""

    def __init__(
        self,
        backend_manager: BackendManager,
        observer: Observer,
        registry: ElementRegistry,
        vision: VisionServices,
        config: EngineConfig,
    ) -> None:
        self.backends = backend_manager
        self.observer = observer
        self.registry = registry
        self.vision = vision
        self.config = config

    async def resolve(self, target: object) -> ResolvedTarget:
        coordinates = parse_coordinates(target)
        if coordinates is not None:
            return ResolvedTarget(
                kind="coordinates",
                coordinates=coordinates,
                method="explicit-coordinates",
                confidence=1.0,
                description=f"explicit coordinates {coordinates}",
            )

        if not isinstance(target, str) or not target.strip():
            raise TargetResolutionError(target, "target must be text or coordinates")
        query = target.strip()

        if is_element_id(query):
            return self._resolve_element_id(query)

        attempts: list[str] = []
        observation = await self.observer.observe("normal")

        # 1. Accessibility elements from the current observation.
        element, score = self._match_elements(observation.elements, query)
        if element is not None:
            return self._target_from_element(element, score, "accessibility-observation")

        # 2. Fresh accessibility search (tree may be bigger than the snapshot).
        element, score, backend_name = await self._search_accessibility(query)
        if element is not None:
            resolved = self._target_from_element(element, score, f"accessibility:{backend_name}")
            return resolved

        # 3. OCR text on the current screen.
        match = self._match_ocr(observation, query)
        if match is not None:
            ocr_result, score = match
            alternatives = [
                (r.bbox.center(), "ocr", r.confidence)
                for text, r in [
                    (other.text, other)
                    for other in observation.ocr
                    if other.bbox.center() != ocr_result.bbox.center()
                ][:3]
            ]
            return ResolvedTarget(
                kind="text",
                coordinates=ocr_result.bbox.center(),
                method="ocr",
                confidence=max(0.5, min(score, ocr_result.confidence + 0.3)),
                description=f"OCR text {ocr_result.text!r}",
                alternatives=alternatives,
            )

        # 4. Image templates (vision.template_dir).
        if self.vision.templates_available():
            attempts.append("template-miss")
            image = self._load_screenshot_image(observation)
            if image is not None:
                from asyncio import to_thread  # noqa: PLC0415

                result = await to_thread(
                    self.vision.find_template, image, query, None
                )
                if result.found and result.bbox is not None:
                    return ResolvedTarget(
                        kind="template",
                        coordinates=result.bbox.center(),
                        method="template",
                        confidence=result.confidence,
                        description=f"template {query!r} ({result.details})",
                    )

        # 5. Vision-language model (optional).
        if self.vision.vlm_available():
            attempts.append("vlm-miss")
            image = self._load_screenshot_image(observation)
            if image is not None:
                result = await self.vision.locate_with_vlm(image, query)
                if result.found and result.bbox is not None:
                    return ResolvedTarget(
                        kind="vision",
                        coordinates=result.bbox.center(),
                        method="vision",
                        confidence=result.confidence,
                        description=f"VLM located {query!r} ({result.details})",
                    )

        raise TargetResolutionError(
            query,
            "not found via accessibility, OCR, templates or vision "
            f"(attempts: {attempts or ['all']})",
        )

    # ------------------------------------------------------------------
    def _resolve_element_id(self, element_id: str) -> ResolvedTarget:
        found = self.registry.resolve_element(element_id)
        if found is None:
            raise TargetResolutionError(element_id, "unknown element id; run computer.observe first")
        element, obs_id = found
        age_ms = self._observation_age_ms(obs_id)
        stale = age_ms > self.config.observation_cache_ms * 4
        resolved = self._target_from_element(
            element, element.confidence, f"element-id:{obs_id}"
        )
        if stale:
            resolved.description += f" (stale reference, age={int(age_ms)}ms)"
            element.metadata["stale"] = True
            resolved.confidence = min(resolved.confidence, 0.6)
        return resolved

    def _observation_age_ms(self, obs_id: str) -> float:
        observation = self.registry.get_observation(obs_id)
        if observation is None:
            return float("inf")
        # Observation timestamps are UTC; monotonic clock is safer for ages.
        found = self.registry.last_observation()
        if found is not None and found.id == obs_id:
            return 0.0
        return self.config.observation_cache_ms * 10  # conservative default

    def _match_elements(
        self, elements: list[UIElement], query: str
    ) -> tuple[UIElement | None, float]:
        threshold = max(0.7, self.config.fuzzy_threshold)
        best: tuple[UIElement | None, float] = (None, 0.0)
        for element in elements:
            text = element.searchable_text()
            if not text:
                continue
            score = fuzzy_score(query, text)
            if element.is_clickable():
                score = min(1.0, score + 0.05)
            if score > best[1]:
                best = (element, score)
        if best[0] is not None and best[1] >= threshold:
            return best
        return None, 0.0

    async def _search_accessibility(self, query: str) -> tuple[UIElement | None, float, str]:
        from asyncio import to_thread  # noqa: PLC0415

        for backend in self.backends.select_all("accessibility"):
            try:
                elements = await to_thread(backend.get_window_elements, None, 8)
            except Exception:  # noqa: BLE001
                continue
            element, score = self._match_elements(elements, query)
            if element is not None:
                return element, score, backend.name
        return None, 0.0, ""

    def _match_ocr(self, observation, query: str) -> tuple[object, float] | None:
        threshold = self.config.fuzzy_threshold
        best: tuple[object, float] | None = None
        for result in observation.ocr:
            score = fuzzy_score(query, result.text)
            if best is None or score > best[1]:
                best = (result, score)
        if best is not None and best[1] >= threshold:
            return best
        return None

    def _target_from_element(self, element: UIElement, score: float, method: str) -> ResolvedTarget:
        if element.bbox is None:
            raise TargetResolutionError(
                element.searchable_text() or element.id,
                f"element matched ({method}) but has no bounding box",
            )
        semantic = element.can_invoke_semantically()
        return ResolvedTarget(
            kind="element",
            element=element,
            coordinates=element.bbox.center(),
            method=method,
            confidence=max(0.5, min(1.0, score if score else element.confidence)),
            description=(
                f"{element.role} {element.name!r} from {method} "
                f"[{element.bbox.left},{element.bbox.top}]"
            ),
            semantic_available=semantic,
        )

    def _load_screenshot_image(self, observation):
        path = observation.screenshot_path
        if not path:
            return None
        from PIL import Image  # noqa: PLC0415

        try:
            return Image.open(path)
        except OSError:
            return None


def target_repr(target: object) -> str:
    """Stable, log-safe string for an action target."""
    if isinstance(target, dict):
        return f"coords({target.get('x')},{target.get('y')})"
    if isinstance(target, (list, tuple)):
        return f"coords({target[0]},{target[1]})"
    return str(target)

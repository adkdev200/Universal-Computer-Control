"""Observer: builds structured :class:`Observation` snapshots and keeps an
element registry so element IDs stay stable within (and across, briefly)
observation cycles.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from universal_computer.backends.base import AccessibilityBackend, Cap, ScreenshotBackend
from universal_computer.config import EngineConfig
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.errors import BackendUnavailableError
from universal_computer.logging import get_logger
from universal_computer.models.element import ElementSource, UIElement
from universal_computer.models.observation import (
    ActiveWindowInfo,
    CursorInfo,
    Observation,
    ObservationLevel,
    OCRResult,
    ScreenInfo,
    parse_level,
)
from universal_computer.persistence.state import PersistenceManager
from universal_computer.vision.base import VisionServices

logger = get_logger("core.observer")


class ElementRegistry:
    """Remembers the last N observations so ``element_17`` can be resolved."""

    def __init__(self, max_observations: int = 10) -> None:
        self._observations: OrderedDict[str, Observation] = OrderedDict()
        self.max_observations = max_observations
        self._counter = 0

    def next_observation_id(self) -> str:
        self._counter += 1
        return f"obs_{self._counter}"

    def register(self, observation: Observation) -> None:
        self._observations[observation.id] = observation
        while len(self._observations) > self.max_observations:
            self._observations.popitem(last=False)

    def get_observation(self, obs_id: str) -> Observation | None:
        return self._observations.get(obs_id)

    def last_observation(self) -> Observation | None:
        if not self._observations:
            return None
        last_id = next(reversed(self._observations))
        return self._observations[last_id]

    def resolve_element(self, element_id: str) -> tuple[UIElement, str] | None:
        """Search newest -> oldest for ``element_id``; returns (element, obs_id)."""
        for obs_id in reversed(self._observations.keys()):
            observation = self._observations[obs_id]
            element = observation.element_by_id(element_id)
            if element is not None:
                return element, obs_id
        return None


class Observer:
    """Produces observations by combining the best available backends."""

    def __init__(
        self,
        backend_manager: BackendManager,
        engine_config: EngineConfig,
        persistence: PersistenceManager,
        vision: VisionServices,
        registry: ElementRegistry | None = None,
    ) -> None:
        self.backends = backend_manager
        self.config = engine_config
        self.persistence = persistence
        self.vision = vision
        self.registry = registry or ElementRegistry(engine_config.max_observations_cached)
        self._cache: Observation | None = None
        self._cache_time = 0.0

    # ------------------------------------------------------------------
    async def observe(
        self,
        level: str | ObservationLevel = "normal",
        force: bool = False,
        max_age_ms: int | None = None,
    ) -> Observation:
        level = parse_level(level)
        cache_ms = self.config.observation_cache_ms if max_age_ms is None else max_age_ms
        if (
            not force
            and self._cache is not None
            and (time.monotonic() - self._cache_time) * 1000.0 <= cache_ms
            and self._cache.level.rank >= level.rank
        ):
            return self._cache

        notes: list[str] = []
        screen, screenshot = await self._gather_screen(level, notes)
        active_window = await self._gather_active_window(notes)
        cursor = await self._gather_cursor(notes)

        observation = Observation(
            id=self.registry.next_observation_id(),
            level=level,
            screen=screen,
            active_window=active_window,
            cursor=cursor,
            notes=notes,
        )

        if level.rank >= ObservationLevel.NORMAL.rank:
            await self._gather_ocr(observation, screenshot, notes)
            await self._gather_elements(observation, active_window, notes)
            if level == ObservationLevel.FULL:
                await self._gather_focused(observation, notes)

        screenshot_path = (
            self.persistence.save_screenshot(screenshot, observation.id)
            if screenshot is not None
            else None
        )
        observation.screenshot_path = str(screenshot_path) if screenshot_path else None
        if screenshot is None and level.rank >= ObservationLevel.NORMAL.rank:
            notes.append("screenshot unavailable; OCR and visual matching disabled")

        self.registry.register(observation)
        self.persistence.save_observation(observation)
        self._cache = observation
        self._cache_time = time.monotonic()
        return observation

    # ------------------------------------------------------------------
    async def _gather_screen(
        self, level: ObservationLevel, notes: list[str]
    ) -> tuple[ScreenInfo | None, object | None]:
        try:
            backend = self.backends.select(Cap.SCREENSHOT)
        except BackendUnavailableError:
            notes.append("no screenshot backend available")
            return None, None
        assert isinstance(backend, ScreenshotBackend)
        from asyncio import to_thread  # noqa: PLC0415

        try:
            screen_info = await to_thread(backend.get_screen_info)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"screen geometry failed: {type(exc).__name__}")
            screen_info = None
        screenshot = None
        if level.rank >= ObservationLevel.NORMAL.rank:
            try:
                screenshot = await to_thread(backend.take_screenshot)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"screenshot failed: {type(exc).__name__}: {exc}")
        return screen_info, screenshot

    async def _gather_active_window(self, notes: list[str]) -> ActiveWindowInfo | None:
        for backend in self.backends.select_all(Cap.WINDOWS):
            from asyncio import to_thread  # noqa: PLC0415

            try:
                window = await to_thread(backend.get_active_window)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"window backend '{backend.name}' failed: {type(exc).__name__}")
                continue
            if window is not None:
                return ActiveWindowInfo(
                    title=window.title,
                    application=window.application,
                    process_id=window.process_id,
                    window_handle=window.handle,
                    bbox=window.bbox,
                )
        return None

    async def _gather_cursor(self, notes: list[str]) -> CursorInfo | None:
        from asyncio import to_thread  # noqa: PLC0415

        for capability in (Cap.CURSOR, Cap.INPUT):
            for backend in self.backends.select_all(capability):
                getter = getattr(backend, "get_cursor_position", None)
                if getter is None:
                    continue
                try:
                    position = await to_thread(getter)
                except Exception:  # noqa: BLE001
                    continue
                if position is not None:
                    return CursorInfo(x=int(position[0]), y=int(position[1]))
        return None

    async def _gather_ocr(
        self, observation: Observation, screenshot: object | None, notes: list[str]
    ) -> None:
        if screenshot is None:
            return
        from asyncio import to_thread  # noqa: PLC0415

        if not self.vision.ocr_available():
            if self.vision.ocr is None:
                notes.append("OCR provider not installed (optional dependency)")
            else:
                notes.append("OCR provider unavailable (engine/binary missing)")
            return
        try:
            results: list[OCRResult] = await to_thread(self.vision.run_ocr, screenshot)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"OCR failed: {type(exc).__name__}")
            return
        observation.ocr = results

    async def _gather_elements(
        self,
        observation: Observation,
        active_window: ActiveWindowInfo | None,
        notes: list[str],
    ) -> None:
        from asyncio import to_thread  # noqa: PLC0415

        window_model = None
        if active_window is not None:
            from universal_computer.models.window import WindowInfo, WindowState  # noqa: PLC0415

            window_model = WindowInfo(
                title=active_window.title,
                application=active_window.application,
                process_id=active_window.process_id,
                handle=active_window.window_handle,
                state=WindowState.NORMAL,
            )
        accessibility: list[AccessibilityBackend] = self.backends.select_all(Cap.ACCESSIBILITY)
        if not accessibility:
            notes.append("no accessibility backend (OCR/vision still work)")
            return
        merged: list[UIElement] = []
        seen_names = 0
        for backend in accessibility:
            try:
                elements = await to_thread(
                    backend.get_window_elements, window_model, 8
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"accessibility backend '{backend.name}' failed: {type(exc).__name__}")
                continue
            if elements:
                merged.extend(elements)
                seen_names += len(elements)
                if seen_names > 400:
                    break
        observation.elements = merged

    async def _gather_focused(self, observation: Observation, notes: list[str]) -> None:
        from asyncio import to_thread  # noqa: PLC0415

        for backend in self.backends.select_all(Cap.ACCESSIBILITY):
            try:
                focused = await to_thread(backend.get_focused_element)
            except Exception:  # noqa: BLE001
                continue
            if focused is not None:
                observation.focused_element = focused
                return
        notes.append("focused element unavailable")

    # ------------------------------------------------------------------
    def reassign_ids(self, observation: Observation) -> None:
        """Re-key element ids so they are stable within this observation."""
        for index, element in enumerate(observation.elements, start=1):
            element.id = f"element_{index}"
            if element.source == ElementSource.UIA or element.source == ElementSource.ATSPI:
                element.metadata.setdefault("backend", "accessibility")

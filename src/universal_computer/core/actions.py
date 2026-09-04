"""Mouse and keyboard actions mixin for :class:`ComputerControlEngine`.

Implements smart click/type with attempt planning (semantic invoke first,
then coordinates with alternatives), plus move/drag/scroll/press/hotkey and
smart typing (keyboard first, clipboard fallback for Unicode).
"""

from __future__ import annotations

import time
from typing import Any

from universal_computer.backends.base import Cap
from universal_computer.core.errors import ActionFailedError
from universal_computer.core.recovery import Attempt, input_backend
from universal_computer.core.resolver import target_repr
from universal_computer.models.action import ActionType, ResolvedTarget

ClickableTarget = str | int | float | list | tuple | dict[str, Any]


def _dedupe_coords(
    sources: list[tuple[tuple[int, int], str, float]],
    tolerance_px: int = 4,
) -> list[tuple[tuple[int, int], str, float]]:
    """Keep higher-confidence coordinate sources; drop near-duplicates."""
    kept: list[tuple[tuple[int, int], str, float]] = []
    for coords, method, confidence in sorted(sources, key=lambda s: -s[2]):
        if any(
            abs(coords[0] - existing[0]) <= tolerance_px
            and abs(coords[1] - existing[1]) <= tolerance_px
            for existing, _m, _c in kept
        ):
            continue
        kept.append((coords, method, confidence))
    return kept


def _simple_result(success: bool, action: str, error: str | None, started: float, **extra) -> dict:
    payload = {
        "success": success,
        "action": action,
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000.0, 1),
    }
    payload.update(extra)
    return payload


class MouseKeyboardActions:
    """Mixin: assumes the surrounding class is ComputerControlEngine."""

    async def click(
        self,
        target: ClickableTarget,
        button: str = "left",
        verify: bool | None = None,
    ) -> Any:
        return await self._click_like(ActionType.CLICK, target, button=button, verify=verify)

    async def double_click(self, target: ClickableTarget, verify: bool | None = None) -> Any:
        return await self._click_like(ActionType.DOUBLE_CLICK, target, verify=verify)

    async def right_click(self, target: ClickableTarget, verify: bool | None = None) -> Any:
        return await self._click_like(ActionType.RIGHT_CLICK, target, verify=verify)

    async def move_mouse(self, target: ClickableTarget) -> dict:
        resolved = await self.resolver.resolve(target)
        tr = target_repr(target)
        ib = input_backend(self.backend_manager)
        coords = resolved.coordinates
        if coords is None:
            raise ActionFailedError("move_mouse target has no coordinates")
        started = time.monotonic()
        from asyncio import to_thread  # noqa: PLC0415

        error = None
        try:
            await to_thread(ib.move, coords[0], coords[1])
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        result = _simple_result(
            error is None,
            ActionType.MOVE.value,
            error,
            started,
            target=tr,
            method="coordinates",
            coordinates=list(coords),
        )
        self.history.record_custom(result)
        return result

    async def drag(
        self,
        start: ClickableTarget,
        end: ClickableTarget,
        duration_s: float | None = None,
    ) -> dict:
        start_resolved = await self.resolver.resolve(start)
        end_resolved = await self.resolver.resolve(end)
        if start_resolved.coordinates is None or end_resolved.coordinates is None:
            raise ActionFailedError("drag requires resolvable start and end targets")
        ib = input_backend(self.backend_manager)
        started = time.monotonic()
        from asyncio import to_thread  # noqa: PLC0415

        error = None
        try:
            await to_thread(ib.drag, start_resolved.coordinates, end_resolved.coordinates, duration_s)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        result = _simple_result(
            error is None,
            ActionType.DRAG.value,
            error,
            started,
            target=f"{start_resolved.description} -> {end_resolved.description}",
            method="coordinates",
            coordinates=list(end_resolved.coordinates),
        )
        self.history.record_custom(result)
        return result

    async def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> dict:
        ib = input_backend(self.backend_manager)
        started = time.monotonic()
        from asyncio import to_thread  # noqa: PLC0415

        error = None
        try:
            await to_thread(ib.scroll, int(amount), x, y)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        coords = [x, y] if x is not None and y is not None else None
        result = _simple_result(
            error is None,
            ActionType.SCROLL.value,
            error,
            started,
            method="coordinates",
            coordinates=coords,
            amount=int(amount),
        )
        self.history.record_custom(result)
        return result

    # ------------------------------------------------------------------
    async def type(self, text: str, verify: bool | None = None) -> Any:
        """Type text at the current focus; Unicode-safe via clipboard fallback."""
        if not isinstance(text, str):
            raise ActionFailedError("text must be a string")
        tr = f"<text:{len(text)} chars>"  # never log the actual content
        ascii_safe = all(ord(ch) <= 126 for ch in text)
        attempts: list[Attempt] = []
        ib = input_backend(self.backend_manager)
        clipboards = self.backend_manager.select_all(Cap.CLIPBOARD)
        engine_cfg = self.config.engine

        if ascii_safe:
            interval = engine_cfg.type_interval_s

            def run_keyboard() -> None:
                ib.type_text(text, interval_s=interval)

            attempts.append(
                Attempt(
                    method="keyboard",
                    run=run_keyboard,
                    backend=ib.name,
                    confidence=0.9,
                    description=f"type {len(text)} characters via keyboard",
                )
            )
        if clipboards and self.backend_manager.has(Cap.INPUT):
            clipboard = clipboards[0]
            restore = engine_cfg.clipboard_restore

            def run_clipboard() -> None:
                previous = clipboard.get_text()
                if not clipboard.set_text(text):
                    raise ActionFailedError("clipboard set_text failed")
                time.sleep(0.08)
                ib.hotkey("ctrl", "v")
                if restore and previous is not None:
                    time.sleep(0.35)
                    clipboard.set_text(previous)

            attempts.append(
                Attempt(
                    method="clipboard",
                    run=run_clipboard,
                    backend=clipboard.name,
                    confidence=0.85 if not ascii_safe else 0.6,
                    description=f"paste {len(text)} characters via clipboard",
                )
            )
        if not attempts:

            def run_direct() -> None:
                ib.type_text(text)

            attempts.append(
                Attempt(
                    method="keyboard-direct",
                    run=run_direct,
                    backend=ib.name,
                    confidence=0.4,
                    description="direct typing attempt (may drop Unicode)",
                )
            )
        resolved = ResolvedTarget(kind="none", method="keyboard-focus")
        return await self.executor.execute(
            ActionType.TYPE, tr, resolved, attempts, verify, expect_change=False
        )

    async def press(self, key: str) -> Any:
        return await self._key_action(ActionType.PRESS, key, single=True)

    async def hotkey(self, keys: str | list[str]) -> Any:
        if isinstance(keys, str):
            keys = [part for part in keys.split("+") if part]
        return await self._key_action(ActionType.HOTKEY, keys, single=False)

    async def _key_action(self, action: ActionType, keys, single: bool) -> Any:
        ib = input_backend(self.backend_manager)
        if single:
            key_name = str(keys)

            def run() -> None:
                ib.press(key_name)

            description = f"press {key_name}"
        else:
            key_list = [str(k) for k in keys]

            def run() -> None:
                ib.hotkey(*key_list)

            description = f"hotkey {'+'.join(key_list)}"
        attempt = Attempt(
            method="keyboard", run=run, backend=ib.name, confidence=0.95, description=description
        )
        resolved = ResolvedTarget(kind="none", method="keyboard")
        return await self.executor.execute(
            action, description, resolved, [attempt], verify=False, expect_change=False
        )

    # ------------------------------------------------------------------
    async def _click_like(
        self,
        action: ActionType,
        target: ClickableTarget,
        button: str = "left",
        verify: bool | None = None,
    ) -> Any:
        resolved = await self.resolver.resolve(target)
        tr = target_repr(target)
        attempts: list[Attempt] = []
        manager = self.backend_manager

        # 1. Semantic invoke via the accessibility backend that found the element.
        element = resolved.element
        if element is not None and resolved.semantic_available:
            source_backend = element.metadata.get("backend") or ""

            def run_invoke(element=element, source_backend=source_backend) -> None:
                for ab in manager.select_all("accessibility"):
                    if not source_backend or ab.name == source_backend:
                        if ab.invoke_element(element):
                            return
                raise ActionFailedError("semantic invoke failed on every accessibility backend")

            attempts.append(
                Attempt(
                    method="semantic-invoke",
                    run=run_invoke,
                    backend=source_backend or None,
                    confidence=0.97,
                    description=f"invoke {element.role} {element.name!r}",
                    semantic=True,
                )
            )

        # 2/3. Coordinate clicks: element bbox first, then resolver hit, then
        #      lower-confidence alternatives (extra OCR candidates etc.).
        sources: list[tuple[tuple[int, int], str, float]] = []
        if element is not None and element.bbox is not None:
            sources.append((element.bbox.center(), "element-bbox", 0.85))
        if resolved.coordinates is not None:
            confidence = 0.95 if resolved.kind == "coordinates" else 0.75
            sources.append((resolved.coordinates, resolved.method, confidence))
        for coords, method, confidence in resolved.alternatives:
            sources.append((coords, method, min(confidence, 0.6)))

        if not attempts and not sources:
            raise ActionFailedError(
                "resolved target has neither semantic action nor coordinates"
            )

        # Name the preferred input backend up front so attempt records and
        # failure tracking reference it even when execution re-selects fresh.
        try:
            preferred_input = manager.select(Cap.INPUT).name
        except Exception:  # noqa: BLE001
            preferred_input = None

        for coords, method, confidence in _dedupe_coords(sources):

            def run_click(coords=coords, button=button) -> None:
                ib = input_backend(manager)
                if action == ActionType.DOUBLE_CLICK:
                    ib.double_click(coords[0], coords[1])
                elif action == ActionType.RIGHT_CLICK:
                    ib.right_click(coords[0], coords[1])
                else:
                    ib.click(coords[0], coords[1], button=button)

            attempts.append(
                Attempt(
                    method="coordinates",
                    run=run_click,
                    backend=preferred_input,
                    confidence=confidence,
                    description=f"click at {coords} via {method}",
                )
            )
        return await self.executor.execute(action, tr, resolved, attempts, verify)

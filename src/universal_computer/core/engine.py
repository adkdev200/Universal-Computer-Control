"""ComputerControlEngine: the platform-independent computer-control facade.

This is the heart of the system (design §7, §32). The MCP server is only a
thin wrapper; the engine is fully usable without MCP::

    computer = ComputerControlEngine(load_config())
    observation = await computer.observe()
    await computer.click("Continue")
    await computer.type("hello")
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import time
from typing import Any

from universal_computer.backends.base import Cap, WindowManagementBackend
from universal_computer.config import AppConfig
from universal_computer.core.actions import MouseKeyboardActions
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.errors import (
    BackendUnavailableError,
    EmergencyStopError,  # noqa: F401 (re-export)
    SecurityViolationError,
    WindowNotFoundError,
)
from universal_computer.core.observer import ElementRegistry, Observer
from universal_computer.core.recovery import ActionExecutor
from universal_computer.core.resolver import TargetResolver
from universal_computer.core.verifier import Verifier
from universal_computer.logging import get_logger, redact_text
from universal_computer.models.observation import Observation
from universal_computer.models.window import WindowInfo
from universal_computer.persistence.history import ActionHistory
from universal_computer.persistence.state import PersistenceManager
from universal_computer.security.emergency_stop import EmergencyStop
from universal_computer.security.policy import SecurityPolicy
from universal_computer.vision.base import VisionServices

logger = get_logger("core.engine")


class ComputerControlEngine(MouseKeyboardActions):
    """Observe → understand → act → verify on any GUI application.

    All methods are async; blocking OS calls run in worker threads so the MCP
    event loop is never blocked (design §25).
    """

    def __init__(
        self,
        config: AppConfig,
        backend_manager: BackendManager | None = None,
        vision: VisionServices | None = None,
        persistence: PersistenceManager | None = None,
        history: ActionHistory | None = None,
        stop: EmergencyStop | None = None,
        policy: SecurityPolicy | None = None,
        observer: Observer | None = None,
        resolver: TargetResolver | None = None,
        verifier: Verifier | None = None,
        executor: ActionExecutor | None = None,
    ) -> None:
        self.config = config
        self.persistence = persistence or PersistenceManager(config.persistence)
        self.backend_manager = backend_manager or BackendManager(config.backends)
        self.vision = vision or VisionServices.from_config(config.vision, config.ocr)
        self.stop = stop or EmergencyStop(self.persistence.state_dir)
        self.policy = policy or SecurityPolicy(config.security)
        self.history = history or ActionHistory(self.persistence)
        self.observer = observer or Observer(
            self.backend_manager, config.engine, self.persistence, self.vision
        )
        self.registry: ElementRegistry = self.observer.registry
        self.resolver = resolver or TargetResolver(
            self.backend_manager,
            self.observer,
            self.registry,
            self.vision,
            config.engine,
        )
        self.verifier = verifier or Verifier(config.engine)
        self.executor = executor or ActionExecutor(
            config.engine,
            self.observer,
            self.verifier,
            self.backend_manager,
            self.history,
            self.stop,
            self.policy,
        )

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------
    async def observe(self, level: str = "normal", force: bool = False) -> Observation:
        self.stop.check()
        return await self.observer.observe(level, force=force)

    async def screenshot(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
        return_image: bool = False,
    ) -> dict:
        self.stop.check()
        backend = self.backend_manager.select(Cap.SCREENSHOT)
        from asyncio import to_thread  # noqa: PLC0415

        image = await to_thread(backend.take_screenshot, monitor, region)
        stem = f"shot_{int(time.time() * 1000)}"
        path = self.persistence.save_screenshot(image, stem)
        payload: dict[str, Any] = {
            "success": True,
            "size": list(image.size),
            "path": str(path) if path else None,
        }
        if return_image and path:
            payload["image_file"] = path
        return payload

    async def get_ui_tree(self, max_depth: int = 8, window: str | None = None) -> dict:
        self.stop.check()
        window_model = await self._resolve_window_query(window) if window else None
        from asyncio import to_thread  # noqa: PLC0415

        for backend in self.backend_manager.select_all(Cap.ACCESSIBILITY):
            try:
                tree = await to_thread(backend.get_ui_tree, window_model, max_depth)
            except Exception as exc:  # noqa: BLE001
                logger.debug("ui_tree from '%s' failed: %s", backend.name, exc)
                continue
            if tree:
                return {"success": True, "source": backend.name, "tree": tree}
        raise BackendUnavailableError(
            Cap.ACCESSIBILITY, "no accessibility backend produced a UI tree"
        )

    async def find_text(self, query: str, threshold: float | None = None, limit: int = 10) -> dict:
        self.stop.check()
        observation = await self.observer.observe("normal")
        threshold = threshold if threshold is not None else self.config.engine.fuzzy_threshold
        from universal_computer.core.matching import rank_matches  # noqa: PLC0415

        candidates = [(r.text, r) for r in observation.ocr]
        matches = rank_matches(query, candidates, threshold=threshold, limit=limit)
        results = [
            {
                "text": r.text,
                "bbox": r.bbox.model_dump(mode="json"),
                "score": round(score, 3),
                "confidence": r.confidence,
                "source": r.source.value,
            }
            for r, score in matches
        ]
        # Also surface matching accessibility elements (bonus matches).
        from universal_computer.core.matching import fuzzy_score  # noqa: PLC0415

        for element in observation.elements:
            text = element.searchable_text()
            if not text:
                continue
            score = fuzzy_score(query, text)
            if score >= max(threshold, 0.7) and len(results) < limit:
                results.append(
                    {
                        "text": text,
                        "bbox": element.bbox.model_dump(mode="json") if element.bbox else None,
                        "score": round(score, 3),
                        "confidence": element.confidence,
                        "source": element.source.value,
                        "element_id": element.id,
                    }
                )
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"success": True, "query": query, "matches": results, "count": len(results)}

    async def find_visual(
        self,
        description: str = "",
        template: str | None = None,
        threshold: float | None = None,
    ) -> dict:
        self.stop.check()
        observation = await self.observer.observe("normal")
        if not observation.screenshot_path:
            raise BackendUnavailableError(Cap.SCREENSHOT, "no screenshot for visual search")
        from PIL import Image  # noqa: PLC0415

        image = Image.open(observation.screenshot_path)
        template_name = template or description
        if not template_name:
            return {"success": False, "error": "provide a template name or description"}
        from asyncio import to_thread  # noqa: PLC0415

        result = await to_thread(
            self.vision.find_template, image, template_name, threshold
        )
        payload = result.model_dump(mode="json")
        if not result.found and self.vision.vlm_available() and description:
            vlm_result = await self.vision.locate_with_vlm(image, description)
            payload = vlm_result.model_dump(mode="json")
        return {"success": True, "result": payload}

    async def wait_for(
        self,
        expectation: str = "any_change",
        value: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        """Poll observations until the expectation holds or the timeout hits.

        Expectations: ``any_change`` (state signature differs from now),
        ``window`` (a window title containing ``value`` appears/focuses),
        ``text`` (OCR text containing ``value`` appears).
        """
        self.stop.check()
        engine_cfg = self.config.engine
        timeout = timeout_s if timeout_s is not None else engine_cfg.action_timeout_s
        deadline = time.monotonic() + max(0.5, timeout)
        baseline_signature = self.verifier.signature(await self.observer.observe("minimal", force=True))
        started = time.monotonic()
        while True:
            self.stop.check()
            observation = await self.observer.observe("normal")
            matched, why = self._check_expectation(observation, expectation, value, baseline_signature)
            if matched:
                return {
                    "success": True,
                    "expectation": expectation,
                    "detail": why,
                    "elapsed_s": round(time.monotonic() - started, 2),
                    "observation_id": observation.id,
                }
            if time.monotonic() >= deadline:
                return {
                    "success": False,
                    "expectation": expectation,
                    "detail": f"timeout after {timeout}s: {why}",
                    "elapsed_s": round(time.monotonic() - started, 2),
                    "observation_id": observation.id,
                }
            await asyncio.sleep(max(0.1, engine_cfg.wait_poll_interval_s))

    @staticmethod
    def _check_expectation(
        observation: Observation,
        expectation: str,
        value: str | None,
        baseline_signature: dict | None,
    ) -> tuple[bool, str]:
        expectation = expectation.strip().lower()
        if expectation == "any_change":
            engine_cfg_signature = observation  # keep signature computation local
            _ = engine_cfg_signature
            # Build a lightweight signature via Verifier through observation only.
            from universal_computer.core.verifier import dhash  # noqa: PLC0415

            changed_parts = []
            if baseline_signature is None:
                return True, "no baseline"
            if observation.active_window is not None and baseline_signature.get("window"):
                window_sig = (
                    f"{observation.active_window.title}|{observation.active_window.application or ''}"
                )
                if window_sig != baseline_signature["window"]:
                    changed_parts.append("active window")
            if observation.screenshot_path:
                digest = dhash(observation.screenshot_path)
                if digest and digest != baseline_signature.get("hash"):
                    changed_parts.append("screen content")
            if observation.ocr and baseline_signature.get("ocr") is not None:
                texts = {r.text for r in observation.ocr}
                if texts != set(baseline_signature.get("ocr") or []):
                    changed_parts.append("OCR text")
            return (bool(changed_parts), ", ".join(changed_parts) or "no change yet")
        if expectation == "window":
            if observation.active_window is not None and value:
                if value.casefold() in observation.active_window.title.casefold():
                    return True, f"window {observation.active_window.title!r} is active"
            return False, "target window not active yet"
        if expectation == "text":
            if value:
                for result in observation.ocr:
                    if value.casefold() in result.text.casefold():
                        return True, f"text {result.text!r} visible"
            return False, "text not visible yet"
        return False, f"unknown expectation {expectation!r}"

    # ------------------------------------------------------------------
    # Windows
    # ------------------------------------------------------------------
    async def _resolve_window_query(self, query: str) -> WindowInfo:
        backend = self.backend_manager.select(Cap.WINDOWS)
        from asyncio import to_thread  # noqa: PLC0415

        windows: list[WindowInfo] = await to_thread(backend.list_windows)
        for window in windows:
            if window.matches(query):
                return window
        raise WindowNotFoundError(
            f"no window matching {query!r} (open: {[w.title for w in windows][:10]})"
        )

    def _window_backend(self) -> WindowManagementBackend:
        backend = self.backend_manager.select(Cap.WINDOWS)
        assert isinstance(backend, WindowManagementBackend)
        return backend

    async def list_windows(self) -> dict:
        backend = self._window_backend()
        from asyncio import to_thread  # noqa: PLC0415

        windows = await to_thread(backend.list_windows)
        return {
            "success": True,
            "count": len(windows),
            "windows": [w.model_dump(mode="json") for w in windows],
        }

    async def get_active_window(self) -> dict:
        backend = self._window_backend()
        from asyncio import to_thread  # noqa: PLC0415

        window = await to_thread(backend.get_active_window)
        return {
            "success": True,
            "window": window.model_dump(mode="json") if window else None,
        }

    async def _window_op(self, op: str, query: str, confirm: bool = False) -> dict:
        self.stop.check()
        decision = self.policy.check_confirmation(op, confirm)
        if not decision.allowed:
            return {"success": False, "error": decision.reason}
        backend = self._window_backend()
        window = await self._resolve_window_query(query)
        from asyncio import to_thread  # noqa: PLC0415

        func = {
            "focus_window": backend.focus_window,
            "minimize_window": backend.minimize_window,
            "maximize_window": backend.maximize_window,
            "restore_window": backend.restore_window,
            "close_window": backend.close_window,
        }[op]
        started = time.monotonic()
        ok = await to_thread(func, window)
        self.history.record_custom(
            {
                "action": op,
                "target": window.title,
                "backend": backend.name,
                "success": bool(ok),
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
        )
        return {"success": bool(ok), "window": window.model_dump(mode="json"), "op": op}

    async def focus_window(self, window: str) -> dict:
        return await self._window_op("focus_window", window)

    async def minimize_window(self, window: str) -> dict:
        return await self._window_op("minimize_window", window)

    async def maximize_window(self, window: str) -> dict:
        return await self._window_op("maximize_window", window)

    async def restore_window(self, window: str) -> dict:
        return await self._window_op("restore_window", window)

    async def close_window(self, window: str, confirm: bool = False) -> dict:
        return await self._window_op("close_window", window, confirm=confirm)

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------
    async def get_clipboard(self) -> dict:
        backend = self.backend_manager.select(Cap.CLIPBOARD)
        from asyncio import to_thread  # noqa: PLC0415

        text = await to_thread(backend.get_text)
        self.history.record_custom(
            {"action": "get_clipboard", "backend": backend.name, "success": text is not None,
             "length": len(text or "")}
        )
        return {"success": text is not None, "text": text, "length": len(text or "")}

    async def set_clipboard(self, text: str) -> dict:
        backend = self.backend_manager.select(Cap.CLIPBOARD)
        from asyncio import to_thread  # noqa: PLC0415

        ok = await to_thread(backend.set_text, text)
        self.history.record_custom(
            {"action": "set_clipboard", "backend": backend.name, "success": bool(ok),
             "length": len(text)}  # content intentionally never logged
        )
        return {"success": bool(ok), "length": len(text)}

    # ------------------------------------------------------------------
    # Applications / commands
    # ------------------------------------------------------------------
    async def launch_application(self, command: str) -> dict:
        self.stop.check()
        decision = self.policy.check_application(command)
        if not decision.allowed:
            raise SecurityViolationError(decision.reason or "application denied")
        backend = self.backend_manager.select(Cap.LAUNCH)
        from asyncio import to_thread  # noqa: PLC0415

        started = time.monotonic()
        pid = await to_thread(backend.launch, command)
        self.history.record_custom(
            {
                "action": "launch",
                "target": command,
                "backend": backend.name,
                "success": True,
                "pid": pid,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
        )
        return {"success": True, "command": command, "pid": pid}

    async def run_command(self, command: str, timeout_s: float | None = None, confirm: bool = False) -> dict:
        """Execute an allowlisted command (shell string only when enabled)."""
        self.stop.check()
        decision = self.policy.check_command(command)
        if not decision.allowed:
            raise SecurityViolationError(decision.reason or "command denied")
        confirm_decision = self.policy.check_confirmation("run_command", confirm)
        if not confirm_decision.allowed:
            return {"success": False, "error": confirm_decision.reason}
        timeout = timeout_s if timeout_s is not None else self.config.security.command_timeout_s
        from asyncio import to_thread  # noqa: PLC0415

        started = time.monotonic()
        try:
            result = await to_thread(self._run_command_sync, command, timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            self.history.record_custom(
                {"action": "run_command", "target": command, "success": False, "error": str(exc)}
            )
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        self.history.record_custom(
            {
                "action": "run_command",
                "target": command,
                "success": result.get("returncode") == 0,
                "returncode": result.get("returncode"),
                "duration_ms": result["duration_ms"],
            }
        )
        max_chars = self.config.security.max_command_output_chars
        return {
            "success": result.get("returncode") == 0,
            "returncode": result.get("returncode"),
            "stdout": redact_text(result.get("stdout", ""))[:max_chars],
            "stderr": redact_text(result.get("stderr", ""))[:max_chars],
            "timed_out": result.get("timed_out", False),
        }

    def _run_command_sync(self, command: str, timeout: float) -> dict:
        use_shell = self.config.security.allow_shell and self.config.security.mode == "permissive"
        try:
            if use_shell:
                completed = subprocess.run(  # noqa: S602 - explicitly configured
                    command, shell=True, capture_output=True, text=True,
                    timeout=timeout, check=False,
                )
            else:
                args = shlex.split(command, posix=(os.name != "nt"))
                completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    args, shell=False, capture_output=True, text=True,
                    timeout=timeout, check=False,
                )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            return {"returncode": None, "stdout": "", "stderr": "", "timed_out": True}

    # ------------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------------
    async def backend_status(self) -> dict:
        report = self.backend_manager.status_report()
        return {
            "success": True,
            "backends": report,
            "vision": self.vision.status(),
            "emergency_stop": {
                "engaged": self.stop.triggered,
                "reason": self.stop.reason or None,
            },
            "security": {
                "mode": self.policy.mode,
                "allow_shell": self.config.security.allow_shell,
                "allowed_commands": self.config.security.allowed_commands,
            },
        }

    async def emergency_stop(self, reason: str = "requested via MCP") -> dict:
        """Immediately stop automation; checked before every action/attempt."""
        self.stop.trigger(reason)
        return {"success": True, "emergency_stop": "engaged", "reason": reason}

    async def reset_emergency_stop(self) -> dict:
        self.stop.reset()
        return {"success": True, "emergency_stop": "cleared"}

    def recent_actions(self, count: int = 20) -> list[dict]:
        return self.history.recent(count)

    def shutdown(self) -> None:
        """Release resources (kept for symmetry; backends are stateless)."""
        logger.info("Engine shutdown")

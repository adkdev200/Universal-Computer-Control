"""Recovery engine: ordered attempts, verification, retries and duplicate
suppression.

Key behaviours (design §5, §17, §18):

- Attempts run in interaction-hierarchy order and stop at the first success.
- Every attempt is executed inside a timeout; backend failures feed the
  BackendManager health tracking so future selections skip broken backends.
- Verification compares before/after state; in ``strict`` mode an action that
  produced no observable change is treated as failed.
- Identical actions repeated within ``duplicate_window_ms`` are only re-run
  when the screen actually changed since the previous execution - this
  prevents dangerous duplicate clicks when verification is inconclusive.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from universal_computer.backends.base import Backend, Cap
from universal_computer.config import EngineConfig
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.errors import (
    ActionFailedError,
    EmergencyStopError,
    RateLimitExceededError,
    SecurityViolationError,
)
from universal_computer.core.observer import Observer
from universal_computer.core.verifier import Verifier
from universal_computer.logging import get_logger
from universal_computer.models.action import (
    ActionResult,
    ActionType,
    AttemptRecord,
    ResolvedTarget,
)
from universal_computer.persistence.history import ActionHistory
from universal_computer.security.emergency_stop import EmergencyStop
from universal_computer.security.policy import SecurityPolicy

logger = get_logger("core.recovery")


@dataclass
class Attempt:
    """A single executable attempt at performing an action."""

    method: str
    run: Callable[[], None]
    backend: str | None = None
    confidence: float = 0.8
    description: str = ""
    semantic: bool = False
    # Set when the attempt should be skipped because its precondition vanished
    # (e.g. the resolved element is no longer present after a re-observation).
    precondition: Callable[[], bool] | None = field(default=None)


class ActionExecutor:
    """Executes attempts with fallback, verification and duplicate guard."""

    def __init__(
        self,
        config: EngineConfig,
        observer: Observer,
        verifier: Verifier,
        backend_manager: BackendManager,
        history: ActionHistory,
        stop: EmergencyStop,
        policy: SecurityPolicy,
    ) -> None:
        self.config = config
        self.observer = observer
        self.verifier = verifier
        self.backends = backend_manager
        self.history = history
        self.stop = stop
        self.policy = policy
        # (action, target, method) -> (timestamp, after-signature)
        self._recent: deque[tuple[tuple, float, dict | None]] = deque(maxlen=32)

    # ------------------------------------------------------------------
    async def execute(
        self,
        action: ActionType,
        target_repr: str | None,
        resolved: ResolvedTarget | None,
        attempts: list[Attempt],
        verify: bool | None = None,
        *,
        expect_change: bool = True,
    ) -> ActionResult:
        self.stop.check()
        decision = self.policy.check_rate(action.value)
        if not decision.allowed:
            raise RateLimitExceededError(decision.reason or "rate limited")

        verify_enabled = self.config.verify_actions if verify is None else verify
        started = time.monotonic()
        before_obs = None
        signature_before = None
        if verify_enabled:
            # "normal" so the before-state includes a screenshot hash + OCR.
            before_obs = await self.observer.observe("normal", force=True)
            signature_before = self.verifier.signature(before_obs)

        records: list[AttemptRecord] = []
        last_error: str | None = None
        success = False
        used: Attempt | None = None

        for index, attempt in enumerate(attempts):
            self.stop.check()  # cooperative abort between attempts
            key = (action.value, target_repr, attempt.method)
            if index > 0 or self._is_duplicate(key, signature_before):
                if self._is_duplicate(key, signature_before):
                    changed = self._state_changed_since(key, signature_before)
                    if not changed:
                        record = AttemptRecord(
                            method=attempt.method,
                            backend=attempt.backend,
                            detail="skipped: identical action repeated while state "
                            "unchanged (duplicate guard)",
                            success=False,
                            skipped=True,
                        )
                        records.append(record)
                        last_error = record.detail
                        continue

            if attempt.precondition is not None and not attempt.precondition():
                records.append(
                    AttemptRecord(
                        method=attempt.method,
                        backend=attempt.backend,
                        detail="skipped: precondition no longer holds",
                        success=False,
                        skipped=True,
                    )
                )
                continue

            attempt_started = time.monotonic()
            ok, error = await self._run_attempt(attempt)
            duration_ms = (time.monotonic() - attempt_started) * 1000.0
            records.append(
                AttemptRecord(
                    method=attempt.method,
                    backend=attempt.backend,
                    detail=attempt.description,
                    success=ok,
                    error=error,
                    duration_ms=round(duration_ms, 1),
                )
            )
            if ok:
                success = True
                used = attempt
                break
            last_error = error

        verification = None
        observation_after_id = None
        signature_after = None
        if success and verify_enabled:
            after_obs = await self.observer.observe("normal", force=True)
            observation_after_id = after_obs.id
            signature_after = self.verifier.signature(after_obs)
            verification = self.verifier.compare(signature_before, signature_after)
            verification.success = verification.changed if verification.changed is not None else None
            if (
                expect_change
                and self.config.verification_mode == "strict"
                and verification.changed is False
            ):
                # §18: never blindly repeat - treat as failure and report state.
                success = False
                last_error = (
                    "action executed but produced no observable change "
                    "(strict verification)"
                )
                verification.success = False
            elif verification.changed is False:
                verification.details = (
                    "no observable change detected; action may still have succeeded "
                    "(verification is advisory in basic mode)"
                )

        if success and used is not None:
            self._remember((action.value, target_repr, used.method), signature_after)

        result = ActionResult(
            success=success,
            action=action.value,
            target=target_repr,
            method=used.method if used is not None else (attempts[0].method if attempts else "none"),
            backend=used.backend if used is not None else None,
            coordinates=resolved.coordinates if resolved is not None else None,
            confidence=used.confidence if used is not None else 0.0,
            duration_ms=round((time.monotonic() - started) * 1000.0, 1),
            error=None if success else (last_error or "all attempts failed"),
            verification=verification,
            attempts=records,
            element=resolved.element if resolved is not None else None,
            observation_id_before=before_obs.id if before_obs is not None else None,
            observation_id_after=observation_after_id,
        )
        self.history.record(result)
        return result

    # ------------------------------------------------------------------
    async def _run_attempt(self, attempt: Attempt) -> tuple[bool, str | None]:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(attempt.run), timeout=self.config.action_timeout_s
            )
        except EmergencyStopError:
            raise
        except TimeoutError:
            if attempt.backend:
                self.backends.record_failure(attempt.backend, "timeout")
            return False, f"attempt timed out after {self.config.action_timeout_s}s"
        except Exception as exc:  # noqa: BLE001 - failures feed the fallback chain
            message = f"{type(exc).__name__}: {exc}"
            if attempt.backend:
                self.backends.record_failure(attempt.backend, message)
            logger.info("Attempt '%s' failed: %s", attempt.method, message)
            return False, message
        if attempt.backend:
            self.backends.record_success(attempt.backend)
        return True, None

    # -- duplicate guard ----------------------------------------------------
    def _is_duplicate(self, key: tuple, _current_signature: dict | None) -> bool:
        window_s = self.config.duplicate_window_ms / 1000.0
        if window_s <= 0:
            return False
        now = time.monotonic()
        for recent_key, stamp, _sig in reversed(self._recent):
            if recent_key == key and now - stamp <= window_s:
                return True
        return False

    def _state_changed_since(self, key: tuple, current_signature: dict | None) -> bool:
        """Compare the stored after-signature of the previous identical action."""
        for recent_key, _stamp, sig in reversed(self._recent):
            if recent_key == key:
                if sig is None or current_signature is None:
                    # No comparable state: assume unchanged to be safe.
                    return False
                result = self.verifier.compare(sig, current_signature)
                return bool(result.changed)
        return True

    def _remember(self, key: tuple, signature_after: dict | None) -> None:
        self._recent.append((key, time.monotonic(), signature_after))


def input_backend(manager: BackendManager) -> Backend:
    """Fetch the best input backend or raise a clear error."""
    try:
        return manager.select(Cap.INPUT)
    except Exception as exc:
        raise ActionFailedError(
            "no physical input backend available (install PyAutoGUI: "
            "pip install 'universal-computer-control[input]')"
        ) from exc


def security_denied(reason: str) -> SecurityViolationError:
    return SecurityViolationError(reason)

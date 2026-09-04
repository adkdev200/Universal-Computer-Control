"""Backend registry and capability-based selection.

The manager is the single place where the engine asks "who can do X?".
Backends are ranked by priority (higher wins); unavailable or temporarily
disabled backends (after repeated failures) are skipped automatically, which
is what makes graceful degradation and fallback work engine-wide.
"""

from __future__ import annotations

import threading

from universal_computer.backends.base import Backend, BackendHealth
from universal_computer.config import BackendsConfig
from universal_computer.core.errors import BackendUnavailableError
from universal_computer.logging import get_logger

logger = get_logger("backends.manager")


class BackendManager:
    """Registers backends and selects the best available one per capability."""

    def __init__(self, config: BackendsConfig | None = None) -> None:
        self.config = config or BackendsConfig()
        self._backends: list[Backend] = []
        self._lock = threading.RLock()

    # -- registration ---------------------------------------------------------
    def register(self, backend: Backend) -> None:
        override = self.config.priorities.get(backend.name)
        if override is not None:
            backend.priority = int(override)
        with self._lock:
            self._backends.append(backend)
        logger.info(
            "Registered backend '%s' (platform=%s, priority=%s, capabilities=%s)",
            backend.name,
            backend.platform,
            backend.priority,
            sorted(backend.capabilities()),
        )

    def backends(self) -> list[Backend]:
        with self._lock:
            return list(self._backends)

    def get(self, name: str) -> Backend | None:
        for backend in self.backends():
            if backend.name == name:
                return backend
        return None

    def unregister(self, name: str) -> bool:
        """Remove a backend from the registry (used in tests and hot-swaps)."""
        with self._lock:
            before = len(self._backends)
            self._backends = [b for b in self._backends if b.name != name]
            return len(self._backends) < before

    # -- selection --------------------------------------------------------------
    def candidates(self, capability: str) -> list[Backend]:
        """Available backends supporting ``capability``, best first."""
        matches = [
            b
            for b in self.backends()
            if capability in b.capabilities()
            and b.is_available()
            and not b.temporarily_disabled
        ]
        matches.sort(key=lambda b: (-b.priority, b.name))
        return matches

    def select(self, capability: str) -> Backend:
        """Best available backend for ``capability`` or raise."""
        candidates = self.candidates(capability)
        if not candidates:
            tried = sorted(b.name for b in self.backends() if capability in b.capabilities())
            raise BackendUnavailableError(
                capability,
                reason=f"tried: {tried or 'no registered backend supports it'}",
            )
        return candidates[0]

    def select_all(self, capability: str) -> list[Backend]:
        return self.candidates(capability)

    def has(self, capability: str) -> bool:
        return bool(self.candidates(capability))

    # -- failure bookkeeping -----------------------------------------------------
    def record_success(self, backend_name: str | None) -> None:
        if not backend_name:
            return
        backend = self.get(backend_name)
        if backend is not None:
            backend.record_success()

    def record_failure(self, backend_name: str | None, error: str = "") -> None:
        if not backend_name:
            return
        backend = self.get(backend_name)
        if backend is not None:
            backend.record_failure()
            logger.warning("Backend '%s' recorded failure: %s", backend_name, error)

    # -- status -----------------------------------------------------------------
    def status_report(self) -> dict[str, dict]:
        """Per-backend health in the ``computer.backend_status`` shape."""
        report: dict[str, dict] = {}
        for backend in self.backends():
            health: BackendHealth = backend.health_check()
            report[backend.name] = {
                "available": health.available,
                "healthy": health.healthy,
                "status": health.status.value,
                "platform": health.platform,
                "priority": backend.priority,
                "capabilities": sorted(backend.capabilities()),
                "details": health.details,
                "error": health.error,
            }
        return report

    def health_check_all(self) -> dict[str, dict]:
        """Deep health check of every registered backend."""
        return self.status_report()

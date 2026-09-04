"""Unit tests for backend selection, failure handling and fallback (§4, §29)."""

import pytest

from tests.mocks.mock_backends import MockInputBackend, MockScreenshotBackend
from universal_computer.backends.base import Cap
from universal_computer.config import BackendsConfig
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.errors import BackendUnavailableError


class TestBackendManager:
    def test_register_and_select_by_priority(self):
        manager = BackendManager()
        low = MockScreenshotBackend()
        low.priority = 10
        high = MockScreenshotBackend()
        high.priority = 90
        manager.register(low)
        manager.register(high)
        assert manager.select(Cap.SCREENSHOT) is high

    def test_select_skips_unavailable(self):
        manager = BackendManager()
        broken = MockScreenshotBackend()
        broken._probe_error = "no display"
        broken._available = False  # force unavailable
        working = MockInputBackend()
        manager.register(broken)
        manager.register(working)
        # input backend also declares SCREENSHOT? No: input+cursor only.
        with pytest.raises(BackendUnavailableError):
            manager.select(Cap.SCREENSHOT)
        assert manager.select(Cap.INPUT) is working

    def test_priority_override_from_config(self):
        manager = BackendManager(BackendsConfig(priorities={"mock-input": 500}))
        backend = MockInputBackend()
        manager.register(backend)
        assert backend.priority == 500
        assert manager.select(Cap.INPUT) is backend

    def test_record_failures_disables_backend_temporarily(self):
        manager = BackendManager()
        backend = MockInputBackend()
        manager.register(backend)
        for _ in range(backend.FAILURE_THRESHOLD):
            manager.record_failure(backend.name, "boom")
        assert backend.temporarily_disabled
        with pytest.raises(BackendUnavailableError):
            manager.select(Cap.INPUT)

    def test_record_success_resets_failures(self):
        manager = BackendManager()
        backend = MockInputBackend()
        manager.register(backend)
        backend.record_failure()
        backend.record_failure()
        manager.record_success(backend.name)
        assert backend._failure_count == 0

    def test_status_report_shape(self):
        manager = BackendManager()
        manager.register(MockInputBackend())
        report = manager.status_report()
        assert "mock-input" in report
        entry = report["mock-input"]
        assert entry["available"] is True
        assert entry["healthy"] is True
        assert Cap.INPUT in entry["capabilities"]

    def test_select_all_ordered(self):
        manager = BackendManager()
        first = MockInputBackend()
        second = MockInputBackend()
        second.name = "mock-input-2"
        first.priority, second.priority = 20, 80
        manager.register(first)
        manager.register(second)
        assert [b.name for b in manager.select_all(Cap.INPUT)] == ["mock-input-2", "mock-input"]

    def test_select_error_lists_tried_backends(self):
        manager = BackendManager()
        backend = MockScreenshotBackend()
        backend._available = False
        manager.register(backend)
        with pytest.raises(BackendUnavailableError) as exc_info:
            manager.select(Cap.SCREENSHOT)
        assert "mock-screen" in str(exc_info.value)


class TestBackendProbe:
    def test_probe_exception_means_unavailable(self):
        class Exploding(MockScreenshotBackend):
            def _probe(self):
                raise RuntimeError("no X11")

        backend = Exploding()
        assert backend.is_available() is False
        health = backend.health_check()
        assert health.healthy is False
        assert "no X11" in (health.error or "")

    def test_probe_cached(self):
        backend = MockScreenshotBackend()
        calls = {"n": 0}
        original = backend._probe

        def counting():
            calls["n"] += 1
            return original()

        backend._probe = counting
        assert backend.is_available()
        assert backend.is_available()
        assert calls["n"] == 1


class TestEngineAutoRegistration:
    def test_engine_init_without_backend_manager_registers_backends(self):
        from universal_computer import ComputerControlEngine

        engine = ComputerControlEngine()
        assert len(engine.backend_manager.backends()) > 0

    def test_build_default_engine_without_args(self):
        from universal_computer import build_default_engine

        engine = build_default_engine()
        assert len(engine.backend_manager.backends()) > 0


"""Factory: assemble a fully-wired engine from configuration.

Registers every backend that is actually usable on this machine (probing is
cheap and lazy). A machine with only PyAutoGUI still produces a working
engine; UIA/AT-SPI/OCR/vision simply report unavailable warnings (design §29).
"""

from __future__ import annotations

import sys

from universal_computer.config import AppConfig
from universal_computer.core.backend_manager import BackendManager
from universal_computer.core.engine import ComputerControlEngine
from universal_computer.logging import get_logger

logger = get_logger("core.bootstrap")


def build_backend_manager(config: AppConfig) -> BackendManager:
    manager = BackendManager(config.backends)

    # Cross-platform physical input + secondary screenshot backend.
    _try_register(manager, "universal_computer.backends.pyautogui_backend", "PyAutoGUIBackend", config.input)

    # Preferred screenshot backends (fast multi-monitor first, then Pillow).
    _try_register(manager, "universal_computer.backends.screenshot", "MssScreenshotBackend")
    _try_register(manager, "universal_computer.backends.screenshot", "PillowScreenshotBackend")

    platform = sys.platform
    if platform == "win32":
        _try_register(manager, "universal_computer.backends.windows_uia", "WindowsUIABackend")
        _try_register(manager, "universal_computer.backends.windows_manager", "WindowsWindowBackend")
        _try_register(manager, "universal_computer.backends.clipboard", "PowerShellClipboardBackend")
        _try_register(manager, "universal_computer.backends.applications", "WindowsApplicationLauncher")
    elif platform == "linux":
        _try_register(manager, "universal_computer.backends.linux_atspi", "LinuxATSPIBackend")
        _try_register(manager, "universal_computer.backends.linux_window", "LinuxWindowBackend")
        _try_register(manager, "universal_computer.backends.clipboard", "XClipboardBackend")
        _try_register(manager, "universal_computer.backends.applications", "UnixApplicationLauncher")
    else:
        logger.info("No platform-specific backends for %s (macOS support is future work)", platform)

    # Cross-platform clipboard fallback.
    _try_register(manager, "universal_computer.backends.clipboard", "PyperclipClipboardBackend")
    return manager


def _try_register(manager: BackendManager, module_name: str, class_name: str, *args) -> bool:
    """Import and register a backend; return False (with a warning) if unusable."""
    try:
        module = __import__(module_name, fromlist=[class_name])
        klass = getattr(module, class_name)
        instance = klass(*args)
        if not instance.is_available():
            details = getattr(instance, "_probe_error", "")
            logger.warning("Backend '%s' unavailable: %s", class_name, details)
            return False
        manager.register(instance)
        return True
    except Exception as exc:  # noqa: BLE001 - optional backends must never crash startup
        logger.warning("Backend '%s.%s' failed to load: %s", module_name, class_name, exc)
        return False


def build_default_engine(config: AppConfig) -> ComputerControlEngine:
    """Create a :class:`ComputerControlEngine` with all default components."""
    engine = ComputerControlEngine(config, backend_manager=build_backend_manager(config))
    available = [name for name, info in engine.backend_manager.status_report().items() if info["available"]]
    logger.info("Engine ready; available backends: %s", available or "none (degraded mode)")
    return engine

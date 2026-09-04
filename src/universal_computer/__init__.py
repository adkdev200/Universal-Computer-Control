"""Universal Computer Control MCP.

A cross-platform computer-control engine with an MCP interface. The engine can
be used standalone (``ComputerControlEngine``) or served over the Model Context
Protocol (``python -m universal_computer``).
"""

from __future__ import annotations

__version__ = "0.1.1"

from universal_computer.config import AppConfig, load_config
from universal_computer.core.bootstrap import build_default_engine
from universal_computer.core.engine import ComputerControlEngine

__all__ = [
    "AppConfig",
    "ComputerControlEngine",
    "__version__",
    "build_default_engine",
    "load_config",
]

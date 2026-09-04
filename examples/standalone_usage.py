#!/usr/bin/env python3
"""Standalone engine demo - NO MCP server required (design §32, §44).

Run from the project root::

    python examples/standalone_usage.py

This demonstrates that the Computer Control Engine is usable directly.
On a headless machine it shows the graceful-degradation path.
"""

from __future__ import annotations

import asyncio

from universal_computer.config import load_config
from universal_computer.core.bootstrap import build_default_engine
from universal_computer.core.errors import UniversalComputerError


async def main() -> None:
    config = load_config()
    computer = build_default_engine(config)

    print("=== Backend status ===")
    status = await computer.backend_status()
    for name, info in status["backends"].items():
        print(f"  {name:<22} available={info['available']!s:<5} healthy={info['healthy']!s:<5} {info['details']}")
    for kind, info in status["vision"].items():
        print(f"  vision.{kind:<16} available={info['available']}")

    print("\n=== Observe ===")
    try:
        observation = await computer.observe("normal")
        print(f"  observation: {observation.id} level={observation.level.value}")
        print(f"  active window: {observation.active_window.title if observation.active_window else None}")
        print(f"  elements: {len(observation.elements)}, ocr lines: {len(observation.ocr)}")
        if observation.notes:
            print(f"  notes: {observation.notes}")
    except UniversalComputerError as exc:
        print(f"  (degraded) {type(exc).__name__}: {exc}")

    print("\n=== find_text('Continue') ===")
    try:
        result = await computer.find_text("Continue")
        print(f"  matches: {result['count']}")
    except UniversalComputerError as exc:
        print(f"  (degraded) {type(exc).__name__}: {exc}")

    print("\n=== Recent actions ===")
    for entry in computer.recent_actions(5):
        print(f"  {entry}")

    print("\nStandalone engine demo complete - the same engine backs the MCP server.")
    print("Start it with: python -m universal_computer")


if __name__ == "__main__":
    asyncio.run(main())

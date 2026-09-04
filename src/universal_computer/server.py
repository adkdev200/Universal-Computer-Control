"""Server entrypoint: wraps the engine with the MCP stdio protocol.

Run with::

    python -m universal_computer [--config /path/to/config.yaml]

or via the installed script::

    universal-computer-control
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from universal_computer import __version__
from universal_computer.config import load_config
from universal_computer.core.bootstrap import build_default_engine
from universal_computer.core.engine import ComputerControlEngine
from universal_computer.logging import get_logger, setup_logging
from universal_computer.mcp.compat import FastMCP
from universal_computer.mcp.tools import register_all_tools
from universal_computer.persistence.state import PersistenceManager

logger = get_logger("server")


def create_mcp_app(
    engine: ComputerControlEngine | None = None,
    config_path: str | None = None,
) -> Any:
    """Create the FastMCP application around a (new or given) engine."""
    config = load_config(config_path)
    if engine is None:
        engine = build_default_engine(config)
    persistence = PersistenceManager(config.persistence)
    setup_logging(config.logging, persistence.home)
    app = FastMCP(name=config.server.name, instructions=(
        "Universal Computer Control: operate the computer like a human. "
        "Start with computer.observe (or computer.screenshot), understand the "
        "screen, act with computer.click/type/press, then observe again and "
        "verify. Works on any application without browser-specific MCPs."
    ))
    register_all_tools(app, engine)

    original_run = app.run

    def run_with_cleanup(*args, **kwargs):
        try:
            original_run(*args, **kwargs)
        finally:
            engine.shutdown()

    app.run = run_with_cleanup  # type: ignore[method-assign]
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="universal-computer-control",
        description="Universal Computer Control MCP server (stdio transport)",
    )
    parser.add_argument("--config", help="path to config.yaml", default=None)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    # Stdout belongs to the MCP protocol; make sure nothing else writes there.
    app = create_mcp_app(config_path=args.config)
    logger.info("Starting MCP server (stdio)")
    try:
        app.run(transport="stdio")
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        logger.info("Shutting down (keyboard interrupt)")
    except Exception as exc:  # noqa: BLE001
        logger.error("Server crashed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install (editable) with dev deps:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # unit-test deps; add [input,linux,ocr,vision,screenshot,vlm] for runtime backends
```

CI installs `.venv/bin/pip install -e ".[input,ocr,vision,screenshot,vlm,dev]"` and also Linux system packages (`wmctrl xdotool xclip scrot tesseract-ocr at-spi2-core`).

Run tests:

```bash
pytest tests                      # full suite, no display required
pytest tests/unit/test_config.py  # single file
pytest tests/unit/test_config.py::test_name   # single test
```

(`asyncio_mode = "auto"` is set in `pyproject.toml`.)

Lint / typecheck:

```bash
ruff check src tests
mypy src/universal_computer        # python_version = 3.12 in tool config
```

Run the server / diagnostics:

```bash
python -m universal_computer [--config /path/to/config.yaml]
ucc                               # installed script alias
ucc-doctor                        # probes every backend, prints fix hints
python scripts/e2e_live_test.py   # end-to-end with a self-booted Xvfb session
python scripts/e2e_live_test.py --no-tools   # exercise python-Xlib fallback only
```

Environment overrides use `UCC_` prefix with `__` nesting, e.g. `UCC_LOGGING__LEVEL=DEBUG UCC_SECURITY__MODE=strict`. `ucc-doctor` and config loading respect `UCC_HOME` / `persistence.home` for the `~/.universal-computer` home.

## Architecture

**MCP is a thin wrapper; the engine is a plain async library.** `mcp/compat.py` + `mcp/tools*.py` only validate args, call the engine, and convert exceptions to `{"ok": false, "error": {...}}`. `ComputerControlEngine` (`core/engine.py`) is the facade; it can be used standalone without any MCP server (`from universal_computer import ComputerControlEngine, load_config`). Do not put MCP imports in `core/`, `backends/`, `vision/`, or `persistence/`.

Layering and module roles are in `docs/ARCHITECTURE.md` (module map, request flow, degradation matrix). The guiding rules:

- `core/bootstrap.py` (`build_default_engine`) registers only backends that import/work on this machine; missing optional deps degrade gracefully and the server still answers.
- `backends/base.py` holds the ABCs (`InputBackend`, `ScreenshotBackend`, `AccessibilityBackend`, `WindowManagementBackend`, `ClipboardBackend`, `ApplicationBackend`). Platform-specific code lives in `windows_*` / `linux_*` backends; new platforms subclass these and register in `bootstrap.py`.
- `core/backend_manager.py` is the capability registry with priority selection, failure cooldowns (`3 consecutive failures ⇒ 30 s`), and `backend_status()` reports.
- `vision/base.py` defines `OCRProvider`/`VisionLanguageModel`/`VisionServices`; new providers implement those ABCs and register in `VisionServices.from_config`.
- `core/resolver.py` implements the interaction hierarchy (coordinates → `element_N` registry → accessibility fuzzy match → fresh a11y search → OCR fuzzy match → template → optional VLM). `core/actions.py` then plans attempts (semantic-invoke first, then coordinate clicks, deduped), and `core/recovery.py` (`ActionExecutor`) runs them with timeouts, duplicate suppression, and verification (`core/verifier.py` compares dhash / OCR sets / active window).

Config resolution order in `config.py`: defaults ← YAML (`--config` or `./config.yaml`) ← `UCC_*` env overrides (`__` nesting). Security policy (`security/policy.py`) enforces modes, allowlists, rate limits, and confirmation gates; `security/emergency_stop.py` persists a stop file so external triggers survive restarts.

Persistence lives under `~/.universal-computer/` (`persistence/state.py`, `persistence/history.py`): config/state/logs/screenshots/observations/sessions, with retention cleanup. Logs are structured JSON with credential redaction (`logging.py`).

Contributions expected: unit tests use mocked backends (`tests/mocks/mock_backends.py`, `tests/conftest.py`'s `build_engine`); OS-specific paths are mocked, so the suite runs on any machine. The integration test boots the real server over stdio. Ruff line length 110, target py311; mypy ignores missing imports and targets 3.12.

## Notes

- Optional foreign-agent configs exist on this machine (`~/.codex/config.toml`, `~/.gemini/GEMINI.md`). If you want to migrate MCP servers, slash commands, subagents, skills, or instructions from them, reply with `/import` to scan and review, then `/import --yes=<digest>` to apply. (If `/import` is unavailable in this environment, run `claude import` from a terminal instead.) Do not paste config contents into the repo.
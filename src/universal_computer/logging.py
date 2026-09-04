"""Structured logging with redaction of sensitive material.

Design notes:
- The MCP stdio transport owns **stdout**; all log output goes to stderr and
  rotating files under ``<home>/logs``.
- A redaction filter masks common credential patterns in every message.
- Action records (JSONL) are written by ``persistence.history`` and also pass
  through the same redaction.
"""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from universal_computer.config import LoggingConfig

LOGGER_NAME = "universal_computer"

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(
        r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[=:]\s*\S+"
    ),
]
_MASK = "[REDACTED]"
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|cookie|credential|clipboard)",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    """Mask credential-looking substrings inside a log message."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_MASK, result)
    return result


def redact_mapping(data: dict) -> dict:
    """Return a copy of a dict with values of sensitive keys masked."""
    clean: dict = {}
    for key, value in data.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            clean[key] = _MASK
        elif isinstance(value, dict):
            clean[key] = redact_mapping(value)
        else:
            clean[key] = value
    return clean


class RedactionFilter(logging.Filter):
    """Mask credential patterns in log records before they reach a handler."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.args, dict):
            record.args = redact_mapping(record.args)
        else:
            record.args = tuple(
                redact_text(str(a)) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str) and not record.args:
            record.msg = redact_text(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k
            not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "taskName",
                "message",
            }
        }
        if extras:
            payload.update(redact_mapping(extras))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(cfg: LoggingConfig, home: Path) -> None:
    """Configure the package logger. Safe to call more than once."""
    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(getattr(logging, str(cfg.level).upper(), logging.INFO))
    root.addFilter(RedactionFilter())
    if getattr(root, "_ucm_configured", False):
        return
    root._ucm_configured = True  # type: ignore[attr-defined]

    if cfg.console:
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(console)

    try:
        log_dir = Path(cfg.dir).expanduser() if cfg.dir else home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / cfg.file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - unwritable disk must not kill the server
        root.warning("Could not create log file handler; logging to console only")

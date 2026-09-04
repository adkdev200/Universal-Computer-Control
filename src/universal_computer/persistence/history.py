"""Structured action history (JSONL) plus small custom-event recording.

Every executed action appends one JSON line to ``logs/actions.jsonl`` with
timestamp, action, target, backend/method, coordinates, confidence, result,
duration and error. Sensitive values are redacted before writing; typing
actions record the *length* of the text rather than the text itself.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_computer.logging import redact_text
from universal_computer.persistence.state import PersistenceManager


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sanitize_record(record: dict) -> dict:
    """Mask credential-like values inside a history record."""
    clean: dict[str, Any] = {}
    for key, value in record.items():
        if key in {"error", "target", "description", "detail"} and isinstance(value, str):
            clean[key] = redact_text(value)
        else:
            clean[key] = value
    return clean


class ActionHistory:
    """Append-only JSONL history with a bounded in-memory mirror."""

    def __init__(self, persistence: PersistenceManager, memory_size: int = 200) -> None:
        self.persistence = persistence
        self.path: Path = persistence.logs_dir / "actions.jsonl"
        self._lock = threading.Lock()
        self._memory: deque[dict] = deque(maxlen=memory_size)

    def record(self, result: Any) -> dict:
        """Record an :class:`ActionResult` (or any object with the fields)."""
        entry: dict[str, Any] = {
            "timestamp": _now_iso(),
            "action": getattr(result, "action", "unknown"),
            "target": getattr(result, "target", None),
            "method": getattr(result, "method", None),
            "backend": getattr(result, "backend", None),
            "coordinates": (
                list(result.coordinates) if getattr(result, "coordinates", None) else None
            ),
            "confidence": getattr(result, "confidence", 0.0),
            "success": bool(getattr(result, "success", False)),
            "duration_ms": getattr(result, "duration_ms", 0.0),
            "error": getattr(result, "error", None),
        }
        verification = getattr(result, "verification", None)
        if verification is not None:
            entry["verification"] = {
                "performed": verification.performed,
                "changed": verification.changed,
                "similarity": verification.similarity,
            }
        return self.record_custom(entry)

    def record_custom(self, entry: dict) -> dict:
        """Record a pre-built entry dict (used for launch/clipboard/etc.)."""
        entry.setdefault("timestamp", _now_iso())
        entry = _sanitize_record(entry)
        with self._lock:
            self._memory.append(entry)
            self._append_line(entry)
        return entry

    def _append_line(self, entry: dict) -> None:
        if not self.persistence.enabled:
            return
        try:
            self.persistence.logs_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            # History loss is not fatal; the in-memory mirror still works.
            print(f"[universal_computer] history append failed: {exc}")

    def recent(self, count: int = 20) -> list[dict]:
        with self._lock:
            items = list(self._memory)
        return items[-max(0, count):]

    def clear_memory(self) -> None:
        with self._lock:
            self._memory.clear()

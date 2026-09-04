"""Local persistent state under ``~/.universal-computer`` (configurable).

Layout::

    ~/.universal-computer/
        config/          config.yaml, templates for visual matching
        state/           backend health snapshots, emergency-stop flag
        logs/            server.log, actions.jsonl
        screenshots/     persisted observation screenshots
        observations/    persisted observation JSON
        sessions/        one JSON file per server session

All writes are best-effort: a read-only or missing home directory degrades to
in-memory operation with warnings instead of crashing the server.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from universal_computer.config import PersistenceConfig
from universal_computer.logging import get_logger

logger = get_logger("persistence")

_SUBDIRS = ("config", "state", "logs", "screenshots", "observations", "sessions")


class PersistenceManager:
    """Creates and manages the on-disk state directory."""

    def __init__(self, cfg: PersistenceConfig) -> None:
        self.cfg = cfg
        env_home = os.environ.get("UCC_HOME")
        raw_home = cfg.home or env_home
        self.home = Path(raw_home).expanduser() if raw_home else Path.home() / ".universal-computer"
        self.enabled = True
        self.dirs: dict[str, Path] = {}
        for name in _SUBDIRS:
            path = self.home / name
            self.dirs[name] = path
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for path in self.dirs.values():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Persistence disabled (%s): %s", path, exc)
                self.enabled = False
                return

    # -- paths ---------------------------------------------------------------
    @property
    def config_dir(self) -> Path:
        return self.dirs["config"]

    @property
    def state_dir(self) -> Path:
        return self.dirs["state"]

    @property
    def logs_dir(self) -> Path:
        return self.dirs["logs"]

    @property
    def screenshots_dir(self) -> Path:
        return self.dirs["screenshots"]

    @property
    def observations_dir(self) -> Path:
        return self.dirs["observations"]

    @property
    def sessions_dir(self) -> Path:
        return self.dirs["sessions"]

    def new_screenshot_path(self, stem: str) -> Path:
        return self.screenshots_dir / f"{stem}.png"

    def new_observation_path(self, obs_id: str) -> Path:
        return self.observations_dir / f"{obs_id}.json"

    # -- writes ----------------------------------------------------------------
    def save_screenshot(self, image: Any, stem: str) -> Path | None:
        """Persist a PIL image; returns the path or None when disabled/failed."""
        if not self.enabled or not self.cfg.save_screenshots:
            return None
        try:
            path = self.new_screenshot_path(stem)
            image.save(path, format="PNG")
            return path
        except (OSError, ValueError) as exc:
            logger.warning("Could not save screenshot: %s", exc)
            return None

    def write_json(self, path: Path, payload: Any) -> Path | None:
        if not self.enabled:
            return None
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )
            return path
        except (OSError, TypeError) as exc:
            logger.warning("Could not write %s: %s", path, exc)
            return None

    def save_observation(self, observation: Any) -> Path | None:
        if not self.enabled or not self.cfg.save_observations:
            return None
        try:
            payload = observation.to_dict()
        except Exception as exc:  # noqa: BLE001 - persistence must never crash
            logger.warning("Could not serialize observation: %s", exc)
            return None
        return self.write_json(self.new_observation_path(observation.id), payload)

    def write_state(self, name: str, payload: Any) -> Path | None:
        return self.write_json(self.state_dir / name, payload)

    def write_session(self, session_id: str, payload: dict) -> Path | None:
        return self.write_json(self.sessions_dir / f"{session_id}.json", payload)

    # -- cleanup -----------------------------------------------------------
    def cleanup(self) -> dict[str, int]:
        """Enforce retention rules; returns removed-file counts per directory."""
        removed: dict[str, int] = {}
        if not self.enabled:
            return removed
        rules = [
            ("screenshots", self.cfg.retention_days, self.cfg.max_screenshots),
            ("observations", self.cfg.retention_days, self.cfg.max_observations),
            ("logs", self.cfg.retention_days, None),
            ("sessions", max(self.cfg.retention_days, 1), 200),
        ]
        for name, retention_days, max_files in rules:
            removed[name] = self._cleanup_dir(
                self.dirs[name], retention_days=retention_days, max_files=max_files
            )
        return removed

    def _cleanup_dir(self, directory: Path, retention_days: int, max_files: int | None) -> int:
        removed = 0
        try:
            files = sorted(
                (p for p in directory.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return 0
        cutoff = time.time() - max(0, retention_days) * 86400
        for path in files:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        if max_files is not None:
            excess = len(files) - max_files
            for path in files[: max(0, excess)]:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

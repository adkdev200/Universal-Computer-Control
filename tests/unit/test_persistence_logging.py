"""Unit tests for persistence, history and log redaction (§21, §22)."""

import json
import os
import time
from pathlib import Path

from PIL import Image

from universal_computer.config import PersistenceConfig
from universal_computer.logging import redact_mapping, redact_text
from universal_computer.persistence.history import ActionHistory
from universal_computer.persistence.state import PersistenceManager


def _cfg(tmp_path: Path):
    from universal_computer.config import PersistenceConfig

    return PersistenceConfig(home=str(tmp_path / "home"))


class TestPersistenceManager:
    def test_directories_created(self, tmp_path):
        manager = PersistenceManager(_cfg(tmp_path))
        for name in ("config", "state", "logs", "screenshots", "observations", "sessions"):
            assert manager.dirs[name].is_dir()

    def test_save_screenshot(self, tmp_path):
        manager = PersistenceManager(_cfg(tmp_path))
        path = manager.save_screenshot(Image.new("RGB", (10, 10)), "shot_test")
        assert path is not None and path.is_file()

    def test_save_observation(self, tmp_path):
        from universal_computer.models.observation import Observation

        manager = PersistenceManager(_cfg(tmp_path))
        observation = Observation(id="obs_9")
        path = manager.save_observation(observation)
        assert path is not None and path.is_file()
        payload = json.loads(path.read_text())
        assert payload["id"] == "obs_9"

    def test_disabled_when_unwritable(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        broken = PersistenceManager(PersistenceConfig(home=str(blocker / "child")))
        assert broken.enabled is False
        assert broken.save_screenshot(Image.new("RGB", (4, 4)), "x") is None

    def test_cleanup_by_retention(self, tmp_path):
        from universal_computer.config import PersistenceConfig

        cfg = PersistenceConfig(home=str(tmp_path / "home"), retention_days=0, max_screenshots=100)
        manager = PersistenceManager(cfg)
        shot = manager.screenshots_dir / "old.png"
        shot.write_bytes(b"x")
        ancient = time.time() - 10 * 86400
        os.utime(shot, (ancient, ancient))
        removed = manager.cleanup()
        assert removed["screenshots"] >= 1
        assert not shot.exists()

    def test_cleanup_by_max_count(self, tmp_path):
        from universal_computer.config import PersistenceConfig

        cfg = PersistenceConfig(home=str(tmp_path / "home"), retention_days=30, max_screenshots=2)
        manager = PersistenceManager(cfg)
        for i in range(5):
            path = manager.screenshots_dir / f"s{i}.png"
            path.write_bytes(b"x")
            os.utime(path, (time.time() + i, time.time() + i))
        manager.cleanup()
        remaining = list(manager.screenshots_dir.iterdir())
        assert len(remaining) == 2


class TestActionHistory:
    def _history(self, tmp_path: Path) -> ActionHistory:
        return ActionHistory(PersistenceManager(_cfg(tmp_path)))

    def test_record_result(self, tmp_path):
        from universal_computer.models.action import ActionResult

        history = self._history(tmp_path)
        result = ActionResult(success=True, action="click", target="Continue", method="ocr")
        history.record(result)
        lines = (tmp_path / "home" / "logs" / "actions.jsonl").read_text().strip().splitlines()
        entry = json.loads(lines[-1])
        assert entry["action"] == "click"
        assert entry["target"] == "Continue"
        assert entry["success"] is True

    def test_redacts_secrets_in_targets(self, tmp_path):
        from universal_computer.models.action import ActionResult

        history = self._history(tmp_path)
        result = ActionResult(
            success=True, action="click", target="password=hunter2", method="ocr"
        )
        history.record(result)
        lines = (tmp_path / "home" / "logs" / "actions.jsonl").read_text().strip().splitlines()
        entry = json.loads(lines[-1])
        assert "hunter2" not in entry["target"]
        assert "[REDACTED]" in entry["target"]

    def test_recent_memory_mirror(self, tmp_path):
        history = self._history(tmp_path)
        for i in range(5):
            history.record_custom({"action": f"a{i}", "success": True})
        recent = history.recent(3)
        assert [e["action"] for e in recent] == ["a2", "a3", "a4"]


class TestRedaction:
    def test_password_pattern(self):
        assert "hunter2" not in redact_text("password=hunter2")

    def test_api_key_pattern(self):
        assert "sk-abcdef1234567890" not in redact_text("key sk-abcdef1234567890")

    def test_bearer_pattern(self):
        assert "Bearer abc123def456" not in redact_text("Bearer abc123def456")

    def test_github_token(self):
        assert "ghp_" not in redact_text("token ghp_" + "a" * 30)

    def test_mapping_redaction(self):
        clean = redact_mapping({"api_key": "xyz", "safe": 1, "nested": {"token": "abc"}})
        assert clean["api_key"] == "[REDACTED]"
        assert clean["safe"] == 1
        assert clean["nested"]["token"] == "[REDACTED]"

    def test_normal_text_untouched(self):
        assert redact_text("clicked Continue at 1110,725") == "clicked Continue at 1110,725"

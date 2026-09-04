"""Unit tests for configuration loading (§24)."""

from pathlib import Path

import pytest

from universal_computer.config import load_config
from universal_computer.core.errors import ConfigurationError


class TestDefaults:
    def test_defaults_valid(self):
        cfg = load_config()
        assert cfg.engine.observation_level == "normal"
        assert cfg.security.mode == "standard"
        assert cfg.persistence.home is None

    def test_every_section_present(self):
        cfg = load_config()
        for section in (
            "server", "engine", "input", "ocr", "vision",
            "security", "persistence", "logging", "backends",
        ):
            assert hasattr(cfg, section)


class TestYamlLoading:
    def test_load_from_file(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "engine:\n  observation_level: full\n  verify_actions: false\n"
            "security:\n  mode: permissive\n",
            encoding="utf-8",
        )
        cfg = load_config(config_file)
        assert cfg.engine.observation_level == "full"
        assert cfg.engine.verify_actions is False
        assert cfg.security.mode == "permissive"

    def test_missing_file_means_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nope.yaml")
        assert cfg.engine.observation_level == "normal"

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("engine: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_config(bad)

    def test_non_mapping_root_raises(self, tmp_path):
        bad = tmp_path / "list.yaml"
        bad.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_config(bad)


class TestEnvOverrides:
    def test_scalar_override(self, monkeypatch):
        monkeypatch.setenv("UCC_ENGINE__VERIFY_ACTIONS", "false")
        cfg = load_config()
        assert cfg.engine.verify_actions is False

    def test_int_override(self, monkeypatch):
        monkeypatch.setenv("UCC_SECURITY__MAX_ACTIONS_PER_MINUTE", "5")
        cfg = load_config()
        assert cfg.security.max_actions_per_minute == 5

    def test_list_override(self, monkeypatch):
        monkeypatch.setenv("UCC_SECURITY__ALLOWED_COMMANDS", '["ls", "wmctrl"]')
        cfg = load_config()
        assert cfg.security.allowed_commands == ["ls", "wmctrl"]

    def test_nested_section_override(self, monkeypatch):
        monkeypatch.setenv("UCC_VISION__VLM__ENABLED", "true")
        cfg = load_config()
        assert cfg.vision.vlm.enabled is True


class TestConfigPathResolution:
    def test_ucc_config_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("UCC_LOGGING__LEVEL", raising=False)
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("logging:\n  level: DEBUG\n", encoding="utf-8")
        monkeypatch.setenv("UCC_CONFIG", str(config_file))
        cfg = load_config()
        assert cfg.logging.level == "DEBUG"

    def test_local_config_yaml_autodetected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("UCC_LOGGING__LEVEL", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("logging:\n  level: WARNING\n", encoding="utf-8")
        cfg = load_config()
        assert cfg.logging.level == "WARNING"

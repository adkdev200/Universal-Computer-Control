"""Configuration loading: defaults <- YAML file <- environment overrides.

Environment overrides use the ``UCC_`` prefix with ``__`` as the nesting
delimiter, e.g. ``UCC_ENGINE__VERIFY_ACTIONS=false`` or
``UCC_SECURITY__ALLOWED_COMMANDS=["ls"]``. Values are parsed as YAML scalars so
booleans, numbers and JSON-style lists work naturally.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from universal_computer.core.errors import ConfigurationError


class ServerConfig(BaseModel):
    name: str = "Universal Computer Control MCP"
    transport: str = "stdio"


class EngineConfig(BaseModel):
    observation_level: str = "normal"            # minimal | normal | full
    observation_cache_ms: int = 1500
    duplicate_window_ms: int = 2000
    verify_actions: bool = True
    verification_mode: str = "basic"             # off | basic | strict
    action_timeout_s: float = 10.0
    retry_attempts: int = 2
    wait_poll_interval_s: float = 0.5
    type_interval_s: float = 0.02
    clipboard_restore: bool = True
    fuzzy_threshold: float = 0.55
    max_observations_cached: int = 10


class InputConfig(BaseModel):
    pause_s: float = 0.05
    failsafe: bool = True
    move_duration_s: float = 0.12
    drag_duration_s: float = 0.35


class OCRConfig(BaseModel):
    enabled: bool = True
    provider: str = "tesseract"                  # tesseract | easyocr
    language: str = "eng"
    min_confidence: float = 0.5
    tesseract_cmd: str | None = None


class VLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "VLM_API_KEY"
    timeout_s: float = 30.0


class VisionConfig(BaseModel):
    enabled: bool = True
    template_dir: str | None = None
    match_threshold: float = 0.8
    vlm: VLMConfig = Field(default_factory=VLMConfig)


class SecurityConfig(BaseModel):
    mode: str = "standard"                       # permissive | standard | strict
    allow_shell: bool = False
    allowed_commands: list[str] = Field(default_factory=list)
    allowed_applications: list[str] = Field(default_factory=list)
    max_actions_per_minute: int = 120
    command_timeout_s: float = 30.0
    confirmation_required: list[str] = Field(
        default_factory=lambda: ["close_window", "run_command"]
    )
    max_command_output_chars: int = 4000


class PersistenceConfig(BaseModel):
    home: str | None = None                   # default ~/.universal-computer
    save_screenshots: bool = True
    save_observations: bool = True
    retention_days: int = 14
    max_screenshots: int = 500
    max_observations: int = 500


class LoggingConfig(BaseModel):
    level: str = "INFO"
    dir: str | None = None                    # default <home>/logs
    file: str = "server.log"
    action_log: str = "actions.jsonl"
    console: bool = True


class BackendsConfig(BaseModel):
    priorities: dict[str, int] = Field(default_factory=dict)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    backends: BackendsConfig = Field(default_factory=BackendsConfig)


def default_config_path() -> Path | None:
    """Look for a config file in the standard locations."""
    env = os.environ.get("UCC_CONFIG")
    if env:
        candidate = Path(env).expanduser()
        return candidate if candidate.is_file() else None
    for candidate in (Path("config.yaml"), Path("config.yml")):
        if candidate.is_file():
            return candidate
    home = _persistence_home()
    for candidate in (home / "config" / "config.yaml", home / "config.yaml"):
        if candidate.is_file():
            return candidate
    return None


def _persistence_home() -> Path:
    env_home = os.environ.get("UCC_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".universal-computer"


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Merge ``UCC_SECTION__KEY`` environment variables into the config dict."""
    for raw_name, raw_value in os.environ.items():
        if not raw_name.startswith("UCC_") or len(raw_name) <= 4:
            continue
        parts = [part.lower() for part in raw_name[4:].split("__")]
        if any(not p for p in parts):
            continue
        try:
            value: Any = yaml.safe_load(raw_value)
        except yaml.YAMLError:
            value = raw_value
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ConfigurationError(
                    f"Environment override {raw_name} conflicts with a non-dict config node"
                )
        node[parts[-1]] = value
    return data


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load configuration from an optional YAML file plus environment overrides."""
    if path is None:
        path = default_config_path()
    data: dict[str, Any] = {}
    if path is not None:
        config_file = Path(path).expanduser()
        if config_file.is_file():
            try:
                loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ConfigurationError(f"Invalid YAML in {config_file}: {exc}") from exc
            if loaded is not None:
                if not isinstance(loaded, dict):
                    raise ConfigurationError(
                        f"Config root in {config_file} must be a mapping"
                    )
                data = loaded
    data = _apply_env_overrides(data)
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc

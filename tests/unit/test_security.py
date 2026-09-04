"""Unit tests for security policy, rate limiting and emergency stop (§23)."""

import pytest

from tests.conftest import build_engine
from universal_computer.config import SecurityConfig
from universal_computer.core.errors import (
    EmergencyStopError,
    RateLimitExceededError,
    SecurityViolationError,
)
from universal_computer.security.policy import SecurityPolicy


class TestPolicyCommands:
    def test_standard_mode_requires_allowlist(self):
        policy = SecurityPolicy(SecurityConfig(mode="standard", allowed_commands=[]))
        assert policy.check_command("rm -rf /").allowed is False

    def test_standard_mode_allowlist_match(self):
        policy = SecurityPolicy(
            SecurityConfig(mode="standard", allowed_commands=["ls", "git status"])
        )
        assert policy.check_command("ls -la").allowed is True
        assert policy.check_command("git status").allowed is True
        assert policy.check_command("git push --force").allowed is False

    def test_permissive_mode_allows_all(self):
        policy = SecurityPolicy(SecurityConfig(mode="permissive"))
        assert policy.check_command("anything --with flags").allowed is True

    def test_strict_mode_blocks_without_allowlist(self):
        policy = SecurityPolicy(SecurityConfig(mode="strict", allowed_commands=[]))
        assert policy.check_command("echo hi").allowed is False

    def test_strict_mode_allows_explicit_allowlist(self):
        policy = SecurityPolicy(
            SecurityConfig(mode="strict", allowed_commands=["xdotool"], allow_shell=False)
        )
        assert policy.check_command("xdotool key F5").allowed is True

    def test_empty_command_denied(self):
        policy = SecurityPolicy(SecurityConfig(mode="permissive"))
        assert policy.check_command("   ").allowed is False

    def test_windows_path_names_match(self):
        policy = SecurityPolicy(
            SecurityConfig(mode="standard", allowed_commands=["C:\\Tools\\tool.exe"])
        )
        # full-path patterns match the token directly
        assert policy.check_command("C:\\Tools\\tool.exe /quiet").allowed is True
        policy2 = SecurityPolicy(
            SecurityConfig(mode="standard", allowed_commands=["tool.exe"])
        )
        # basename matching also works for commands invoked with a full path
        assert policy2.check_command("C:\\Tools\\tool.exe /quiet").allowed is True


class TestPolicyApplications:
    def test_standard_empty_list_allows(self):
        policy = SecurityPolicy(SecurityConfig(mode="standard", allowed_applications=[]))
        assert policy.check_application("chrome").allowed is True

    def test_standard_with_allowlist(self):
        policy = SecurityPolicy(
            SecurityConfig(mode="standard", allowed_applications=["chrome", "code"])
        )
        assert policy.check_application("chrome").allowed is True
        assert policy.check_application("rm").allowed is False

    def test_strict_empty_list_denies(self):
        policy = SecurityPolicy(SecurityConfig(mode="strict", allowed_applications=[]))
        assert policy.check_application("chrome").allowed is False


class TestConfirmations:
    def test_requires_confirmation(self):
        policy = SecurityPolicy(
            SecurityConfig(confirmation_required=["run_command", "close_window"])
        )
        assert policy.requires_confirmation("run_command")
        assert not policy.requires_confirmation("click")

    def test_confirmation_hook(self):
        policy = SecurityPolicy(SecurityConfig(confirmation_required=["run_command"]))
        policy.set_confirmation_hook(lambda action: True)
        assert policy.check_confirmation("run_command", confirmed=False).allowed is True


class TestEngineSecurity:
    async def test_run_command_allowed_in_permissive(self, config, mocks):
        engine = build_engine(config, mocks)
        result = await engine.run_command("echo hello", confirm=True)
        assert result["success"] is True
        assert "hello" in result["stdout"]

    async def test_run_command_requires_confirmation(self, config, mocks):
        config.security.confirmation_required = ["run_command"]
        engine = build_engine(config, mocks)
        result = await engine.run_command("echo hello", confirm=False)
        assert result["success"] is False
        assert "confirm" in (result["error"] or "")

    async def test_run_command_denied_in_standard_mode(self, config, mocks):
        config.security.mode = "standard"
        config.security.allowed_commands = []
        engine = build_engine(config, mocks)
        with pytest.raises(SecurityViolationError):
            await engine.run_command("echo hello", confirm=True)

    async def test_launch_denied_in_standard_without_allowlist(self, config, mocks):
        config.security.mode = "standard"
        config.security.allowed_applications = ["chrome"]
        engine = build_engine(config, mocks)
        with pytest.raises(SecurityViolationError):
            await engine.launch_application("definitely-not-allowed")

    async def test_rate_limit(self, config, mocks):
        config.security.max_actions_per_minute = 2
        engine = build_engine(config, mocks)
        await engine.click({"x": 1, "y": 1})
        await engine.click({"x": 2, "y": 2})
        with pytest.raises(RateLimitExceededError):
            await engine.click({"x": 3, "y": 3})

    async def test_emergency_stop_blocks_and_resets(self, config, mocks):
        engine = build_engine(config, mocks)
        await engine.click({"x": 1, "y": 1})
        await engine.emergency_stop("test")
        with pytest.raises(EmergencyStopError):
            await engine.click({"x": 2, "y": 2})
        status = await engine.backend_status()
        assert status["emergency_stop"]["engaged"] is True
        await engine.reset_emergency_stop()
        result = await engine.click({"x": 3, "y": 3})
        assert result.success is True

    async def test_emergency_stop_flag_file(self, config, mocks):
        from pathlib import Path

        engine = build_engine(config, mocks)
        flag = Path(engine.stop.flag_path)
        flag.write_text("external stop", encoding="utf-8")
        engine.stop._last_file_check = 0.0  # force re-check
        with pytest.raises(EmergencyStopError):
            await engine.click({"x": 1, "y": 1})
        flag.unlink()

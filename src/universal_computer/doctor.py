"""``ucc-doctor``: diagnose (and where possible auto-fix) backend availability.

Run after installing the server to verify that every backend this machine can
support is actually registered, with exact copy-paste fix instructions for
the ones that are not::

    ucc-doctor                 # diagnose
    ucc-doctor --init-config   # also write a ready-to-use config.yaml

Exit code 0 when every *applicable* backend is healthy, 1 otherwise (useful
in CI / install scripts).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from universal_computer.logging import get_logger

logger = get_logger("doctor")

_APT_PACKAGES = "wmctrl xdotool xclip scrot tesseract-ocr at-spi2-core"
_PIP_EXTRAS = "universal-computer-control[input,linux,ocr,vision,screenshot,vlm]"


def _ok(label: str, detail: str = "") -> None:
    print(f"  [OK]   {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, fix: str) -> None:
    print(f"  [MISS] {label}")
    print(f"         fix: {fix}")


def check_environment() -> bool:
    print("\n== Environment ==")
    ok = True
    _ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")  # <3.11 cannot even import this package
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if display:
        _ok(f"DISPLAY={display}")
    elif wayland:
        _warn(f"WAYLAND_DISPLAY={wayland} without DISPLAY",
              "XWayland needed for screenshots/input; run the server inside your graphical session")
    else:
        _fail("no DISPLAY", "run the MCP server from a graphical session (X11 or XWayland)")
        ok = False
    return ok


def check_system_tools() -> None:
    print("\n== System tools (Linux X11) ==")
    for tool, pkg in [
        ("wmctrl", "wmctrl"),
        ("xdotool", "xdotool"),
        ("xclip", "xclip"),
        ("scrot", "scrot"),
        ("tesseract", "tesseract-ocr"),
    ]:
        path = shutil.which(tool)
        if path:
            _ok(tool, path)
        elif tool == "tesseract":
            _fail(tool, f"sudo apt install {pkg}  (OCR provider)")
        else:
            # Optional since the Xlib fallback covers the basics.
            _warn(tool, f"recommended: sudo apt install {pkg}")
    missing = [t for t in ("wmctrl", "xdotool", "xclip", "scrot", "tesseract") if not shutil.which(t)]
    if missing:
        print(f"\n  one-liner: sudo apt install {_APT_PACKAGES}")


def check_python_packages() -> bool:
    print("\n== Python packages ==")
    ok = True
    import importlib.util

    for module, extra in [
        ("mss", "screenshot"),
        ("pyautogui", "input"),
        ("pytesseract", "ocr"),
        ("cv2", "vision"),
        ("numpy", "vision"),
        ("httpx", "vlm"),
        ("psutil", "linux/windows"),
        ("Xlib", "input (python-xlib, auto-installed with pyautogui)"),
    ]:
        if importlib.util.find_spec(module) is not None:
            _ok(module)
        else:
            _fail(module, f"pip install '{_PIP_EXTRAS}'  (missing group: {extra})")
            ok = False
    return ok


def check_backends() -> bool:
    print("\n== Backends (live probe) ==")
    import importlib.util

    try:
        from universal_computer import load_config
        from universal_computer.core.bootstrap import build_default_engine
    except Exception as exc:  # noqa: BLE001
        _fail(f"importing universal_computer failed: {exc}", f"pip install -e '{_PIP_EXTRAS}'")
        return False
    try:
        engine = build_default_engine(load_config())
    except Exception as exc:  # noqa: BLE001
        _fail(f"engine bootstrap failed: {type(exc).__name__}: {exc}", "check config.yaml / UCC_ env overrides")
        return False

    report = engine.backend_manager.status_report()
    ok = True
    critical_caps = {"windows", "input", "screenshot"}
    for name, info in sorted(report.items()):
        caps = set(info["capabilities"])
        caps_str = ",".join(sorted(caps))
        if info["available"]:
            _ok(f"{name} ({caps_str})")
        else:
            error = (info.get("error") or "").strip()
            _fail(f"{name} ({caps_str})", error or "unavailable")
            if caps & critical_caps:
                ok = False
    vision = engine.vision.status()
    print("\n== Vision services ==")
    for service, info in vision.items():
        if info.get("available"):
            _ok(f"vision.{service}", str(info.get("provider")))
        elif service == "ocr":
            _fail("vision.ocr", "sudo apt install tesseract-ocr && pip install pytesseract")
            ok = False
        elif service == "templates":
            cfg_dir = info.get("template_dir")
            if not cfg_dir:
                _warn("vision.templates (OpenCV ready, no template_dir configured)",
                      "set vision.template_dir in config.yaml to enable computer.find_visual")
            else:
                _fail("vision.templates", "pip install opencv-python numpy")
                ok = False
        else:  # vlm
            enabled = info.get("enabled")
            if not enabled:
                _warn("vision.vlm disabled",
                      "set vision.vlm.enabled=true and vision.vlm.api_key_env in config.yaml, "
                      "then export that env variable")
            elif importlib.util.find_spec("httpx") is None:
                _fail("vision.vlm", "pip install httpx (extra: [vlm])")
            else:
                _fail("vision.vlm", "export the API key named in vision.vlm.api_key_env "
                                   "(default: VLM_API_KEY)")
    return ok


def init_config() -> None:
    """Write a ready-to-use config with every optional feature enabled."""
    home = Path(os.environ.get("UCC_HOME", Path.home() / ".universal-computer")).expanduser()
    target_dir = home / "config"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "config.yaml"
    template_dir = home / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        print(f"\nconfig already exists: {target} (left untouched)")
        return
    target.write_text(
        f"""# Universal Computer Control - generated by `ucc-doctor --init-config`
# Every value can be overridden with UCC_SECTION__KEY environment variables.

server:
  name: "Universal Computer Control MCP"
  transport: stdio

engine:
  observation_level: normal
  observation_cache_ms: 1500
  duplicate_window_ms: 2000
  verify_actions: true
  verification_mode: basic
  action_timeout_s: 10.0
  retry_attempts: 2

input:
  pause_s: 0.05
  failsafe: true
  move_duration_s: 0.12
  drag_duration_s: 0.35

ocr:
  enabled: true
  provider: tesseract
  language: eng
  min_confidence: 0.5

vision:
  enabled: true
  template_dir: {template_dir}
  match_threshold: 0.8
  vlm:
    enabled: true                    # requires the API key env var below
    provider: openai-compatible
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: VLM_API_KEY         # export VLM_API_KEY=sk-... before starting
    timeout_s: 30

security:
  mode: standard
  allow_shell: false
  allowed_commands: []
  allowed_applications: []
  max_actions_per_minute: 120
  command_timeout_s: 30
  confirmation_required:
    - close_window
    - run_command

persistence:
  save_screenshots: true
  save_observations: true
  retention_days: 14

logging:
  level: INFO
  file: server.log
  action_log: actions.jsonl
  console: true

backends:
  priorities: {{}}
""",
        encoding="utf-8",
    )
    print(f"\nwrote {target}")
    print("remember to: export VLM_API_KEY=...  (or disable vision.vlm in the config)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Universal Computer Control backends")
    parser.add_argument("--init-config", action="store_true", help="write a ready-to-use config.yaml")
    args = parser.parse_args()

    print("Universal Computer Control - doctor")
    if args.init_config:
        init_config()
    ok = check_environment()
    check_system_tools()
    if not check_python_packages():
        ok = False
    if not check_backends():
        ok = False
    print("\n" + ("ALL CHECKS PASSED" if ok else "PROBLEMS FOUND (see [MISS] rows above)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

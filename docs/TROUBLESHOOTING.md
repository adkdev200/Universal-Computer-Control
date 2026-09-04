# Troubleshooting Guide

Symptoms are listed per capability. Start with
`computer.backend_status` (or `python examples/standalone_usage.py`) — it
tells you exactly which backend is unavailable and why.

## The server starts but reports "no screenshot backend"

- **Linux**: `scrot` missing (PyAutoGUI capture path) → `sudo apt install
  scrot`. On Wayland-only sessions, screenshots via XWayland need
  `DISPLAY` set; use `pip install '.[screenshot]'` (mss) where possible.
- **Windows**: remote-desktop sessions minimized to the login screen have no
  active console — keep the session open.
- Both Pillow and mss unavailable → `pip install '.[screenshot]'` (mss is
  recommended; multi-monitor correct).

## PyAutoGUI unavailable / `KeyError: 'DISPLAY'` / Xlib errors

- The server was started from a shell without a graphical session. Launch it
  from a terminal inside the desktop session, or export `DISPLAY=:0`.
- Install the input extra: `pip install '.[input]'`.
- On headless servers there is nothing to control; the tools will answer
  with clear degraded errors instead of crashing (by design).

## Accessibility tree empty (Windows)

- pywinauto missing → `pip install '.[windows]'`.
- Some apps expose no UIA tree (games, canvas apps, Chromium with
  accessibility off). Chromium/Edge/Electron expose UIA only when a screen
  reader is detected or `--force-renderer-accessibility` is passed —
  otherwise the engine transparently falls back to OCR/vision. Nothing to
  fix server-side; check `computer.get_ui_tree` for the specific app.
- Elevated (admin) windows cannot be automated by a non-elevated server and
  vice versa — run both at the same integrity level.

## Accessibility tree empty (Linux)

- `pyatspi` missing → `sudo apt install python3-pyatspi at-spi2-core` (or
  the distro equivalent; the `gir1.2-atspi-2.0` introspection package).
- GNOME/GTK apps: `gsettings set org.gnome.desktop.interface
  toolkit-accessibility true`, then log out/in.
- Verify the bus: `busctl --user | grep at-spi` (or `listened` via
  `at-spi-bus-launcher`).
- KDE/Qt apps need the accessibility bridge (`qt-at-spi` /
  `QT_ACCESSIBILITY`), otherwise OCR is the fallback for them.

## Window management errors (Linux)

- `wmctrl`/`xdotool` missing → `sudo apt install wmctrl xdotool`.
- Pure Wayland (no `DISPLAY`): these tools cannot manage foreign windows;
  use a X11/XWayland session, or rely on `computer.launch_application` +
  accessibility/OCR. The backend disables itself with a warning instead of
  erroring on every call.

## OCR finds nothing or is inaccurate

- Tesseract binary missing → `sudo apt install tesseract-ocr` (Windows:
  winget/choco; set `ocr.tesseract_cmd` if not on `PATH`).
- Wrong language → set `ocr.language` (e.g. `deu+eng`; install
  `tesseract-ocr-deu`).
- Low contrast/small text → lower `ocr.min_confidence` (e.g. 0.3), or grab a
  region screenshot and check what the engine sees.
- Verify offline: `tesseract shot.png stdout` with a screenshot from
  `computer.screenshot`.

## Clicks land in the wrong place

- **DPI scaling (Windows)**: ensure the process runs per-monitor DPI aware
  (automatic; needs Windows 10 1703+). If an RDP client or a scaling utility
  interferes, set the monitor scale to 100% as a test and compare
  `computer.screenshot` size with `screen.width/height` in
  `computer.observe`.
- **Multi-monitor with mixed scale factors**: coordinates are virtual-desktop
  pixels; check `screen.monitors[].scale_factor` in an observation.
- **Stale element**: after UI changes, re-run `computer.observe` and re-click
  by element id/text instead of reusing old coordinates (the duplicate guard
  also protects you here).

## "Command not allowed" from `computer.run_command`

Default security is `standard` with an empty allowlist — shell execution is
**denied on purpose**. Either add commands:

```yaml
security:
  allowed_commands: ["ls", "git status", "xdotool", "wmctrl"]
```

or pass `confirm=true` in addition to the allowlist, or use
`security.mode: permissive` only on trusted machines. Full shell strings
additionally require `security.allow_shell: true`.

## Emergency stop engaged and will not clear

`computer.emergency_stop()` writes `<home>/state/EMERGENCY_STOP`. If an
external process created the flag, `computer.reset_emergency_stop()` (or
deleting the file while the server is stopped) clears it. While engaged,
every action returns an `EmergencyStopError` immediately.

## MCP client does not see the server

- Use an **absolute** path to the venv python in the client config.
- Never wrap the command in a shell pipeline; the client owns stdio.
- Logging goes to `<home>/logs/server.log` — stdout is reserved for the MCP
  protocol, so debug output can never corrupt the stream.
- Run `python examples/mcp_client_demo.py` to validate the server in
  isolation.

## Testing on CI / headless machines

The unit suite (`pytest tests/unit`) runs anywhere — OS-specific backends
are mocked. The stdio integration test (`pytest tests/integration`) boots
the real server and works headless: tools answer with honest degraded
payloads.

## Filing a good issue

Include: `computer.backend_status` output (redacted), the failing tool call,
the matching line(s) from `logs/actions.jsonl`, OS + session type
(X11/Wayland/RDP), and the observation JSON if the problem is about element
detection.

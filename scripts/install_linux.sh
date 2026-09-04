#!/usr/bin/env bash
# Universal Computer MCP - Linux installation script
# Run:  bash scripts/install_linux.sh
set -euo pipefail

echo "=== Universal Computer MCP - Linux installer ==="

# --- 1. Python ---------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.11+ (e.g. 'sudo apt install python3 python3-venv python3-pip')" >&2
    exit 1
fi
PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo "Python $PYV is too old; 3.11+ required." >&2
    exit 1
fi
echo "[ok] Python $PYV"

# --- 2. System packages -------------------------------------------------------
echo "[..] Checking system packages (X11 tools, OCR, accessibility)..."
MISSING=()
for pkg in wmctrl xdotool xclip scrot tesseract-ocr at-spi2-core libgles2; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then MISSING+=("$pkg"); fi
done
APT_HINT="sudo apt install -y ${MISSING[*]}"
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[warn] Missing system packages: ${MISSING[*]}"
    echo "       Install them with:  $APT_HINT"
    if [ -t 0 ]; then
        read -r -p "Install now with sudo? [y/N] " ANSWER || ANSWER=n
        if [ "${ANSWER:-n}" = "y" ]; then
            # shellcheck disable=SC2086
            sudo apt update && sudo apt install -y ${MISSING[*]}
        fi
    fi
else
    echo "[ok] System packages present"
fi

# --- 3. Virtual environment + packages ----------------------------------------
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip wheel
./.venv/bin/python -m pip install -e ".[input,linux,ocr,vision,screenshot,vlm,dev]"
echo "[ok] Python dependencies installed"

# --- 4. Session / display checks ----------------------------------------------
if [ -z "${DISPLAY:-}" ]; then
    echo "[warn] DISPLAY is not set: PyAutoGUI/screenshots will not work in this shell." >&2
    echo "       Run the server from a graphical session (X11 or XWayland)."
fi
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
    echo "[warn] Pure Wayland session: window management via wmctrl/xdotool is unavailable."
    echo "       Accessibility (AT-SPI) still works for GNOME/GTK apps; see docs/TROUBLESHOOTING.md."
fi
if command -v gsettings >/dev/null 2>&1; then
    if [ "$(gsettings get org.gnome.desktop.interface toolkit-accessibility 2>/dev/null)" = "false" ]; then
        echo "[warn] GNOME accessibility is disabled; enable with:"
        echo "       gsettings set org.gnome.desktop.interface toolkit-accessibility true"
    fi
fi

# --- 5. Smoke test --------------------------------------------------------------
./.venv/bin/python -c "import universal_computer; print('[ok] universal_computer', universal_computer.__version__, 'importable')"

echo
echo "=== Done ==="
echo "Run the MCP server:        ./.venv/bin/universal-computer-control  (alias: ucc)"
echo "Try the standalone engine: ./.venv/bin/python examples/standalone_usage.py"
echo "Try the MCP client demo:   ./.venv/bin/python examples/mcp_client_demo.py"
echo "Run the tests:             ./.venv/bin/python -m pytest tests"
echo
echo "Register with an MCP client using examples/mcp-config.example.json"
echo "(adjust the command path to your .venv/bin/python)"

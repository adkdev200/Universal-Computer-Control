# Universal Computer MCP - Windows 10/11 installation script (PowerShell)
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== Universal Computer MCP - Windows installer ===" -ForegroundColor Cyan

# --- 1. Python --------------------------------------------------------------
try {
    $pyVersion = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
} catch { $pyVersion = $null }

if (-not $pyVersion) {
    Write-Host "Python not found. Install Python 3.11+ from https://python.org" -ForegroundColor Yellow
    Write-Host "Recommended: winget install -e --id Python.Python.3.12"
    Write-Host "IMPORTANT: check 'Add python.exe to PATH' during installation."
    exit 1
}
$major, $minor = $pyVersion.Split(".")
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 11)) {
    Write-Host "Python $pyVersion is too old; 3.11+ required." -ForegroundColor Red
    exit 1
}
Write-Host "[ok] Python $pyVersion"

# --- 2. Virtual environment + packages --------------------------------------
python -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Host "venv creation failed" -ForegroundColor Red; exit 1 }
.\.venv\Scripts\python.exe -m pip install --upgrade pip wheel

# Core + Windows + input + OCR + vision + screenshot + VLM support
.\.venv\Scripts\python.exe -m pip install -e ".[input,windows,ocr,vision,screenshot,vlm,dev]"
if ($LASTEXITCODE -ne 0) { Write-Host "dependency install failed" -ForegroundColor Red; exit 1 }
Write-Host "[ok] Python dependencies installed"

# --- 3. Tesseract OCR (optional but recommended) ----------------------------
$tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tesseract) {
    Write-Host "[warn] Tesseract OCR not found. Install one of:" -ForegroundColor Yellow
    Write-Host "       winget install -e --id UB-Mannheim.TesseractOCR"
    Write-Host "       choco install tesseract"
    Write-Host "       Then set ocr.tesseract_cmd in config.yaml if not on PATH."
} else {
    Write-Host "[ok] Tesseract OCR found"
}

# --- 4. Accessibility notes --------------------------------------------------
Write-Host "[info] Windows UI Automation needs no extra permissions."
Write-Host "[info] If the server runs elevated, apps launched non-elevated may not be automatable."
Write-Host "[info] For DPI correctness Windows 10 1703+ is assumed (per-monitor v2 awareness is set best-effort)."

# --- 5. Smoke test ------------------------------------------------------------
.\.venv\Scripts\python.exe -c "import universal_computer; print('[ok] universal_computer', universal_computer.__version__, 'importable')"

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Run the MCP server:            .\.venv\Scripts\universal-computer-control  (alias: ucc)"
Write-Host "Try the standalone engine:     .\.venv\Scripts\python examples\standalone_usage.py"
Write-Host "Try the MCP client demo:       .\.venv\Scripts\python examples\mcp_client_demo.py"
Write-Host "Run the tests:                 .\.venv\Scripts\python -m pytest tests"
Write-Host ""
Write-Host "Register with an MCP client using examples\mcp-config.example.windows.json"
Write-Host "(adjust the command path to your .venv\Scripts\python.exe)"

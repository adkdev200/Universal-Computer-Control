# Architecture

This document maps the design to the code. The guiding rule (design §32):

> **MCP is not the computer-control engine.** MCP is only the interface
> between the AI agent and the engine. The engine
> (`universal_computer.core.engine.ComputerControlEngine`) is a plain async
> Python library usable without any server.

## Module map

```
src/universal_computer/
├── server.py                 MCP entrypoint (stdio), app factory
├── __main__.py               python -m universal_computer
├── config.py                 AppConfig: defaults <- YAML <- UCC_* env
├── logging.py                structured JSON logs + credential redaction
│
├── models/                   pydantic schemas shared by everything
│   ├── element.py            BoundingBox ([l,t,r,b]), UIElement, roles
│   ├── observation.py        Observation, OCRResult, ScreenInfo, levels
│   ├── action.py             ResolvedTarget, ActionResult, AttemptRecord,
│   │                         VerificationResult, ActionType
│   └── window.py             WindowInfo, WindowState
│
├── core/                     the engine (no MCP imports)
│   ├── engine.py             ComputerControlEngine facade
│   ├── actions.py            mouse/keyboard mixin (click/type/press/…)
│   ├── observer.py           builds Observations; ElementRegistry
│   ├── resolver.py           target → coordinates (§5 hierarchy)
│   ├── verifier.py           before/after signatures (dhash, OCR, window)
│   ├── recovery.py           ActionExecutor: attempts, timeouts, duplicate
│   │                         suppression, strict verification
│   ├── backend_manager.py    capability registry, priority selection,
│   │                         failure cooldowns, health reports
│   ├── bootstrap.py          factory: registers whatever works here
│   ├── matching.py           dependency-free fuzzy text matching
│   ├── coordinates.py        coordinate transformer abstraction (DPI)
│   └── errors.py             typed exception hierarchy
│
├── backends/                 platform adapters behind ABCs (§9, §10, §11)
│   ├── base.py               Backend ABC + capability contracts
│   ├── pyautogui_backend.py  input + cursor + secondary screenshots
│   ├── screenshot.py         mss + Pillow backends, monitor enumeration,
│   │                         DPI awareness (Windows), xrandr (Linux)
│   ├── windows_uia.py        pywinauto UIA: trees, elements, Invoke
│   ├── windows_manager.py    pygetwindow window lifecycle
│   ├── linux_atspi.py        pyatspi: trees, roles, states, actions
│   ├── linux_window.py       wmctrl/xdotool window lifecycle
│   ├── clipboard.py          pyperclip / PowerShell / xclip-xsel
│   └── applications.py       detached app launching (startfile/xdg-open)
│
├── vision/                   pluggable providers (§12-§14)
│   ├── base.py               OCRProvider, VisionProvider,
│   │                         VisionLanguageModel, VisionServices
│   ├── ocr.py                Tesseract (+ EasyOCR), line grouping
│   ├── opencv.py             multi-scale template matching
│   └── vlm.py                OpenAI-compatible locate_element
│
├── persistence/
│   ├── state.py              ~/.universal-computer layout, retention
│   └── history.py            actions.jsonl + in-memory mirror
│
├── security/
│   ├── policy.py             modes, allowlists, rate limiter, confirmations
│   └── emergency_stop.py     event + stop-file kill switch
│
└── mcp/                      thin MCP wrapper only (§31)
    ├── compat.py             mcp 1.x FastMCP / 2.x MCPServer shim
    ├── tools_observation.py  observe/screenshot/tree/find/wait_for
    ├── tools_input.py        click/type/press/hotkey/scroll/…
    ├── tools_system.py       windows/clipboard/launch/run/admin
    └── tools.py              registration aggregator + TOOL_NAMES
```

## Request flow: `computer.click("Continue")`

1. **MCP tool handler** (`mcp/tools_input.py`) validates args and calls
   `engine.click("Continue")` — all handlers convert exceptions to
   `{"ok": false, "error": {...}}` payloads.
2. **Emergency stop + rate limit** are checked (`recovery.ActionExecutor`).
3. **Resolution** (`core/resolver.py`), in order, stopping at the first hit:
   explicit coordinates → `element_N` from the registry → accessibility
   elements of the fresh observation (fuzzy match) → fresh accessibility
   search → OCR lines (fuzzy match) → image templates → optional VLM.
   The result carries lower-confidence *alternatives* for later fallback.
4. **Attempt planning** (`core/actions.py`):
   - if the matched element supports a semantic action →
     `Attempt(method="semantic-invoke")` first;
   - then coordinate clicks from element bbox, resolver hit, alternatives
     (near-duplicate coordinates deduplicated);
   - each attempt resolves the input backend *at execution time* so a
     backend that died mid-run is skipped.
5. **Execution** (`core/recovery.py`): attempts run sequentially in worker
   threads under a timeout; failures feed `BackendManager` health tracking
   (3 consecutive failures ⇒ 30 s cooldown) and the next attempt runs.
   The duplicate guard suppresses an identical action repeated within
   `duplicate_window_ms` unless the screen provably changed since.
6. **Verification**: a forced fresh observation; the verifier compares
   screenshots (dhash), OCR text sets, active window and focused element.
   `basic` mode records the outcome; `strict` mode treats "no observable
   change" as failure and reports state instead of blindly re-clicking.
7. **Bookkeeping**: the `ActionResult` (attempts, verification, confidence)
   goes to the agent and to `logs/actions.jsonl`.

## Interaction hierarchy vs implementation

| Design layer | Implemented by |
|---|---|
| 1. Accessibility / UI Automation | `WindowsUIABackend`, `LinuxATSPIBackend` (semantic invoke) |
| 2. Semantic UI element | `UIElement.can_invoke_semantically` + resolver element match |
| 3. OCR text detection | `TesseractOCRProvider` / `EasyOCRProvider` + fuzzy matcher |
| 4. Image/template matching | `OpenCVTemplateMatcher` |
| 5. Vision model | `OpenAICompatibleVLM` (any OpenAI-compatible endpoint) |
| 6. Coordinates + PyAutoGUI | `PyAutoGUIBackend` (final universal fallback) |

## Coordinate system

The canonical space is **virtual-desktop pixels**: the union of all monitors
(may include negative coordinates). Screenshots from `mss`/Pillow are taken
and interpreted in this space; `computer.click({"x": ...})` targets it. On
Windows the process enables per-monitor-v2 DPI awareness so screenshot
pixels equal OS pixels; `core/coordinates.py` provides the transformation
abstraction for sources that report logical coordinates.

## Degradation matrix

| Missing | Effect |
|---|---|
| PyAutoGUI | no physical input: observation/read-only tools still work |
| mss / Pillow screenshots | OCR + vision disabled; minimal observe works |
| UIA / AT-SPI | semantic elements + invoke disabled; OCR path continues |
| tesseract / EasyOCR | OCR disabled; templates/accessibility continue |
| OpenCV | template matching disabled; OCR/VLM continue |
| VLM (httpx/key) | description-based locate disabled; everything else works |
| wmctrl/xdotool | window management reports a clear error; input works |
| clipboard tools | clipboard typing strategy skipped; direct typing works |

Every combination leaves the MCP server running and answering.

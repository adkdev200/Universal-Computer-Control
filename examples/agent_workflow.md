# Agent Workflow Example: "Open Chrome, search for Python documentation, click the first result"

This scenario (design §33) runs **entirely through the Universal Computer
Control MCP** — no Chrome MCP, no Playwright, no browser extension. The agent
combines the generic `computer.*` tools in a look → understand → act → verify
loop.

```text
Agent                                   Universal Computer MCP
-----                                   ----------------------
1. computer.list_windows
        ---------- discover/focus Chrome ------------------------------>|
2. computer.focus_window "Chrome"              OS window activated
3. computer.observe "normal"                   screenshot + OCR + a11y
        <-- elements: [address bar?], ocr: [...]
4. computer.click "element_3"                  semantic-invoke (UIA/AT-SPI)
   | fallback: OCR match "Search or type" -> coordinates -> PyAutoGUI click
5. computer.type "python documentation"        keyboard; clipboard fallback
6. computer.press "enter"
7. computer.wait_for "any_change" timeout 10   poll OCR/screenshot hash
8. computer.observe "normal"                   results page snapshot
9. computer.find_text "Python documentation"   fuzzy OCR search
        <-- matches with bboxes
10. computer.click element at match bbox       (or semantic invoke)
11. computer.wait_for "any_change"             verify navigation happened
12. computer.observe "minimal"                 confirm new page loaded
```

Key properties demonstrated:

- **Interaction hierarchy** (steps 4, 10): accessibility invoke is preferred;
  if unavailable the engine automatically falls back OCR → template → vision
  → coordinates without the agent knowing or caring.
- **Verification** (steps 7, 11): `wait_for` + observe confirm that the action
  had an effect instead of blindly repeating.
- **Window handling** (steps 1-2): focusing the real OS window means popups,
  new windows and tab changes are just new windows to discover.
- **Browser independence**: every step works identically for Firefox, Edge,
  VS Code, LibreOffice, a file manager, or a terminal.

The same loop covers design §34 (open VS Code, type, save with `ctrl+s`) and
§35 (open the file manager, navigate, open a PDF) with no changes to the
server.

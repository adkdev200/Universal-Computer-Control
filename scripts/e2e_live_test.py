#!/usr/bin/env python3
"""End-to-end live verification of the Universal Computer Control engine.

Manages its own virtual display (Xvfb + openbox), creates real X11 test
windows, and exercises EVERY backend that was previously unavailable:
screenshot, window management, OCR, OpenCV templates, VLM (mock server),
clipboard, mouse/keyboard input, launch, run_command, emergency stop.

Modes:
  python scripts/ucc_e2e_test.py             # full stack (wmctrl/xdotool/xclip on PATH)
  python scripts/ucc_e2e_test.py --no-tools  # zero X11 tools on PATH: verifies the
                                             # python-Xlib fallback window management

Exit 0 = everything works.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path("/home/z/my-project/Universal-Computer-Control")
PREFIX = Path("/home/z/local")
TEST_HOME = Path("/tmp/ucc-e2e-home")
NO_TOOLS = "--no-tools" in sys.argv

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, details: str = "") -> bool:
    RESULTS.append((name, bool(ok), details))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {details}" if details else ""), flush=True)
    return bool(ok)


# ---------------------------------------------------------------------------
# 1. Display stack lifecycle
# ---------------------------------------------------------------------------
_XVFB: subprocess.Popen | None = None
_WM: subprocess.Popen | None = None


def start_display() -> None:
    global _XVFB, _WM
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{PREFIX}/usr/lib/x86_64-linux-gnu:{PREFIX}/usr/lib"
    env["DISPLAY"] = ":98"
    env["XAUTHORITY"] = "/home/z/.Xauthority"
    _XVFB = subprocess.Popen(
        ["/usr/bin/Xvfb", ":98", "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):
        if Path("/tmp/.X11-unix/X98").exists():
            break
        time.sleep(0.1)
    openbox = PREFIX / "usr/bin/openbox"
    rc = Path("/home/z/.config/openbox/rc.xml")
    if openbox.exists():
        _WM = subprocess.Popen(
            [str(openbox), *(["--config-file", str(rc)] if rc.exists() else [])],
            env=env, stdout=open("/tmp/ucc_e2e_openbox.log", "w"), stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(1.0)
        if _WM.poll() is not None:
            raise RuntimeError("openbox died at startup: see /tmp/ucc_e2e_openbox.log")


def stop_display() -> None:
    for proc in (_WM, _XVFB):
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()


atexit.register(stop_display)


# ---------------------------------------------------------------------------
# 2. Mock OpenAI-compatible VLM server
# ---------------------------------------------------------------------------
class MockVLMHandler(BaseHTTPRequestHandler):
    bbox = [110, 120, 260, 160]

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _body = self.rfile.read(length)
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_response(401)
            self.end_headers()
            return
        content = json.dumps({"found": True, "bbox_2d": self.bbox, "confidence": 0.93})
        reply = json.dumps({
            "id": "mock", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_args):
        pass


def start_mock_vlm() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), MockVLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# 3. Test X11 windows with OCR-friendly labels
# ---------------------------------------------------------------------------
_DISP = None  # keep the X connection alive: when a client disconnects the
              # X server destroys ALL of its windows, so this must outlive
              # the whole test run (GC of the Display object closes it).


_DISP = None  # keep the X connection alive: when a client disconnects the
              # X server destroys ALL of its windows, so this must outlive
              # the whole test run (GC of the Display object closes it).
_WINDOWS: list = []  # keep python-xlib window objects referenced


def paint_test_windows() -> None:
    """Repaint all test windows (top window last so its content is visible)."""
    from Xlib import X

    if not _DISP:
        return
    disp = _DISP
    for win, _title, raw in _WINDOWS:
        gc = win.create_gc()
        band_rows = 20
        bytes_per_row = 640 * 4
        for band_start in range(0, 240, band_rows):
            rows = min(band_rows, 240 - band_start)
            win.put_image(gc, 0, band_start, 640, rows, X.ZPixmap, 24, 0,
                          raw[band_start * bytes_per_row:(band_start + rows) * bytes_per_row])
        disp.flush()


def create_test_windows() -> list[int]:
    """Create two real X11 windows with large PIL-rendered labels."""
    global _DISP
    from PIL import Image, ImageDraw, ImageFont
    from Xlib import X, Xatom, display as xdisplay

    disp = _DISP = xdisplay.Display(os.environ["DISPLAY"])
    screen = disp.screen()
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)

    def render(label, color):
        img = Image.new("RGB", (640, 240), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([40, 150, 320, 210], fill=color)
        draw.text((60, 40), label, fill=(20, 20, 20), font=font)
        return img.tobytes("raw", "BGRX")  # depth-24 ZPixmap = 4 bytes/pixel

    specs = [
        ("UCC Test Alpha", (30, 90, 160), "ALPHA BUTTON"),
        ("UCC Test Beta", (140, 40, 60), "BETA SEARCH BOX"),
    ]
    window_ids = []
    for title, color, label in specs:
        win = screen.root.create_window(
            140, 140, 640, 240, 2, screen.root_depth,
            window_class=X.InputOutput,
            background_pixel=screen.white_pixel,
            event_mask=X.ExposureMask | X.StructureNotifyMask,
        )
        win.set_wm_name(title)
        win.change_property(disp.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        win.map()
        disp.flush()
        window_ids.append(win.id)
        _WINDOWS.append((win, title, render(label, color)))
        time.sleep(0.4)

    # Focus Alpha so _NET_ACTIVE_WINDOW is set; wait for openbox to finish
    # ALL stacking/restacking before painting, or exposes wipe the content.
    try:
        from Xlib import protocol as xprotocol
        ev = xprotocol.event.ClientMessage(
            window=window_ids[0],
            client_type=disp.intern_atom("_NET_ACTIVE_WINDOW"),
            data=(32, [1, 0, 0, 0, 0]),
        )
        screen.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        disp.flush()
    except Exception:
        pass
    time.sleep(1.0)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        while disp.pending_events():
            disp.next_event()
        time.sleep(0.05)

    paint_test_windows()
    time.sleep(0.3)

    # Determinism guards.
    xdot = shutil.which("xdotool")
    if xdot:
        r = subprocess.run([xdot, "search", "--name", "UCC"], capture_output=True, text=True)
        found = len(r.stdout.split())
        if found < 2:
            raise RuntimeError(
                f"test windows not alive after creation (found {found}); "
                f"openbox log: {Path('/tmp/ucc_e2e_openbox.log').read_text()[:300]}"
            )
    if _WM and _WM.poll() is not None:
        raise RuntimeError("openbox died during window creation")
    return window_ids


# ---------------------------------------------------------------------------
# 4. Test suites
# ---------------------------------------------------------------------------
async def assert_core_backends(engine) -> None:
    status = await engine.backend_status()
    backends = status["backends"]
    print("\n--- backend status ---")
    print(json.dumps({**backends, **status.get("vision", {})}, indent=2, default=str)[:2000])
    for name in ("pyautogui", "mss", "pillow", "linux-window"):
        info = backends.get(name)
        if info:
            check(f"backend available: {name}", info.get("available") is True,
                  str(info.get("error", ""))[:140])
    # vision status
    vision = status.get("vision", {})
    check("vision.ocr available", vision.get("ocr", {}).get("available") is True)
    return backends


async def run_full_suite() -> None:
    print("=" * 60)
    print("FULL STACK SUITE (wmctrl/xdotool/xclip present)")
    print("=" * 60)
    os.environ["DISPLAY"] = ":98"
    os.environ["XAUTHORITY"] = "/home/z/.Xauthority"
    os.environ["UCC_HOME"] = str(TEST_HOME)
    os.environ["VLM_API_KEY"] = "mock-key-for-local-testing"
    os.environ.pop("UCC_ENGINE__VERIFICATION_MODE", None)

    from universal_computer import ComputerControlEngine, load_config

    create_test_windows()

    template_dir = TEST_HOME / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(None)
    cfg.vision.vlm.enabled = True
    cfg.vision.vlm.base_url = f"http://127.0.0.1:{MOCK_VLM_PORT}/v1"
    cfg.vision.vlm.api_key_env = "VLM_API_KEY"
    cfg.vision.vlm.model = "mock-vision-model"
    cfg.vision.template_dir = str(template_dir)
    cfg.security.mode = "permissive"
    cfg.persistence.home = str(TEST_HOME)

    def _dump_state(tag: str) -> None:
        xdot = shutil.which("xdotool")
        r = subprocess.run([xdot, "search", "--name", "UCC"], capture_output=True, text=True) if xdot else None
        wm_alive = _WM.poll() is None if _WM else False
        # read _NET_CLIENT_LIST from a fresh connection inside a worker thread
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            def _read():
                import Xlib.threaded  # noqa
                from Xlib import display as xd
                d = xd.Display()
                p = d.screen().root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), 0)
                return [int(v) for v in p.value] if p and p.value else None
            val = ex.submit(_read).result()
        print(f"[state:{tag}] windows={r.stdout.split() if r else '?'} openbox_alive={wm_alive} worker_CLIENT_LIST={val}")

    _dump_state("pre-engine")
    engine = ComputerControlEngine(cfg)
    _dump_state("post-engine")

    # DEBUG: probe the linux-window backend strategies directly
    _lw = engine.backend_manager.get("linux-window")
    if _lw:
        _wm = _lw._run([_lw._wmctrl, "-l", "-p"]) if _lw._wmctrl else None
        print(f"[debug] wmctrl={_lw._wmctrl} rc={_wm.returncode if _wm else 'n/a'} out={(_wm.stdout if _wm else '')[:200]!r}")
        try:
            _xw = _lw._list_windows_xlib()
            print(f"[debug] xlib listing: {[(w.title, w.handle) for w in _xw]}")
        except Exception as _exc:
            print(f"[debug] xlib listing raised: {type(_exc).__name__}: {_exc}")

        # Trace every subsequent list_windows call (the engine calls via worker threads)
        _orig_lw = _lw.list_windows

        def _traced_lw():
            try:
                r = _orig_lw()
                print(f"[trace] list_windows -> {len(r)} windows: {[w.title for w in r][:4]}")
                return r
            except Exception as e:
                print(f"[trace] list_windows RAISED {type(e).__name__}: {e}")
                raise

        _lw.list_windows = _traced_lw
        _orig_ah = _lw._active_hex

        def _traced_ah():
            r = _orig_ah()
            print(f"[trace] _active_hex -> {r}")
            return r

        _lw._active_hex = _traced_ah

        _orig_top = _lw._xlib_top_levels

        def _traced_top():
            try:
                disp = _lw._xlib()
                root = _lw._xlib_root(disp)
                prop = root.get_full_property(_lw._xlib_atom(disp, "_NET_CLIENT_LIST"), 0)
                print(f"[trace-deep] _NET_CLIENT_LIST prop: {[hex(int(v)) for v in prop.value] if prop and prop.value else prop}")
                r = _orig_top()
                print(f"[trace-deep] _xlib_top_levels -> {len(r)} entries")
                return r
            except Exception as e:
                print(f"[trace-deep] _xlib_top_levels RAISED {type(e).__name__}: {e}")
                raise

        _lw._xlib_top_levels = _traced_top

    await assert_core_backends(engine)

    # screenshot
    shot = await engine.screenshot()
    check("screenshot backend", shot.get("success") is True and shot.get("path"),
          f"size={shot.get('size')}")

    # observation + OCR
    obs = await engine.observe("full")
    check("observe(): screen geometry", bool(obs.screen and obs.screen.width > 0),
          f"{obs.screen.width if obs.screen else 0}x{obs.screen.height if obs.screen else 0}")
    ocr_text = " | ".join(r.text for r in obs.ocr)
    # Only the top window's label is visible (Beta sits behind Alpha).
    check("observe(): OCR reads window labels", "ALPHA" in ocr_text.upper(), ocr_text[:140])
    check("observe(): active window detected",
          bool(obs.active_window and obs.active_window.title),
          obs.active_window.title if obs.active_window else "none")

    # find_text
    ft = await engine.find_text("alpha button")
    check("find_text('alpha button')", ft.get("count", 0) >= 1, str(ft.get("matches", []))[:120])

    # window management
    wins = await engine.list_windows()
    titles = [w.get("title", "") for w in wins.get("windows", [])]
    check("list_windows() finds test windows",
          any("UCC Test Alpha" in t for t in titles), f"{titles[:6]}")
    active = await engine.get_active_window()
    check("get_active_window()", active.get("window") is not None, str(active)[:100])

    r = await engine.focus_window("UCC Test Beta")
    check("focus_window()", r.get("success") is True, str(r.get("error", ""))[:80])
    r = await engine.maximize_window("UCC Test Beta")
    check("maximize_window()", r.get("success") is True, str(r.get("error", ""))[:80])
    await asyncio.sleep(0.5)
    r = await engine.restore_window("UCC Test Beta")
    check("restore_window()", r.get("success") is True, str(r.get("error", ""))[:80])
    r = await engine.minimize_window("UCC Test Beta")
    check("minimize_window()", r.get("success") is True, str(r.get("error", ""))[:80])
    await asyncio.sleep(0.7)
    wins2 = await engine.list_windows()
    beta = next((w for w in wins2["windows"] if "Beta" in w.get("title", "")), {})
    check("minimize reflected in WM state", beta.get("state") == "minimized",
          f"state={beta.get('state')}")
    r = await engine.restore_window("UCC Test Beta")
    check("restore_window() after minimize", r.get("success") is True)

    # clipboard
    await engine.set_clipboard("ucc-e2e-clipboard-123")
    r2 = await engine.get_clipboard()
    check("clipboard round-trip", "ucc-e2e-clipboard-123" in str(r2.get("text", "")), str(r2)[:80])

    # mouse input
    r = await engine.move_mouse({"x": 500, "y": 400})
    check("move_mouse()", getattr(r, "success", r.get("success") if isinstance(r, dict) else None) is True, str(r)[:80])
    r = await engine.click({"x": 480, "y": 180})
    click_ok = getattr(r, "success", None)
    if click_ok is None and isinstance(r, dict):
        click_ok = r.get("success")
    check("click(coordinate)", click_ok is True, str(r)[:100])

    # template matching via OpenCV: crop OCR-located label, then find it again
    await asyncio.sleep(1.8)  # let the observation cache expire (click verify cached a mid-change frame)
    paint_test_windows()   # the minimize/maximize dance wiped the painted content (expose)
    time.sleep(0.4)
    shot_path = obs.screenshot_path
    if shot_path:
        from PIL import Image as PILImage
        from universal_computer.core.matching import rank_matches  # noqa: F401
        ft2 = await engine.find_text("alpha")
        matches = ft2.get("matches", [])
        bbox_match = next((m for m in matches if m.get("bbox")), None)
        if bbox_match:
            x1, y1, x2, y2 = bbox_match["bbox"]
            full = PILImage.open(shot_path).convert("RGB")
            pad = 6
            patch = full.crop((max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad))
            patch.save(template_dir / "alpha_button.png")
            fv = await engine.find_visual("alpha_button")
            res = fv.get("result", {})
            check("find_visual(): OpenCV template match", res.get("found") is True,
                  f"conf={res.get('confidence')} details={str(res.get('details'))[:80]}")
        else:
            check("find_visual(): OpenCV template match", False,
                  f"no OCR bbox to crop; matches={str(ft2.get('matches', []))[:200]}")
    else:
        check("find_visual(): OpenCV template match", False, "no screenshot path in observation")

    # VLM against the mock OpenAI-compatible server
    from PIL import Image as PILImage
    vlm_img = PILImage.open(shot_path) if shot_path else None
    vr = await engine.vision.locate_with_vlm(vlm_img, "the ALPHA BUTTON label")
    check("VLM locate (mock OpenAI endpoint)",
          vr.found and vr.bbox is not None and vr.confidence > 0.5,
          f"bbox={vr.bbox} conf={vr.confidence}")

    # launch + run_command
    r = await engine.launch_application("true")
    check("launch_application()", r.get("success") is True, str(r)[:80])
    r = await engine.run_command("echo ucc-run-command-ok", confirm=True)
    check("run_command()", "ucc-run-command-ok" in str(r.get("stdout", "")), str(r)[:100])

    # emergency stop cycle
    r = await engine.emergency_stop("e2e test")
    check("emergency_stop()", r.get("success") is True or r.get("engaged") is True, str(r)[:80])
    r = await engine.reset_emergency_stop()
    check("reset_emergency_stop()", r.get("success") is True or r.get("engaged") is False, str(r)[:80])


async def run_xlib_fallback_suite() -> None:
    print("=" * 60)
    print("XLIB FALLBACK SUITE (no wmctrl/xdotool/xclip on PATH)")
    print("=" * 60)
    os.environ["DISPLAY"] = ":98"
    os.environ["XAUTHORITY"] = "/home/z/.Xauthority"
    os.environ["UCC_HOME"] = str(TEST_HOME)

    # Scrub the local tools from the harness process environment so the
    # backend cannot find them; forces the python-Xlib fallback paths.
    purged = [p for p in os.environ.get("PATH", "").split(os.pathsep) if "local/usr/bin" not in p]
    os.environ["PATH"] = os.pathsep.join(purged)
    for tool in ("wmctrl", "xdotool", "xclip", "xsel"):
        assert not shutil.which(tool), f"{tool} still on PATH"
    print("tools scrubbed from PATH: ok")

    from universal_computer import ComputerControlEngine, load_config

    create_test_windows()

    cfg = load_config(None)
    cfg.persistence.home = str(TEST_HOME)
    engine = ComputerControlEngine(cfg)

    status = await engine.backend_status()
    lw = status["backends"].get("linux-window", {})
    check("fallback: linux-window available with ZERO tools", lw.get("available") is True,
          str(lw.get("error", ""))[:140])

    wins = await engine.list_windows()
    titles = [w.get("title", "") for w in wins.get("windows", [])]
    check("fallback: list_windows() via Xlib", any("UCC Test Alpha" in t for t in titles),
          f"{titles[:6]}")
    alpha = next((w for w in wins["windows"] if "Alpha" in w.get("title", "")), None)
    check("fallback: titles read", bool(alpha and alpha.get("title")), str(alpha)[:100])
    check("fallback: bbox populated", bool(alpha and alpha.get("bbox")), str(alpha)[:120])
    check("fallback: process id detected", bool(alpha and alpha.get("process_id")), str(alpha)[:120])

    r = await engine.focus_window("UCC Test Beta")
    check("fallback: focus_window() via EWMH", r.get("success") is True, str(r.get("error", ""))[:80])
    await asyncio.sleep(0.5)
    active = await engine.get_active_window()
    check("fallback: get_active_window()",
          bool(active.get("window") and "Beta" in active["window"].get("title", "")),
          str(active.get("window", {}).get("title"))[:80])

    r = await engine.maximize_window("UCC Test Beta")
    check("fallback: maximize_window() via EWMH", r.get("success") is True, str(r.get("error", ""))[:80])
    await asyncio.sleep(0.5)
    wins2 = await engine.list_windows()
    beta = next((w for w in wins2["windows"] if "Beta" in w.get("title", "")), {})
    check("fallback: maximize reflected in state", beta.get("state") == "maximized",
          f"state={beta.get('state')}")
    r = await engine.restore_window("UCC Test Beta")
    check("fallback: restore_window() via EWMH", r.get("success") is True)
    r = await engine.minimize_window("UCC Test Beta")
    check("fallback: minimize_window() via ICCCM", r.get("success") is True, str(r.get("error", ""))[:80])
    await asyncio.sleep(0.7)
    wins3 = await engine.list_windows()
    beta3 = next((w for w in wins3["windows"] if "Beta" in w.get("title", "")), {})
    check("fallback: minimize reflected in state", beta3.get("state") == "minimized",
          f"state={beta3.get('state')}")
    r = await engine.restore_window("UCC Test Beta")
    check("fallback: restore after minimize", r.get("success") is True)


def main() -> int:
    global MOCK_VLM_PORT
    TEST_HOME.mkdir(parents=True, exist_ok=True)
    if NO_TOOLS:
        run = run_xlib_fallback_suite
    else:
        run = run_full_suite
    server = start_mock_vlm()
    MOCK_VLM_PORT = server.server_address[1]
    start_display()
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("harness completed without crash", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.shutdown()
        stop_display()
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print(f"RESULTS: {len(RESULTS) - len(failed)} passed / {len(failed)} failed / {len(RESULTS)} total")
    for name, ok, details in failed:
        print(f"  FAIL: {name} -- {details}")
    return 1 if failed else 0




if __name__ == "__main__":
    sys.exit(main())

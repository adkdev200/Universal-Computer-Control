"""Linux window management via ``wmctrl`` / ``xdotool`` (X11).

Tool-based paths are preferred (battle-tested); when neither tool is
installed the backend falls back to a pure python-Xlib implementation that
speaks EWMH/ICCCM directly (list windows, active window, focus, minimize,
maximize, restore, close). This keeps window management working in minimal
environments (containers, stripped-down servers) where no X11 utilities are
present - only an X server connection is required. Wayland sessions:
``wmctrl``/``xdotool`` generally do not work; the backend probes the session
type and degrades gracefully (documented in the troubleshooting guide).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from typing import Any

from universal_computer.backends.base import Cap, WindowManagementBackend
from universal_computer.logging import get_logger
from universal_computer.models.element import BoundingBox
from universal_computer.models.window import WindowInfo, WindowState

logger = get_logger("backends.linux_window")

_WMCTRL_LINE = re.compile(r"^(?P<id>0x[0-9a-fA-F]+)\s+(?P<desk>-?\d+)\s+(?P<pid>\d+)\s+\S+\s*(?P<title>.*)$")
_GEO_RE = re.compile(r"window\s+0x[0-9a-fA-F]+\s+geometry\s+(?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+)")

# ICCCM / EWMH constants (mirrored to avoid importing Xlib at module load).
_ICONIC_STATE = 3
_NORMAL_STATE = 1
_SOURCE_APPLICATION = 1


class LinuxWindowBackend(WindowManagementBackend):
    """X11 window management via wmctrl/xdotool/python-Xlib (priority 70)."""

    name = "linux-window"
    platform = "linux"
    priority = 70

    def __init__(self) -> None:
        super().__init__()
        self._wmctrl = shutil.which("wmctrl")
        self._xdotool = shutil.which("xdotool")
        self._xlib_failed = False
        # python-Xlib connections are thread-affine in practice: a Display
        # object shared across worker threads can desync its request/reply
        # stream (observed: GetProperty replies parsed as empty arrays).
        # The engine invokes backends via asyncio.to_thread, so each thread
        # gets its own connection.
        self._tls = threading.local()

    # -- availability ------------------------------------------------------------
    def _probe(self) -> bool:
        import sys  # noqa: PLC0415

        if sys.platform != "linux":
            self._probe_error = "not Linux"
            return False
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            self._probe_error = (
                "Wayland session without XWayland DISPLAY; wmctrl/xdotool cannot manage windows"
            )
            return False
        if not self._wmctrl and not self._xdotool:
            logger.info(
                "wmctrl/xdotool not found; using python-Xlib fallback for window management "
                "(install them for the most robust behavior: 'sudo apt install wmctrl xdotool')"
            )
        try:
            windows = self.list_windows()
            # A successful X round-trip is enough; an empty list is valid too.
            logger.debug("linux-window probe found %d windows", len(windows))
        except Exception as exc:  # noqa: BLE001
            self._probe_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def capabilities(self) -> set[str]:
        return {Cap.WINDOWS}

    # -- subprocess helpers --------------------------------------------------------
    def _run(self, args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    # -- python-Xlib fallback layer --------------------------------------------------
    def _xlib(self) -> Any | None:
        """Connect to the X server with python-Xlib, one connection per thread.

        The engine invokes backends from worker threads (``asyncio.to_thread``);
        sharing one python-Xlib Display object across threads desyncs its
        protocol stream even with ``Xlib.threaded`` loaded, so connections are
        cached in thread-local storage instead.
        """
        display = getattr(self._tls, "display", None)
        if display is not None:
            return display
        try:
            import Xlib.threaded  # noqa: F401,PLC0415 - protocol-wide lock
            from Xlib.display import Display  # noqa: PLC0415

            display = Display()
        except Exception as exc:  # noqa: BLE001
            logger.debug("python-Xlib connection failed: %s", exc)
            self._xlib_failed = True
            self._probe_error = f"python-Xlib connection failed: {exc}"
            return None
        self._tls.display = display
        return display

    def _xlib_atom(self, disp: Any, name: str) -> int:
        return disp.intern_atom(name)

    def _xlib_root(self, disp: Any) -> Any:
        return disp.screen().root

    def _xlib_send(self, disp: Any, event: Any, target: Any, to_root: bool) -> bool:
        from Xlib import X  # noqa: PLC0415

        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        target_window = self._xlib_root(disp) if to_root else target
        try:
            target_window.send_event(event, event_mask=mask)
            disp.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Xlib send_event failed: %s", exc)
            return False

    def _client_message(self, disp: Any, window_id: int, type_name: str, values: list[int]) -> Any:
        from Xlib import protocol  # noqa: PLC0415

        return protocol.event.ClientMessage(
            window=window_id,
            client_type=self._xlib_atom(disp, type_name),
            data=(32, values + [0] * (5 - len(values))),
        )

    def _xlib_top_levels(self) -> list[dict]:
        """Enumerate top-level client windows via python-Xlib (no WM required).

        Strategy (in order):
        1. ``_NET_CLIENT_LIST`` root property - the EWMH client list every
           window manager maintains (matches ``wmctrl -l`` exactly).
        2. Direct scan of root children when no WM is running (a real client
           is a root child carrying the ICCCM ``WM_STATE`` property).

        Override-redirect popups (menus, tooltips) are always skipped.
        """
        disp = self._xlib()
        if disp is None:
            raise RuntimeError("python-Xlib cannot connect to the X server")

        root = self._xlib_root(disp)
        client_ids: list[int] = []

        client_list = root.get_full_property(self._xlib_atom(disp, "_NET_CLIENT_LIST"), 0)
        if client_list is not None and client_list.value:
            client_ids = [int(v) for v in client_list.value]
        else:
            # No window manager (or a WM that ignores EWMH): scan the root.

            for child in root.query_tree().children:
                try:
                    if child.attributes().override_redirect:
                        continue
                    wm_state = child.get_full_property(self._xlib_atom(disp, "WM_STATE"), 0)
                    if wm_state is not None:
                        client_ids.append(child.id)
                except Exception:  # noqa: BLE001 - a broken window must not kill enumeration
                    continue

        clients: list[dict] = []
        for window_id in client_ids:
            try:
                info = self._xlib_read_window(disp, root, window_id)
            except Exception:  # noqa: BLE001
                info = None
            if info is not None:
                clients.append(info)
        return clients

    def _xlib_read_window(self, disp: Any, root: Any, window_id: int) -> dict | None:

        win = disp.create_resource_object("window", window_id)
        attrs = win.get_attributes()
        if attrs.override_redirect:
            return None
        wm_state = win.get_full_property(self._xlib_atom(disp, "WM_STATE"), 0)
        # Title: _NET_WM_NAME (UTF-8) preferred, WM_NAME fallback.
        title = ""
        net_name = win.get_full_property(self._xlib_atom(disp, "_NET_WM_NAME"), 0)
        if net_name and net_name.value:
            raw = net_name.value
            title = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        else:
            name_reply = win.get_wm_name()
            if isinstance(name_reply, bytes):
                title = name_reply.decode("utf-8", "replace")
            elif name_reply:
                title = str(name_reply)
        pid_prop = win.get_full_property(self._xlib_atom(disp, "_NET_WM_PID"), 0)
        pid = int(pid_prop.value[0]) if pid_prop and pid_prop.value else None
        net_state = win.get_full_property(self._xlib_atom(disp, "_NET_WM_STATE"), 0)
        state_atoms: set[int] = set()
        if net_state and net_state.value:
            state_atoms = {int(v) for v in net_state.value}
        geo = win.get_geometry()
        # python-xlib quirk: win.translate_coords(root, ...) maps root->window
        # (negated position); root.translate_coords(win, ...) gives the
        # window's top-left in root space, which is what we want.
        translated = root.translate_coords(win, 0, 0)
        return {
            "id": win.id,
            "title": title,
            "pid": pid,
            "x": translated.x,
            "y": translated.y,
            "w": geo.width,
            "h": geo.height,
            "iconic": wm_state is not None
            and bool(wm_state.value)
            and int(wm_state.value[0]) == _ICONIC_STATE,
            "maximized": bool(state_atoms)
            and {
                self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_VERT"),
                self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_HORZ"),
            } <= state_atoms,
            "win": win,
        }

    def _xlib_active_id(self) -> int | None:
        disp = self._xlib()
        if disp is None:
            return None
        prop = self._xlib_root(disp).get_full_property(self._xlib_atom(disp, "_NET_ACTIVE_WINDOW"), 0)
        if prop and prop.value and int(prop.value[0]) != 0:
            return int(prop.value[0])
        return None

    def _list_windows_xlib(self) -> list[WindowInfo]:
        active_id = self._xlib_active_id()
        windows: list[WindowInfo] = []
        for info in self._xlib_top_levels():
            pid = info["pid"]
            app = None
            if pid:
                try:
                    app = open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace").read().strip() or None
                except OSError:
                    app = None
            state = WindowState.NORMAL
            if info["iconic"]:
                state = WindowState.MINIMIZED
            elif info["maximized"]:
                state = WindowState.MAXIMIZED
            windows.append(
                WindowInfo(
                    title=info["title"],
                    application=app,
                    process_id=pid,
                    handle=f"0x{info['id']:x}",
                    bbox=BoundingBox.from_xywh(info["x"], info["y"], info["w"], info["h"]),
                    state=state,
                    is_active=(info["id"] == active_id),
                )
            )
        return windows

    # -- helpers -----------------------------------------------------------------
    def _geometry(self, hex_id: str) -> BoundingBox | None:
        if not self._xdotool:
            return None
        try:
            result = self._run([self._xdotool, "getwindowgeometry", "--shell", hex_id])
            values: dict[str, int] = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    try:
                        values[key.strip()] = int(value.strip())
                    except ValueError:
                        continue
            if {"X", "Y", "WIDTH", "HEIGHT"} <= values.keys():
                return BoundingBox.from_xywh(values["X"], values["Y"], values["WIDTH"], values["HEIGHT"])
            match = _GEO_RE.search(result.stdout)
            if match:
                return BoundingBox.from_ltrb(
                    int(match.group("x")),
                    int(match.group("y")),
                    int(match.group("x")) + int(match.group("w")),
                    int(match.group("y")) + int(match.group("h")),
                )
        except Exception:  # noqa: BLE001
            pass
        return None

    def _active_hex(self) -> str | None:
        if self._xdotool:
            try:
                result = self._run([self._xdotool, "getactivewindow"])
                if result.returncode == 0 and result.stdout.strip():
                    return f"0x{int(result.stdout.strip()):x}"
            except Exception:  # noqa: BLE001
                pass
        if self._wmctrl:
            try:
                result = self._run([self._wmctrl, "-l"])
                for line in result.stdout.splitlines():
                    if " _NET_ACTIVE_WINDOW" in line or ":*" in line:
                        match = _WMCTRL_LINE.match(line)
                        if match:
                            return match.group("id")
            except Exception:  # noqa: BLE001
                pass
        active_id = self._xlib_active_id()
        return f"0x{active_id:x}" if active_id else None

    def _window_info(self, hex_id: str, title: str, pid: int | None, bbox: BoundingBox | None = None) -> WindowInfo:
        return WindowInfo(
            title=title,
            application=None,
            process_id=pid,
            handle=hex_id,
            bbox=bbox if bbox is not None else self._geometry(hex_id),
            state=WindowState.NORMAL,
            is_active=(hex_id == self._active_hex()),
        )

    # -- WindowManagementBackend API -------------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        """Enumerate windows via wmctrl, then python-Xlib.

        Every strategy falls through to the next on failure: tools may crash
        (e.g. broken locales/containers) or be missing entirely; the backend
        stays available as long as ONE mechanism works. An empty wmctrl
        answer is cross-checked against Xlib because flaky tool builds were
        observed to exit 0 with empty output right before crashing.
        """
        errors: list[str] = []
        wmctrl_windows: list[WindowInfo] | None = None
        if self._wmctrl:
            try:
                result = self._run([self._wmctrl, "-l", "-p"])
                if result.returncode == 0:
                    windows: list[WindowInfo] = []
                    for line in result.stdout.splitlines():
                        match = _WMCTRL_LINE.match(line)
                        if not match:
                            continue
                        pid = int(match.group("pid")) if match.group("pid").isdigit() else None
                        app = None
                        if pid:
                            try:
                                app = open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace").read().strip() or None
                            except OSError:
                                app = None
                        info = self._window_info(match.group("id"), match.group("title").strip(), pid)
                        info.application = app
                        windows.append(info)
                    wmctrl_windows = windows
                else:
                    errors.append(f"wmctrl rc={result.returncode}: {result.stderr.strip()[:100]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"wmctrl {type(exc).__name__}: {exc}")
        if wmctrl_windows:
            return wmctrl_windows
        try:
            xlib_windows = self._list_windows_xlib()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"xlib {type(exc).__name__}: {exc}")
            xlib_windows = None
        if xlib_windows is not None:
            # Xlib reading zero clients on a live desktop is as trustworthy
            # as wmctrl's own empty answer; prefer whichever saw windows.
            if xlib_windows:
                return xlib_windows
            if wmctrl_windows is not None:
                return wmctrl_windows  # both agree: genuinely no windows
            return xlib_windows
        raise RuntimeError("window listing failed for all strategies: " + "; ".join(errors))

    def _list_windows_xdotool(self) -> list[WindowInfo]:
        """Enumerate visible windows with xdotool search + per-window queries."""
        assert self._xdotool
        found = self._run([self._xdotool, "search", "--onlyvisible", "--name", ""])
        if found.returncode != 0 or not found.stdout.strip():
            return []
        windows: list[WindowInfo] = []
        for line in found.stdout.splitlines():
            if not line.strip().isdigit():
                continue
            hex_id = f"0x{int(line.strip()):x}"
            name = self._run([self._xdotool, "getwindowname", hex_id])
            pid_out = self._run([self._xdotool, "getwindowpid", hex_id])
            pid = int(pid_out.stdout.strip()) if pid_out.returncode == 0 and pid_out.stdout.strip().isdigit() else None
            windows.append(self._window_info(hex_id, name.stdout.strip() if name.returncode == 0 else "", pid))
        return windows

    def get_active_window(self) -> WindowInfo | None:
        active_hex = self._active_hex()
        if not active_hex:
            return None
        for window in self.list_windows():
            if window.handle == active_hex:
                return window
        return None

    def _find_window(self, window: WindowInfo) -> WindowInfo | None:
        if window.handle:
            for candidate in self.list_windows():
                if candidate.handle == window.handle:
                    return candidate
        for candidate in self.list_windows():
            if candidate.title == window.title:
                return candidate
        return None

    # -- xlib EWMH/ICCCM operations (last-resort when tools are absent) -------------
    def _xlib_window_id(self, window: WindowInfo) -> int | None:
        if window.handle and window.handle.startswith("0x"):
            try:
                return int(window.handle, 16)
            except ValueError:
                return None
        return None

    def _focus_xlib(self, window: WindowInfo) -> bool:
        disp = self._xlib()
        window_id = self._xlib_window_id(window)
        if disp is None or window_id is None:
            return False
        event = self._client_message(disp, window_id, "_NET_ACTIVE_WINDOW", [_SOURCE_APPLICATION, 0, 0, 0, 0])
        return self._xlib_send(disp, event, disp.create_resource_object("window", window_id), to_root=True)

    def _minimize_xlib(self, window: WindowInfo) -> bool:
        disp = self._xlib()
        window_id = self._xlib_window_id(window)
        if disp is None or window_id is None:
            return False
        target = disp.create_resource_object("window", window_id)
        # A maximized window must be un-maximized first: some window managers
        # ignore WM_CHANGE_STATE/IconicState while maximized atoms are set.
        net_state = target.get_full_property(self._xlib_atom(disp, "_NET_WM_STATE"), 0)
        state_atoms = {int(v) for v in net_state.value} if net_state and net_state.value else set()
        maximized_atoms = {
            self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_VERT"),
            self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_HORZ"),
        }
        if maximized_atoms & state_atoms:
            unmax = self._client_message(disp, window_id, "_NET_WM_STATE", [0, *maximized_atoms])
            self._xlib_send(disp, unmax, target, to_root=True)
            # Give the window manager a beat to process the state change;
            # an immediate WM_CHANGE_STATE is otherwise swallowed.
            import time  # noqa: PLC0415

            time.sleep(0.15)
        # ICCCM: WM_CHANGE_STATE -> IconicState. Delivered to the ROOT window
        # (some window managers, e.g. openbox, ignore it when sent directly
        # to the client window).
        event = self._client_message(disp, window_id, "WM_CHANGE_STATE", [_ICONIC_STATE])
        return self._xlib_send(disp, event, target, to_root=True)

    def _restore_xlib(self, window: WindowInfo) -> bool:
        disp = self._xlib()
        window_id = self._xlib_window_id(window)
        if disp is None or window_id is None:
            return False
        target = disp.create_resource_object("window", window_id)
        # EWMH activation un-iconifies AND focuses on every major window
        # manager (verified on openbox; mutter/kwin/xfwm follow the spec).
        # Sending WM_CHANGE_STATE first was observed to swallow the activate
        # request on some WMs, so activation alone is the safe sequence.
        event = self._client_message(disp, window_id, "_NET_ACTIVE_WINDOW", [_SOURCE_APPLICATION, 0, 0, 0, 0])
        return self._xlib_send(disp, event, target, to_root=True)

    def _maximize_xlib(self, window: WindowInfo) -> bool:
        disp = self._xlib()
        window_id = self._xlib_window_id(window)
        if disp is None or window_id is None:
            return False
        vert = self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_VERT")
        horz = self._xlib_atom(disp, "_NET_WM_STATE_MAXIMIZED_HORZ")
        event = self._client_message(disp, window_id, "_NET_WM_STATE", [_SOURCE_APPLICATION, vert, horz, 0])
        return self._xlib_send(disp, event, disp.create_resource_object("window", window_id), to_root=True)

    def _close_xlib(self, window: WindowInfo) -> bool:
        disp = self._xlib()
        window_id = self._xlib_window_id(window)
        if disp is None or window_id is None:
            return False
        event = self._client_message(disp, window_id, "_NET_CLOSE_WINDOW", [0, 0, 0, 0, 0])
        return self._xlib_send(disp, event, disp.create_resource_object("window", window_id), to_root=True)

    # -- operations (tool first, Xlib fallback) ---------------------------------------
    def focus_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._wmctrl:
            result = self._run([self._wmctrl, "-i", "-a", target.handle])
            if result.returncode == 0:
                return True
        if self._xdotool:
            result = self._run([self._xdotool, "windowactivate", target.handle])
            if result.returncode == 0:
                return True
        return self._focus_xlib(target)

    def minimize_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._xdotool:
            result = self._run([self._xdotool, "windowminimize", target.handle])
            if result.returncode == 0:
                return True
        return self._minimize_xlib(target)

    def maximize_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._wmctrl:
            result = self._run(
                [self._wmctrl, "-i", "-r", target.handle, "-b", "add,maximized_vert,maximized_horz"]
            )
            if result.returncode == 0:
                return True
        return self._maximize_xlib(target)

    def restore_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._wmctrl:
            result = self._run(
                [self._wmctrl, "-i", "-r", target.handle, "-b", "remove,maximized_vert,maximized_horz"]
            )
            if result.returncode == 0:
                return True
        return self._restore_xlib(target)

    def close_window(self, window: WindowInfo) -> bool:
        target = self._find_window(window)
        if target is None:
            return False
        if self._wmctrl:
            result = self._run([self._wmctrl, "-i", "-c", target.handle])
            if result.returncode == 0:
                return True
            # -ic sends a graceful WM_CLOSE; modal confirmations stay visible.
        return self._close_xlib(target)

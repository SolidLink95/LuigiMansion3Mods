"""Record keyboard and mouse input history to a JSON file for later replay.

Usage:
    python input_recorder.py [output.json] [--no-moves] [--print-moves] [--quiet]

Press F9 to stop recording (the stop key itself is not saved).
The JSON lands next to this script by default. Every event stores its type,
absolute time since the first event, and the interval since the previous one,
so input_replay.py can reproduce the session with original timing.

Keyboard events come from the `keyboard` package; mouse events come from a
low-level WH_MOUSE_LL hook driven by ctypes, so no extra dependency is needed.
Both use time.time(), which keeps the two streams on one clock.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import queue
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import keyboard

from input_events import EXTENDED_KEYS, describe, summarize

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "input_history.json"
STOP_KEY = "f9"

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
LLMHF_INJECTED = 0x0001

# wParam values delivered to the low-level mouse hook.
MOUSE_MESSAGES = {
    0x0200: ("move", None, None),
    0x0201: ("button", "left", "down"),
    0x0202: ("button", "left", "up"),
    0x0204: ("button", "right", "down"),
    0x0205: ("button", "right", "up"),
    0x0207: ("button", "middle", "down"),
    0x0208: ("button", "middle", "up"),
    0x020B: ("xbutton", None, "down"),
    0x020C: ("xbutton", None, "up"),
    0x020A: ("wheel", "vertical", None),
    0x020E: ("wheel", "horizontal", None),
}

# SM_* indexes for the bounding box of all monitors.
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

ULONG_PTR = wintypes.WPARAM
LRESULT = ctypes.c_ssize_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
# Without an explicit restype the HMODULE is truncated to 32 bits and
# SetWindowsHookExW rejects it with "module could not be found".
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)


def virtual_screen() -> dict:
    return {
        "left": user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        "top": user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        "width": user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        "height": user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    }


def high_word_signed(value: int) -> int:
    """Return mouseData's HIWORD as a signed 16-bit number (wheel delta)."""
    word = (value >> 16) & 0xFFFF
    return word - 0x10000 if word & 0x8000 else word


class Recorder:
    def __init__(self, record_moves: bool, move_interval: float, echo: bool, echo_moves: bool) -> None:
        self.events: list[dict] = []
        self.start: float | None = None
        self.record_moves = record_moves
        self.move_interval = move_interval
        self.last_move = 0.0
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.main_thread = kernel32.GetCurrentThreadId()
        # Live output is handed to a printer thread: a slow hook callback can
        # exceed LowLevelHooksTimeout and get the hook silently removed.
        self.echo = echo
        self.echo_moves = echo_moves
        self.outbox: queue.Queue[str | None] = queue.Queue()
        self.printer = threading.Thread(target=self._drain, daemon=True)
        self.printer.start()

    def _drain(self) -> None:
        while True:
            line = self.outbox.get()
            if line is None:
                return
            print(line, flush=True)

    def add(self, moment: float, payload: dict) -> None:
        with self.lock:
            if self.done.is_set():
                return
            if self.start is None:
                self.start = moment
            payload["t"] = round(moment - self.start, 6)
            self.events.append(payload)
            if self.echo and (self.echo_moves or payload.get("action") != "move"):
                self.outbox.put(describe(payload))

    def stop(self) -> None:
        self.done.set()
        # Break the main thread out of GetMessageW so the pump can exit.
        user32.PostThreadMessageW(self.main_thread, WM_QUIT, 0, 0)

    def on_key(self, event: keyboard.KeyboardEvent) -> None:
        name = (event.name or "").lower()
        if name == STOP_KEY:
            if event.event_type == "down":
                self.stop()
            return
        self.add(event.time, {
            "type": "key",
            "key": name,
            "scan_code": event.scan_code,
            "extended": name in EXTENDED_KEYS and not event.is_keypad,
            "event": event.event_type,  # "down" | "up"
        })

    def on_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            self.handle_mouse(w_param, info)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def handle_mouse(self, message: int, info: MSLLHOOKSTRUCT) -> None:
        entry = MOUSE_MESSAGES.get(message)
        if entry is None:
            return
        kind, button, action = entry
        moment = time.time()
        if kind == "move":
            if not self.record_moves or moment - self.last_move < self.move_interval:
                return
            self.last_move = moment
        payload = {
            "type": "mouse",
            "action": "button" if kind == "xbutton" else kind,
            "x": info.pt.x,
            "y": info.pt.y,
            "injected": bool(info.flags & LLMHF_INJECTED),
        }
        if kind == "button":
            payload.update(button=button, event=action)
        elif kind == "xbutton":
            payload.update(button=f"x{(info.mouseData >> 16) & 0xFFFF}", event=action)
        elif kind == "wheel":
            payload.update(axis=button, delta=high_word_signed(info.mouseData))
        self.add(moment, payload)

    def close(self) -> None:
        self.outbox.put(None)
        self.printer.join(timeout=1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?", default=DEFAULT_OUTPUT)
    parser.add_argument("--no-moves", action="store_true", help="record clicks and wheel but not cursor movement")
    parser.add_argument(
        "--move-interval", type=float, default=0.016,
        help="minimum seconds between recorded move samples (default: 0.016, about 60 Hz)",
    )
    parser.add_argument("--print-moves", action="store_true", help="also echo cursor movement, not just presses")
    parser.add_argument("--quiet", action="store_true", help="do not echo events while recording")
    args = parser.parse_args()

    recorder = Recorder(
        record_moves=not args.no_moves,
        move_interval=args.move_interval,
        echo=not args.quiet,
        echo_moves=args.print_moves,
    )
    proc = HOOKPROC(recorder.on_mouse)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        raise ctypes.WinError(ctypes.get_last_error())
    keyboard_hook = keyboard.hook(recorder.on_key)

    moves = "off" if args.no_moves else f"every {args.move_interval * 1000:.0f} ms"
    print(f"recording keyboard + mouse (moves: {moves})... press {STOP_KEY.upper()} to stop", flush=True)
    message = wintypes.MSG()
    try:
        # GetMessageW returns 0 on WM_QUIT, which recorder.stop() posts.
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    except KeyboardInterrupt:
        recorder.done.set()
    finally:
        keyboard.unhook(keyboard_hook)
        user32.UnhookWindowsHookEx(hook)
        recorder.close()

    events = recorder.events
    for index, event in enumerate(events):
        event["dt"] = round(event["t"] - events[index - 1]["t"], 6) if index else 0.0

    payload = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "stop_key": STOP_KEY,
        "duration": events[-1]["t"] if events else 0.0,
        "event_count": len(events),
        "virtual_screen": virtual_screen(),
        "events": events,
    }
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved {len(events)} events ({payload['duration']:.3f}s) to {args.output}")
    print(f"  {summarize(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

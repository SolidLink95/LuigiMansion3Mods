"""Replay keyboard and mouse input recorded by input_recorder.py.

Usage:
    python input_replay.py [history.json] [--speed 1.0] [--delay 3] [--repeat 1] [--quiet]

Injects hardware-level scan-code and absolute-position events through
SendInput, which SDL-based applications such as yuzu accept (synthetic window
messages are ignored). Focus the target window during the countdown. Hold ESC
to abort mid-replay; any keys or buttons still held are released before exit.

No third-party dependencies: ctypes plus the sibling input_events module.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path

from input_events import describe, summarize

DEFAULT = Path(__file__).resolve().parent / "input_history.json"

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

BUTTON_FLAGS = {
    ("left", "down"): 0x0002, ("left", "up"): 0x0004,
    ("right", "down"): 0x0008, ("right", "up"): 0x0010,
    ("middle", "down"): 0x0020, ("middle", "up"): 0x0040,
    ("x1", "down"): 0x0080, ("x1", "up"): 0x0100,
    ("x2", "down"): 0x0080, ("x2", "up"): 0x0100,
}

VK_ESCAPE = 0x1B
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _INPUT_UNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (("type", wintypes.DWORD), ("union", _INPUT_UNION))


user32 = ctypes.WinDLL("user32", use_last_error=True)


def virtual_screen() -> dict:
    return {
        "left": user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        "top": user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        "width": user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        "height": user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    }


def send(record: INPUT) -> None:
    if user32.SendInput(1, ctypes.byref(record), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def send_scan(scan_code: int, key_up: bool, extended: bool) -> None:
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    record = INPUT(type=INPUT_KEYBOARD)
    record.ki = KEYBDINPUT(0, scan_code, flags, 0, 0)
    send(record)


def normalize(x: int, y: int, screen: dict) -> tuple[int, int]:
    """Map screen coordinates onto SendInput's 0..65535 virtual-desktop grid."""
    width = max(screen["width"] - 1, 1)
    height = max(screen["height"] - 1, 1)
    dx = round((x - screen["left"]) * 65535 / width)
    dy = round((y - screen["top"]) * 65535 / height)
    return max(0, min(65535, dx)), max(0, min(65535, dy))


def send_mouse(event: dict, screen: dict, button_flag: int = 0, data: int = 0) -> None:
    dx, dy = normalize(event["x"], event["y"], screen)
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | button_flag
    record = INPUT(type=INPUT_MOUSE)
    record.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    send(record)


def abort_requested() -> bool:
    return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)


def dispatch(event: dict, screen: dict, held_keys: dict, held_buttons: dict) -> None:
    if event.get("type", "key") == "key":
        identity = (event["scan_code"], event["extended"])
        key_up = event["event"] == "up"
        send_scan(event["scan_code"], key_up, event["extended"])
        held_keys.pop(identity, None) if key_up else held_keys.update({identity: event["key"]})
        return

    action = event.get("action")
    if action == "move":
        send_mouse(event, screen)
    elif action == "button":
        button = event["button"]
        flag = BUTTON_FLAGS[(button, event["event"])]
        # XBUTTON1/2 share one flag pair and are told apart by mouseData.
        data = int(button[1]) if button.startswith("x") else 0
        send_mouse(event, screen, flag, data)
        held_buttons.pop(button, None) if event["event"] == "up" else held_buttons.update({button: event})
    elif action == "wheel":
        axis = MOUSEEVENTF_HWHEEL if event.get("axis") == "horizontal" else MOUSEEVENTF_WHEEL
        send_mouse(event, screen, axis, event["delta"])


def replay(events: list[dict], speed: float, screen: dict, echo: bool) -> bool:
    """Replay events with original timing scaled by speed. False on abort."""
    held_keys: dict[tuple[int, bool], str] = {}
    held_buttons: dict[str, dict] = {}
    origin = time.perf_counter()
    try:
        for event in events:
            target = origin + event["t"] / speed
            while True:
                remaining = target - time.perf_counter()
                if remaining <= 0:
                    break
                if abort_requested():
                    return False
                time.sleep(min(0.005, remaining))
            dispatch(event, screen, held_keys, held_buttons)
            if echo:
                print(describe(event), flush=True)
        return True
    finally:
        for scan_code, extended in held_keys:
            send_scan(scan_code, True, extended)
        for button, event in held_buttons.items():
            send_mouse(event, screen, BUTTON_FLAGS[(button, "up")], int(button[1]) if button.startswith("x") else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path, nargs="?", default=DEFAULT)
    parser.add_argument("--speed", type=float, default=1.0, help="timing multiplier, 2.0 = twice as fast")
    parser.add_argument("--delay", type=float, default=3.0, help="countdown seconds before replay starts")
    parser.add_argument("--repeat", type=int, default=1, help="number of times to replay the history")
    parser.add_argument("--quiet", action="store_true", help="do not echo events while replaying")
    args = parser.parse_args()

    payload = json.loads(args.history.read_text(encoding="utf-8"))
    events = payload["events"]
    if not events:
        print("history contains no events")
        return 1

    screen = virtual_screen()
    recorded = payload.get("virtual_screen")
    if recorded and recorded != screen:
        print(f"warning: recorded on {recorded['width']}x{recorded['height']}, replaying on "
              f"{screen['width']}x{screen['height']}; click positions may not line up")

    print(f"{len(events)} events, {payload.get('duration', events[-1]['t']):.3f}s recorded")
    print(f"  {summarize(events)}")

    for remaining in range(int(args.delay), 0, -1):
        print(f"starting in {remaining}... focus the target window (hold ESC to abort)")
        time.sleep(1)

    for iteration in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"replay {iteration}/{args.repeat}")
        if not replay(events, args.speed, screen, echo=not args.quiet):
            print("aborted; held keys and buttons released")
            return 1
    print("replay complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

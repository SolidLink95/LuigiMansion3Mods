"""Offline checks for the recorder's decode path and the replay encode path.

No real input is injected and no hook is installed, so this is safe to run
while the machine is in use. Verifies that every mouse message the hook can
deliver decodes to the documented JSON shape, and that each JSON shape encodes
back to the correct SendInput flags.

    python test_input_roundtrip.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import input_replay
from input_events import describe
from input_recorder import MSLLHOOKSTRUCT, Recorder
from input_replay import (
    BUTTON_FLAGS, INPUT_KEYBOARD, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_HWHEEL, MOUSEEVENTF_MOVE, MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL, dispatch, normalize,
)

SCREEN = {"left": 0, "top": 0, "width": 5120, "height": 1440}

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def decode(message: int, mouse_data: int = 0, x: int = 100, y: int = 200) -> dict:
    """Run one synthetic hook message through the recorder's decoder."""
    recorder = Recorder(record_moves=True, move_interval=0.0, echo=False, echo_moves=False)
    info = MSLLHOOKSTRUCT()
    info.pt.x, info.pt.y = x, y
    info.mouseData = mouse_data
    info.flags = 0
    recorder.handle_mouse(message, info)
    recorder.close()
    return recorder.events[0]


def test_decode() -> None:
    cases = [
        (0x0201, 0, "button", {"button": "left", "event": "down"}),
        (0x0202, 0, "button", {"button": "left", "event": "up"}),
        (0x0204, 0, "button", {"button": "right", "event": "down"}),
        (0x0205, 0, "button", {"button": "right", "event": "up"}),
        (0x0207, 0, "button", {"button": "middle", "event": "down"}),
        (0x0208, 0, "button", {"button": "middle", "event": "up"}),
        (0x020B, 1 << 16, "button", {"button": "x1", "event": "down"}),
        (0x020C, 2 << 16, "button", {"button": "x2", "event": "up"}),
        (0x020A, 120 << 16, "wheel", {"axis": "vertical", "delta": 120}),
        (0x020E, 120 << 16, "wheel", {"axis": "horizontal", "delta": 120}),
        (0x0200, 0, "move", {}),
    ]
    for message, data, action, fields in cases:
        event = decode(message, data)
        check(f"decode {message:#06x} action", event["action"], action)
        check(f"decode {message:#06x} x", event["x"], 100)
        check(f"decode {message:#06x} y", event["y"], 200)
        for key, value in fields.items():
            check(f"decode {message:#06x} {key}", event.get(key), value)

    # A negative wheel delta arrives as an unsigned HIWORD and must come back
    # signed, otherwise scroll-down replays as a huge scroll-up.
    check("decode wheel down delta", decode(0x020A, 0xFF88 << 16)["delta"], -120)


def capture(event: dict) -> ctypes.Structure:
    """Dispatch one event with SendInput stubbed out, returning the INPUT."""
    sent: list = []
    original = input_replay.send
    input_replay.send = sent.append
    try:
        dispatch(event, SCREEN, {}, {})
    finally:
        input_replay.send = original
    check("one input record emitted", len(sent), 1)
    return sent[0]


def test_encode() -> None:
    base = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

    record = capture({"type": "key", "key": "a", "scan_code": 0x1E, "extended": False, "event": "down", "t": 0})
    check("key type", record.type, INPUT_KEYBOARD)
    check("key scan", record.ki.wScan, 0x1E)
    check("key down flags", record.ki.dwFlags, 0x0008)

    record = capture({"type": "key", "key": "up", "scan_code": 0x48, "extended": True, "event": "up", "t": 0})
    check("extended key up flags", record.ki.dwFlags, 0x0008 | 0x0002 | 0x0001)

    for button in ("left", "right", "middle", "x1", "x2"):
        for action in ("down", "up"):
            event = {"type": "mouse", "action": "button", "button": button,
                     "event": action, "x": 640, "y": 360, "t": 0}
            record = capture(event)
            check(f"{button} {action} type", record.type, INPUT_MOUSE)
            check(f"{button} {action} flags", record.mi.dwFlags, base | BUTTON_FLAGS[(button, action)])
            expected_data = int(button[1]) if button.startswith("x") else 0
            check(f"{button} {action} mouseData", record.mi.mouseData, expected_data)

    record = capture({"type": "mouse", "action": "wheel", "axis": "vertical", "delta": 120, "x": 1, "y": 1, "t": 0})
    check("wheel flags", record.mi.dwFlags, base | MOUSEEVENTF_WHEEL)
    check("wheel data", record.mi.mouseData, 120)

    record = capture({"type": "mouse", "action": "wheel", "axis": "horizontal", "delta": -120, "x": 1, "y": 1, "t": 0})
    check("hwheel flags", record.mi.dwFlags, base | MOUSEEVENTF_HWHEEL)
    check("hwheel negative data", ctypes.c_int32(record.mi.mouseData).value, -120)

    record = capture({"type": "mouse", "action": "move", "x": 5119, "y": 1439, "t": 0})
    check("move flags", record.mi.dwFlags, base)
    check("move dx at right edge", record.mi.dx, 65535)
    check("move dy at bottom edge", record.mi.dy, 65535)


def test_normalize() -> None:
    check("origin", normalize(0, 0, SCREEN), (0, 0))
    check("far corner", normalize(5119, 1439, SCREEN), (65535, 65535))
    check("clamped beyond edge", normalize(9999, 9999, SCREEN), (65535, 65535))
    offset = {"left": -1920, "top": 0, "width": 3840, "height": 1080}
    check("negative-origin left edge", normalize(-1920, 0, offset), (0, 0))


def test_describe() -> None:
    samples = [
        {"type": "key", "key": "a", "scan_code": 0x1E, "extended": False, "event": "down", "t": 0.5},
        {"type": "mouse", "action": "button", "button": "left", "event": "down", "x": 10, "y": 20, "t": 1.0},
        {"type": "mouse", "action": "wheel", "axis": "vertical", "delta": -120, "x": 10, "y": 20, "t": 1.5},
        {"type": "mouse", "action": "move", "x": 10, "y": 20, "t": 2.0},
    ]
    for event in samples:
        line = describe(event)
        if not line.strip():
            failures.append(f"describe produced no output for {event}")
        line.encode("ascii")  # must survive a legacy console code page


def main() -> int:
    for test in (test_decode, test_encode, test_normalize, test_describe):
        test()
    if failures:
        print(f"FAILED ({len(failures)})")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("all checks passed: decode, encode, normalize, describe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared event schema helpers for input_recorder.py and input_replay.py.

Kept ASCII-only so live output survives a legacy console code page.
"""

from __future__ import annotations

# Keys whose hardware scan code carries the 0xE0 extended prefix. Needed so
# replay can tell e.g. the up arrow (extended 0x48) from keypad 8 (0x48).
EXTENDED_KEYS = {
    "up", "down", "left", "right",
    "insert", "delete", "home", "end", "page up", "page down",
    "right ctrl", "right alt", "alt gr",
    "print screen", "num lock", "break",
    "windows", "left windows", "right windows", "menu",
}


def describe(event: dict) -> str:
    """Return a fixed-width, human-readable line for one recorded event."""
    stamp = f"[{event['t']:8.3f}s]"
    if event.get("type", "key") == "key":
        arrow = "v" if event["event"] == "down" else "^"
        suffix = " ext" if event.get("extended") else ""
        return f"{stamp} key   {arrow} {event['key']:<14} scan {event['scan_code']:#04x}{suffix}"

    action = event.get("action")
    position = f"@ ({event['x']:>5}, {event['y']:>5})"
    if action == "button":
        arrow = "v" if event["event"] == "down" else "^"
        return f"{stamp} mouse {arrow} {event['button']:<14} {position}"
    if action == "wheel":
        axis = "vert" if event.get("axis") == "vertical" else "horz"
        return f"{stamp} wheel   {axis:<14} {event['delta']:+5d} {position}"
    return f"{stamp} move    {'':<14} {position}"


def summarize(events: list[dict]) -> str:
    keys = sum(1 for e in events if e.get("type", "key") == "key" and e["event"] == "down")
    clicks = sum(1 for e in events if e.get("action") == "button" and e["event"] == "down")
    moves = sum(1 for e in events if e.get("action") == "move")
    wheels = sum(1 for e in events if e.get("action") == "wheel")
    return f"{keys} key presses, {clicks} clicks, {moves} moves, {wheels} wheel steps"
